from datetime import datetime
from pathlib import Path
import argparse
from contextlib import nullcontext
import csv
import io
import sys

import cv2
import imagehash
import numpy as np
import pandas as pd
from PIL import Image, ExifTags
import pyiqa
import rawpy
import requests
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table
import torch
import torch.nn as nn
from transformers import CLIPModel, CLIPProcessor

from evaluation_cache import CachedEvaluation, EvaluationCache, FileFingerprint
from file_ops import (
    ALL_IMAGE_EXTENSIONS,
    RAW_EXTENSIONS,
    build_export_plan,
    copy_export_plan,
    discover_image_files,
)
from scoring import (
    assign_session_focus_factors,
    calculate_composite_score,
    group_bursts,
)

console = Console()

CLIP_MODEL_ID = "openai/clip-vit-large-patch14"
MUSIQ_MODEL_ID = "musiq"
EVALUATION_CACHE_SIGNATURE = (
    "v1;clip=openai/clip-vit-large-patch14;musiq=musiq;max_dim=1024;"
    "focus=laplacian-16x16-top3pct;exposure=luma-v1"
)

def resolve_device() -> torch.device:
    """Selects NVIDIA CUDA on Windows, MPS on macOS, or falls back to CPU."""
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


class AestheticPredictor(nn.Module):
    """Linear regression head trained on CLIP embeddings (LAION Aesthetic)."""
    def __init__(self, input_dim: int = 768):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(input_dim, 1024),
            nn.Dropout(0.2),
            nn.Linear(1024, 128),
            nn.Dropout(0.2),
            nn.Linear(128, 64),
            nn.Dropout(0.1),
            nn.Linear(64, 16),
            nn.Linear(16, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.layers(x)


def load_aesthetic_head(device: torch.device) -> nn.Module:
    weights_path = Path("sac_vit_l_14_linear.pth")
    if not weights_path.exists():
        console.print("[yellow]Downloading aesthetic scoring weights...[/yellow]")
        url = "https://github.com/christophschuhmann/improved-aesthetic-predictor/raw/main/sac_public_2022_06_29_vit_l_14_linear.pth"
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        weights_path.write_bytes(resp.content)

    model = AestheticPredictor(input_dim=768)
    state = torch.load(weights_path, map_location=device, weights_only=True)
    model.load_state_dict(state)
    model.to(device)
    model.eval()
    return model


def get_image_timestamp(file_path: Path) -> datetime:
    """Extracts capture timestamp from EXIF, with rawpy fallback for RAW files."""
    ext = file_path.suffix.lower()
    if ext in RAW_EXTENSIONS:
        try:
            with rawpy.imread(str(file_path)) as raw:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    with Image.open(io.BytesIO(thumb.data)) as img:
                        exif = img._getexif()
                        if exif:
                            for tag_id, val in exif.items():
                                if ExifTags.TAGS.get(tag_id) in ("DateTimeOriginal", "DateTime"):
                                    return datetime.strptime(str(val), "%Y:%m:%d %H:%M:%S")
        except Exception:
            pass
    else:
        try:
            with Image.open(file_path) as img:
                exif = img._getexif()
                if exif:
                    for tag_id, val in exif.items():
                        if ExifTags.TAGS.get(tag_id) in ("DateTimeOriginal", "DateTime"):
                            return datetime.strptime(str(val), "%Y:%m:%d %H:%M:%S")
        except Exception:
            pass
    return datetime.fromtimestamp(file_path.stat().st_mtime)


def load_image_pair(file_path: Path, max_dim: int = 1024) -> tuple[np.ndarray, Image.Image]:
    """Loads RAW or JPEG files, using fast preview extraction where possible."""
    ext = file_path.suffix.lower()
    cv_img = None

    if ext in RAW_EXTENSIONS:
        with rawpy.imread(str(file_path)) as raw:
            try:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    cv_img = cv2.imdecode(np.frombuffer(thumb.data, np.uint8), cv2.IMREAD_COLOR)
                else:
                    cv_img = cv2.cvtColor(thumb.data, cv2.COLOR_RGB2BGR)
            except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
                rgb = raw.postprocess(half_size=True, use_camera_wb=True)
                cv_img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    else:
        cv_img = cv2.imread(str(file_path), cv2.IMREAD_COLOR)

    if cv_img is None:
        raise ValueError(f"Unable to read image at {file_path}")

    h, w = cv_img.shape[:2]
    if max(h, w) > max_dim:
        scale = max_dim / max(h, w)
        cv_resized = cv2.resize(cv_img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    else:
        cv_resized = cv_img

    pil_img = Image.fromarray(cv2.cvtColor(cv_resized, cv2.COLOR_BGR2RGB))
    return cv_resized, pil_img


def calculate_top_percentile_focus(cv_img: np.ndarray, grid_size: int = 16, top_k_pct: float = 0.03) -> float:
    """Calculates focus using the top 3 percent sharpest tiles in a 16x16 grid."""
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    tile_h, tile_w = h // grid_size, w // grid_size

    if tile_h < 8 or tile_w < 8:
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    scores = []
    for row in range(grid_size):
        for col in range(grid_size):
            patch = gray[row * tile_h : (row + 1) * tile_h, col * tile_w : (col + 1) * tile_w]
            scores.append(cv2.Laplacian(patch, cv2.CV_64F).var())

    scores.sort(reverse=True)
    count = max(1, int(len(scores) * top_k_pct))
    return float(np.mean(scores[:count]))


def check_exposure_clipping(cv_img: np.ndarray) -> tuple[float, float, float]:
    """Calculates blown highlight fraction, crushed shadow fraction, and penalty multiplier."""
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    blown_frac = float(np.mean(gray >= 254))
    crushed_frac = float(np.mean(gray <= 1))

    penalty = 1.0
    if blown_frac > 0.02:
        penalty *= max(0.4, 1.0 - (blown_frac - 0.02) * 5.0)
    if crushed_frac > 0.05:
        penalty *= max(0.6, 1.0 - (crushed_frac - 0.05) * 2.0)

    return blown_frac, crushed_frac, penalty


def record_from_cache(file_path: Path, cached: CachedEvaluation) -> dict:
    """Restore an in-memory ranking record from a safe SQLite representation."""
    embedding = np.frombuffer(cached.embedding_bytes, dtype="<f4")
    if embedding.size != cached.embedding_length:
        raise ValueError(f"Cached embedding has the wrong size for {file_path}")
    return {
        "file_name": file_path.name,
        "file_path": str(file_path.resolve()),
        "timestamp": datetime.fromisoformat(cached.timestamp_iso),
        "phash": imagehash.hex_to_hash(cached.phash_hex),
        "focus_score": cached.focus_score,
        "musiq_score": cached.musiq_score,
        "blown_pct": cached.blown_pct,
        "crushed_pct": cached.crushed_pct,
        "exposure_penalty": cached.exposure_penalty,
        "aesthetic_score": cached.aesthetic_score,
        "embedding": embedding.copy(),
        "cache_hit": True,
    }


def record_to_cache(record: dict) -> CachedEvaluation:
    """Convert a live evaluation record into a non-pickle cache value."""
    embedding = np.asarray(record["embedding"], dtype="<f4")
    return CachedEvaluation(
        timestamp_iso=record["timestamp"].isoformat(),
        phash_hex=str(record["phash"]),
        focus_score=float(record["focus_score"]),
        musiq_score=float(record["musiq_score"]),
        blown_pct=float(record["blown_pct"]),
        crushed_pct=float(record["crushed_pct"]),
        exposure_penalty=float(record["exposure_penalty"]),
        aesthetic_score=float(record["aesthetic_score"]),
        embedding_bytes=embedding.tobytes(),
        embedding_length=embedding.size,
    )


def run_pipeline(
    input_folder: str,
    output_folder: str | None,
    time_window: float,
    sim_threshold: float,
    no_group: bool,
    no_cache: bool,
    refresh_cache: bool,
) -> None:
    folder = Path(input_folder)
    if not folder.is_dir():
        console.print(f"[bold red]Error:[/bold red] '{input_folder}' is not a valid directory.")
        sys.exit(1)

    folder = folder.resolve()
    output_root = (
        Path(output_folder).expanduser().resolve()
        if output_folder is not None
        else folder / ".photo-cull"
    )
    if output_root == folder:
        console.print("[bold red]Error:[/bold red] The output directory cannot be the input directory itself.")
        sys.exit(1)

    device = resolve_device()
    console.print(
        Panel.fit(
            f"[bold green]Compute Target:[/bold green] [bold cyan]{device.type.upper()}[/bold cyan]\n"
            f"[bold green]Scanning Folder:[/bold green] {folder.resolve()}\n"
            f"[bold green]Output Root:[/bold green] {output_root}\n"
            f"[bold green]Burst Grouping:[/bold green] {'Disabled' if no_group else 'Enabled'}\n"
            f"[bold green]Metrics:[/bold green] Laplacian Patch Focus, MUSIQ Technical IQA, LAION Aesthetic, Exposure Clipping",
            title="Image Culling Engine",
        )
    )

    all_files = discover_image_files(folder, output_root)
    if not all_files:
        console.print("[yellow]No supported RAW or JPEG images found.[/yellow]")
        sys.exit(0)

    records = []
    pending_files: list[tuple[Path, FileFingerprint]] = []
    output_root.mkdir(parents=True, exist_ok=True)
    cache_context = (
        nullcontext(None)
        if no_cache
        else EvaluationCache(output_root / "evaluation_cache.sqlite3")
    )

    with cache_context as evaluation_cache:
        for file_path in all_files:
            try:
                fingerprint = FileFingerprint.from_path(file_path)
                cached = (
                    None
                    if evaluation_cache is None or refresh_cache
                    else evaluation_cache.get(
                        file_path,
                        fingerprint,
                        EVALUATION_CACHE_SIGNATURE,
                    )
                )
                if cached is None:
                    pending_files.append((file_path, fingerprint))
                else:
                    records.append(record_from_cache(file_path, cached))
            except Exception as err:
                console.log(f"[yellow]Could not inspect {file_path}:[/yellow] {err}")

        if records:
            console.print(
                f"[green]Reused {len(records)} cached evaluation"
                f"{'s' if len(records) != 1 else ''}.[/green]"
            )

        if pending_files:
            clip_model = CLIPModel.from_pretrained(CLIP_MODEL_ID).to(device).eval()
            clip_processor = CLIPProcessor.from_pretrained(CLIP_MODEL_ID)
            aesthetic_head = load_aesthetic_head(device)

            console.print("[cyan]Initializing PyIQA MUSIQ model...[/cyan]")
            musiq_metric = pyiqa.create_metric(MUSIQ_MODEL_ID, device=device)

            progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                MofNCompleteColumn(),
                TimeRemainingColumn(),
                console=console,
            )

            with progress:
                eval_task = progress.add_task(
                    "[cyan]Evaluating photos...",
                    total=len(pending_files),
                )

                for file_path, initial_fingerprint in pending_files:
                    try:
                        timestamp = get_image_timestamp(file_path)
                        cv_img, pil_img = load_image_pair(file_path)

                        focus_score = calculate_top_percentile_focus(cv_img)
                        blown_frac, crushed_frac, exp_penalty = check_exposure_clipping(cv_img)
                        img_phash = imagehash.phash(pil_img)

                        img_tensor = (
                            torch.from_numpy(cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB))
                            .permute(2, 0, 1)
                            .unsqueeze(0)
                            .float()
                            / 255.0
                        )
                        img_tensor = img_tensor.to(device)
                        with torch.inference_mode():
                            musiq_score = float(musiq_metric(img_tensor).item())

                        inputs = clip_processor(images=pil_img, return_tensors="pt").to(device)
                        with torch.inference_mode():
                            feats = clip_model.get_image_features(**inputs)
                            feats = feats / feats.norm(p=2, dim=-1, keepdim=True)
                            aesthetic_score = aesthetic_head(feats).item()
                            embedding = feats.cpu().numpy().flatten()

                        record = {
                            "file_name": file_path.name,
                            "file_path": str(file_path.resolve()),
                            "timestamp": timestamp,
                            "phash": img_phash,
                            "focus_score": focus_score,
                            "musiq_score": musiq_score,
                            "blown_pct": blown_frac * 100,
                            "crushed_pct": crushed_frac * 100,
                            "exposure_penalty": exp_penalty,
                            "aesthetic_score": aesthetic_score,
                            "embedding": embedding,
                            "cache_hit": False,
                        }
                        if FileFingerprint.from_path(file_path) != initial_fingerprint:
                            raise RuntimeError("File changed while it was being evaluated")
                        if evaluation_cache is not None:
                            evaluation_cache.put(
                                file_path,
                                initial_fingerprint,
                                EVALUATION_CACHE_SIGNATURE,
                                record_to_cache(record),
                            )
                        records.append(record)
                    except Exception as err:
                        console.log(f"[yellow]Skipped {file_path}:[/yellow] {err}")
                    finally:
                        progress.advance(eval_task)

    if not records:
        console.print("[red]No images were successfully processed.[/red]")
        sys.exit(1)

    assign_session_focus_factors(records)

    if no_group:
        for idx, item in enumerate(records, start=1):
            del item["embedding"]
            del item["phash"]
            item["burst_id"] = idx
            item["burst_size"] = 1
            item["relative_focus_factor"] = 1.0
            item["composite_score"] = calculate_composite_score(item)
        df = pd.DataFrame(records)
        df["burst_winner"] = True
    else:
        console.print("[bold blue]Clustering bursts and calculating composite ranks...[/bold blue]")
        labeled = group_bursts(records, time_window_seconds=time_window, sim_threshold=sim_threshold)
        for item in labeled:
            del item["embedding"]
            del item["phash"]
        df = pd.DataFrame(labeled)
        df["burst_winner"] = False
        for burst_id, group in df.groupby("burst_id"):
            winner_idx = group["composite_score"].idxmax()
            df.loc[winner_idx, "burst_winner"] = True

    df = df.sort_values(by=["burst_winner", "composite_score"], ascending=[False, False]).reset_index(drop=True)
    df["rank"] = df.index + 1

    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir = output_root / f"run_{run_stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    csv_file = run_dir / "evaluation.csv"
    df.to_csv(csv_file, index=False)

    summary_table = Table(title="Evaluation Complete - Top 10 Ranked Images", header_style="bold magenta")
    summary_table.add_column("Rank", justify="center")
    summary_table.add_column("Filename", justify="left")
    summary_table.add_column("Burst", justify="center")
    summary_table.add_column("Winner", justify="center")
    summary_table.add_column("Focus", justify="right")
    summary_table.add_column("MUSIQ", justify="right")
    summary_table.add_column("Aesthetic", justify="right")
    summary_table.add_column("Blown %", justify="right")
    summary_table.add_column("Composite", justify="right")

    for _, row in df.head(10).iterrows():
        is_winner = "[green]Yes[/green]" if row["burst_winner"] else "[dim]No[/dim]"
        summary_table.add_row(
            str(row["rank"]),
            row["file_name"],
            str(row["burst_id"]),
            is_winner,
            f"{row['focus_score']:.1f}",
            f"{row['musiq_score']:.1f}",
            f"{row['aesthetic_score']:.2f}",
            f"{row['blown_pct']:.1f}%",
            f"{row['composite_score']:.2f}",
        )

    console.print(summary_table)
    console.print(f"[bold green]Report saved to:[/bold green] {csv_file}")

    winners_df = df[df["burst_winner"]].sort_values(by="composite_score", ascending=False)
    num_winners = len(winners_df)
    console.print(f"\n[cyan]Found {num_winners} unique selects out of {len(df)} total files.[/cyan]")

    prompt_msg = (
        f"Enter number of top images to copy into '{run_dir.name}/picks/' "
        f"(1-{num_winners}, 'all', or press Enter to skip): "
    )
    user_input = console.input(f"[bold yellow]{prompt_msg}[/bold yellow]").strip().lower()

    if not user_input:
        console.print("[dim]Selection skipped. Original files remain untouched.[/dim]")
        return

    count_to_copy = num_winners if user_input == "all" else 0
    if user_input != "all":
        try:
            count_to_copy = int(user_input)
            if not 1 <= count_to_copy <= num_winners:
                raise ValueError
        except ValueError:
            console.print("[red]Invalid entry. No files copied.[/red]")
            return

    picks_dir = run_dir / "picks"
    picks_dir.mkdir(parents=True, exist_ok=False)

    picks_to_copy = winners_df.head(count_to_copy)
    console.print(f"\n[bold green]Copying top {len(picks_to_copy)} selects and companion files...[/bold green]")

    lead_files = [Path(file_path) for file_path in picks_to_copy["file_path"]]
    export_plan = build_export_plan(folder, lead_files)
    copied_files_count = copy_export_plan(picks_dir, export_plan)

    manifest_file = run_dir / "export_manifest.csv"
    with manifest_file.open("w", newline="", encoding="utf-8") as manifest_handle:
        writer = csv.DictWriter(
            manifest_handle,
            fieldnames=["source", "destination", "size_bytes"],
        )
        writer.writeheader()
        for item in export_plan:
            writer.writerow({
                "source": str(item.source),
                "destination": str(picks_dir / item.relative_destination),
                "size_bytes": item.source.stat().st_size,
            })

    console.print(
        Panel.fit(
            f"Successfully copied [bold green]{len(picks_to_copy)}[/bold green] selects "
            f"([cyan]{copied_files_count}[/cyan] total files including sidecars/companions) "
            f"to:\n[bold cyan]{picks_dir.resolve()}[/bold cyan]\n"
            f"Manifest: [cyan]{manifest_file}[/cyan]",
            title="Export Finished",
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate photos using local GPU acceleration.")
    parser.add_argument("folder", type=str, help="Directory containing RAW or JPEG photos")
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Output root (default: <folder>/.photo-cull); excluded from image discovery",
    )
    parser.add_argument("--burst-window", type=float, default=2.0, help="Maximum seconds between burst shots")
    parser.add_argument("--sim-threshold", type=float, default=0.88, help="Cosine similarity threshold for burst frames")
    parser.add_argument("--no-group", action="store_true", help="Disable burst grouping and rank all photos globally")
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not read or write the persistent evaluation cache",
    )
    cache_group.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Re-evaluate every file and replace its cached metrics",
    )
    cli_args = parser.parse_args()

    run_pipeline(
        cli_args.folder,
        cli_args.output_dir,
        cli_args.burst_window,
        cli_args.sim_threshold,
        cli_args.no_group,
        cli_args.no_cache,
        cli_args.refresh_cache,
    )
