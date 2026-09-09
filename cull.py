"""Photo Cull command-line application."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from rich.console import Console
from rich.markup import escape
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

from evaluation_cache import CachedEvaluation, EvaluationCache, FileFingerprint
from file_ops import (
    build_export_plan,
    copy_export_plan,
    discover_image_files,
    logical_asset_stem,
    select_primary_images,
)
from metadata_reader import (
    CaptureMetadata,
    read_metadata_with_exiftool,
    resolve_assumed_timezone,
    select_metadata_backend,
)
from portfolio import (
    generate_contact_sheet,
    load_feedback,
    select_portfolio_candidates,
    write_feedback_template,
)
from run_audit import RunAudit, atomic_write_csv
from scoring import (
    SCORING_PROFILES,
    ScoringProfile,
    assign_session_focus_factors,
    calculate_composite_score,
    get_scoring_profile,
    group_bursts,
)
from xmp_rating import rating_for_rank, write_xmp_rating

console = Console()

MAX_IMAGE_DIMENSION = 1024
# 5: aspect-correct JPEG draft scaling (including RAW previews), per-channel
# exposure clipping, summed-area tile variance, a neutral eye factor when no face
# is detected, and a non-zero landscape subject weight.
EVALUATION_ALGORITHM_VERSION = "5"
REPORT_FIELDS = (
    "global_quality_rank",
    "selection_rank",
    "burst_rank",
    "file_name",
    "file_path",
    "timestamp",
    "timestamp_utc",
    "timestamp_source",
    "timezone_source",
    "camera_model",
    "camera_serial",
    "sequence_number",
    "autofocus_info",
    "burst_id",
    "burst_size",
    "burst_winner",
    "focus_score",
    "focus_percentile",
    "absolute_focus_factor",
    "relative_focus_factor",
    "musiq_score",
    "aesthetic_score",
    "subject_integrity",
    "face_count",
    "eye_count",
    "eye_factor",
    "eye_warning",
    "blown_pct",
    "crushed_pct",
    "exposure_penalty",
    "composite_score",
    "feedback_decision",
    "portfolio_selected",
    "cache_hit",
)


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    input_folder: Path
    output_folder: Path | None
    time_window: float
    max_burst_duration: float
    phash_threshold: int
    sim_threshold: float
    no_group: bool
    cache_mode: str
    metadata_backend: str
    assumed_timezone: str | None
    device: str
    batch_size: int
    workers: int
    mixed_precision: bool
    preset: str
    primary: str
    selection: str | None
    diversity: float
    feedback_file: Path | None
    contact_sheet_count: int
    write_xmp: bool
    aesthetic_head: Path | None
    plain: bool
    debug: bool

    def audit_dict(self) -> dict[str, Any]:
        values = asdict(self)
        return {
            key: str(value) if isinstance(value, Path) else value for key, value in values.items()
        }


@dataclass(slots=True)
class PreparedImage:
    file_path: Path
    fingerprint: FileFingerprint
    loaded: Any
    focus_score: float
    blown_pct: float
    crushed_pct: float
    exposure_penalty: float
    phash: Any
    decode_seconds: float = 0.0
    metrics_seconds: float = 0.0
    phash_seconds: float = 0.0
    face_count: int = 0
    eye_count: int = 0
    eye_factor: float = 1.0
    eye_warning: str = ""
    musiq_score: float = 0.0


def _split_bounds(length: int, sections: int) -> Any:
    """Tile boundaries matching numpy.array_split, which keeps remainder pixels."""
    import numpy as np

    base, remainder = divmod(length, sections)
    sizes = np.full(sections, base, dtype=np.int64)
    sizes[:remainder] += 1
    return np.concatenate((np.zeros(1, dtype=np.int64), np.cumsum(sizes)))


def calculate_top_percentile_focus(
    cv_image: Any,
    grid_size: int = 16,
    top_k_pct: float = 0.03,
) -> float:
    """Calculate top-tile focus from one full-frame Laplacian operation.

    Tile variances come from a summed-area table rather than a Python loop over
    256 array views. Boundaries match numpy.array_split, so uneven remainder
    pixels stay in the same tiles.
    """
    import cv2
    import numpy as np

    gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    if height // grid_size < 8 or width // grid_size < 8:
        return float(cv2.Laplacian(gray, cv2.CV_32F).var())

    laplacian = cv2.Laplacian(gray, cv2.CV_32F)
    # Accumulate in float64: the squared sums over a full frame overflow the
    # useful precision of float32.
    totals, squares = cv2.integral2(laplacian, sdepth=cv2.CV_64F, sqdepth=cv2.CV_64F)
    rows = _split_bounds(height, grid_size)
    columns = _split_bounds(width, grid_size)
    top = rows[:-1][:, None]
    bottom = rows[1:][:, None]
    left = columns[:-1][None, :]
    right = columns[1:][None, :]

    counts = (bottom - top) * (right - left)
    tile_sums = totals[bottom, right] - totals[top, right] - totals[bottom, left] + totals[top, left]
    tile_squares = (
        squares[bottom, right] - squares[top, right] - squares[bottom, left] + squares[top, left]
    )
    means = tile_sums / counts
    # Clamp: catastrophic cancellation can drive an almost-flat tile below zero.
    variances = np.maximum(tile_squares / counts - means * means, 0.0).ravel()

    count = max(1, round(variances.size * top_k_pct))
    return float(np.mean(np.sort(variances)[::-1][:count]))


def check_exposure_clipping(cv_image: Any) -> tuple[float, float, float]:
    """Measure clipping per channel rather than on a luminance conversion.

    A saturated channel loses detail even when the luminance average looks
    healthy: a pixel at BGR (50, 100, 255) converts to mid grey, so a red sunset
    that has genuinely clipped read as unclipped. Highlights count a pixel as
    blown when any channel is saturated, which is the raw-converter convention.
    Shadows require every channel to be crushed, because a single channel at
    zero is ordinary in a saturated colour.
    """
    import numpy as np

    channels = np.asarray(cv_image)
    if channels.ndim == 2:
        channels = channels[:, :, None]
    blown_fraction = float(np.mean(np.any(channels >= 254, axis=2)))
    crushed_fraction = float(np.mean(np.all(channels <= 1, axis=2)))
    highlight_penalty = max(0.4, 1.0 - 5.0 * max(0.0, blown_fraction - 0.02))
    shadow_penalty = max(0.6, 1.0 - 2.0 * max(0.0, crushed_fraction - 0.05))
    return blown_fraction, crushed_fraction, highlight_penalty * shadow_penalty


def record_from_cache(file_path: Path, cached: CachedEvaluation) -> dict[str, Any]:
    import imagehash
    import numpy as np

    embedding = np.frombuffer(cached.embedding_bytes, dtype="<f4")
    if embedding.size != cached.embedding_length:
        raise ValueError(f"Cached embedding has the wrong size for {file_path}")
    return {
        "file_name": file_path.name,
        "file_path": str(file_path.resolve()),
        "timestamp": datetime.fromisoformat(cached.timestamp_iso),
        "timestamp_source": cached.timestamp_source,
        "timezone_source": cached.timezone_source,
        "camera_model": cached.camera_model,
        "camera_serial": cached.camera_serial,
        "sequence_number": cached.sequence_number,
        "autofocus_info": cached.autofocus_info,
        "phash": imagehash.hex_to_hash(cached.phash_hex),
        "focus_score": cached.focus_score,
        "musiq_score": cached.musiq_score,
        "blown_pct": cached.blown_pct,
        "crushed_pct": cached.crushed_pct,
        "exposure_penalty": cached.exposure_penalty,
        "aesthetic_score": cached.aesthetic_score,
        "embedding": embedding.copy(),
        "subject_integrity": cached.subject_integrity,
        "face_count": cached.face_count,
        "eye_count": cached.eye_count,
        "eye_factor": cached.eye_factor,
        "eye_warning": cached.eye_warning,
        "cache_hit": True,
    }


def record_to_cache(record: dict[str, Any]) -> CachedEvaluation:
    import numpy as np

    embedding = np.asarray(record["embedding"], dtype="<f4")
    return CachedEvaluation(
        timestamp_iso=record["timestamp"].isoformat(),
        timestamp_source=str(record["timestamp_source"]),
        timezone_source=str(record["timezone_source"]),
        camera_model=str(record["camera_model"]),
        camera_serial=str(record["camera_serial"]),
        sequence_number=str(record["sequence_number"]),
        autofocus_info=str(record["autofocus_info"]),
        phash_hex=str(record["phash"]),
        focus_score=float(record["focus_score"]),
        musiq_score=float(record["musiq_score"]),
        blown_pct=float(record["blown_pct"]),
        crushed_pct=float(record["crushed_pct"]),
        exposure_penalty=float(record["exposure_penalty"]),
        aesthetic_score=float(record["aesthetic_score"]),
        subject_integrity=float(record["subject_integrity"]),
        face_count=int(record["face_count"]),
        eye_count=int(record["eye_count"]),
        eye_factor=float(record["eye_factor"]),
        eye_warning=str(record["eye_warning"]),
        embedding_bytes=embedding.tobytes(),
        embedding_length=int(embedding.size),
    )


def run_pipeline(config: PipelineConfig) -> int:
    from model_config import (
        AESTHETIC_MODEL_REVISION,
        AESTHETIC_WEIGHTS_SHA256,
        CLIP_MODEL_ID,
        CLIP_MODEL_REVISION,
        MUSIQ_MODEL_ID,
        MUSIQ_MODEL_REVISION,
        MUSIQ_WEIGHTS_ID,
        MUSIQ_WEIGHTS_SHA256,
        sha256_file,
    )

    folder = config.input_folder.expanduser().resolve()
    if not folder.is_dir():
        _error(f"{folder} is not a valid directory")
        return 2

    output_root = (
        config.output_folder.expanduser().resolve()
        if config.output_folder is not None
        else folder / ".photo-cull"
    )
    if output_root == folder or folder.is_relative_to(output_root):
        _error("The output directory cannot be the input directory or one of its parents")
        return 2
    _validate_config(config)

    # Discovery runs before the audit directory exists, so unreadable directories
    # are collected here and recorded as soon as the audit is available.
    discovery_errors: list[OSError] = []
    with _status("Scanning source files", config):
        discovered = discover_image_files(folder, output_root, discovery_errors.append)
        all_files = select_primary_images(discovered, config.primary)
    for error in discovery_errors:
        _warning(f"Could not read {getattr(error, 'filename', None) or 'a directory'}: {error}")
    if not all_files:
        console.print("[yellow]No supported images found.[/yellow]")
        return 0

    output_root.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    run_dir = output_root / f"run_{run_stamp}"
    profile = get_scoring_profile(config.preset)
    model_manifest = {
        "clip": f"{CLIP_MODEL_ID}@{CLIP_MODEL_REVISION}",
        "musiq": f"{MUSIQ_MODEL_ID}:{MUSIQ_WEIGHTS_ID}@{MUSIQ_MODEL_REVISION}",
        "musiq_sha256": MUSIQ_WEIGHTS_SHA256,
        "aesthetic_revision": AESTHETIC_MODEL_REVISION,
        "aesthetic_sha256": (
            sha256_file(config.aesthetic_head.expanduser().resolve(strict=True))
            if config.aesthetic_head is not None
            else AESTHETIC_WEIGHTS_SHA256
        ),
        "evaluation_algorithm": EVALUATION_ALGORITHM_VERSION,
    }
    audit = RunAudit(
        run_dir,
        input_root=folder,
        output_root=output_root,
        configuration={**config.audit_dict(), "scoring_profile": profile.as_dict()},
        models=model_manifest,
        discovered_count=len(discovered),
    )
    audit.data["counts"]["primary_candidates"] = len(all_files)
    for error in discovery_errors:
        audit.record_failure(
            Path(error.filename) if getattr(error, "filename", None) else None,
            "discovery",
            error,
        )

    try:
        return _execute_pipeline(
            config,
            folder,
            output_root,
            run_dir,
            all_files,
            profile,
            audit,
        )
    except KeyboardInterrupt:
        audit.finish("interrupted")
        console.print("\n[yellow]Interrupted. Completed evaluations remain cached.[/yellow]")
        return 130
    except Exception as error:
        audit.record_failure(None, str(audit.data.get("phase", "pipeline")), error)
        audit.finish("failed")
        _error(str(error))
        if config.debug:
            raise
        return 1


def _execute_pipeline(
    config: PipelineConfig,
    folder: Path,
    output_root: Path,
    run_dir: Path,
    all_files: list[Path],
    profile: ScoringProfile,
    audit: RunAudit,
) -> int:
    metadata_backend = select_metadata_backend(config.metadata_backend)
    assumed_timezone = resolve_assumed_timezone(config.assumed_timezone)
    feedback = load_feedback(config.feedback_file, folder)

    cache_signature = _cache_signature(config, metadata_backend, audit.data["models"])
    records: list[dict[str, Any]] = []
    pending_files: list[tuple[Path, FileFingerprint]] = []
    cache_context = (
        nullcontext(None)
        if config.cache_mode == "none"
        else EvaluationCache(output_root / "evaluation_cache.sqlite3")
    )

    audit.set_phase("cache_lookup")
    with cache_context as evaluation_cache:
        for file_path in all_files:
            try:
                fingerprint = FileFingerprint.from_path(file_path)
                cached = (
                    None
                    if evaluation_cache is None or config.cache_mode == "refresh"
                    else evaluation_cache.get(file_path, fingerprint, cache_signature)
                )
                if cached is None:
                    pending_files.append((file_path, fingerprint))
                else:
                    records.append(record_from_cache(file_path, cached))
            except Exception as error:
                audit.record_failure(file_path, "cache_lookup", error)
                _warning(f"Could not inspect {file_path}: {error}")

        audit.set_count("cached", len(records))
        if records:
            console.print(f"[green]Reused {len(records)} cached evaluations.[/green]")

        if not pending_files:
            audit.data["environment"]["resolved_device"] = "cache-only"
            _show_environment(
                config,
                folder,
                output_root,
                "cache-only",
                metadata_backend,
                len(all_files),
            )

        metadata_by_path: dict[Path, CaptureMetadata] = {}
        allow_cache_write = True
        if pending_files and metadata_backend == "exiftool":
            audit.set_phase("metadata")
            try:
                with _status("Reading capture metadata with ExifTool", config):
                    metadata_by_path = read_metadata_with_exiftool(
                        (file_path for file_path, _ in pending_files),
                        assumed_timezone,
                    )
            except Exception as error:
                if config.metadata_backend == "exiftool":
                    raise
                allow_cache_write = False
                audit.record_failure(None, "metadata_bulk", error)
                _warning(f"ExifTool failed; using embedded metadata for this run: {error}")

        if pending_files:
            from model_runtime import (
                ModelRuntime,
                is_device_fallback_error,
                resolve_device,
            )

            device = resolve_device(config.device)
            audit.data["environment"]["resolved_device"] = device.type
            _show_environment(
                config,
                folder,
                output_root,
                device.type,
                metadata_backend,
                len(all_files),
            )
            audit.set_phase("model_loading")
            try:
                with _status("Loading pinned scoring models", config):
                    runtime = ModelRuntime(
                        device,
                        preset=config.preset,
                        aesthetic_weights=config.aesthetic_head,
                        mixed_precision=config.mixed_precision,
                    )
            except RuntimeError as error:
                if (
                    config.device != "auto"
                    or device.type == "cpu"
                    or not is_device_fallback_error(error)
                ):
                    raise
                audit.record_failure(None, "model_device_fallback", error)
                _warning(f"Accelerator initialization failed; retrying on CPU: {error}")
                device = resolve_device("cpu")
                runtime = ModelRuntime(
                    device,
                    preset=config.preset,
                    aesthetic_weights=config.aesthetic_head,
                    mixed_precision=False,
                )
            audit.data["models"]["aesthetic_sha256"] = runtime.aesthetic_sha256
            audit.data["models"]["musiq_sha256"] = runtime.musiq_sha256
            audit.data["environment"]["resolved_device"] = runtime.device.type
            audit.set_phase("evaluation")
            starting_device = runtime.device.type
            records.extend(
                _evaluate_pending(
                    pending_files,
                    metadata_by_path,
                    runtime,
                    profile,
                    config,
                    assumed_timezone,
                    evaluation_cache if allow_cache_write else None,
                    cache_signature,
                    audit,
                )
            )
            audit.data["environment"]["resolved_device"] = runtime.device.type
            if runtime.device.type != starting_device:
                _warning(
                    f"Inference moved from {starting_device.upper()} to CPU after a device error"
                )

    if not records:
        raise RuntimeError("No images were successfully processed")

    audit.set_phase("ranking")
    assign_session_focus_factors(records, profile)
    if config.no_group:
        for burst_id, record in enumerate(_timestamp_order(records), start=1):
            record["burst_id"] = burst_id
            record["burst_size"] = 1
            record["relative_focus_factor"] = 1.0
            record["composite_score"] = calculate_composite_score(
                record,
                profile=profile,
            )
    else:
        group_bursts(
            records,
            time_window_seconds=config.time_window,
            phash_threshold=config.phash_threshold,
            sim_threshold=config.sim_threshold,
            max_burst_duration_seconds=config.max_burst_duration,
            profile=profile,
        )
    winners = _assign_ranks(records)
    audit.set_count("winners", len(winners))

    for record in records:
        record["feedback_decision"] = feedback.get(record["file_path"], "")
        record["portfolio_selected"] = False

    _show_summary(winners)

    # Persist the expensive metrics before the interactive prompt. An interrupt or
    # a rejected selection value must not discard a completed evaluation pass.
    audit.set_phase("reporting")
    evaluation_file = run_dir / "evaluation.csv"
    _write_evaluation_csv(evaluation_file, records)
    audit.add_output("evaluation", evaluation_file)

    audit.set_phase("awaiting_selection")
    requested_count = _selection_count(config.selection, len(winners))
    candidate_pool = list(winners)
    candidate_paths = {record["file_path"] for record in candidate_pool}
    candidate_pool.extend(
        record
        for record in records
        if feedback.get(record["file_path"]) == "keep"
        and record["file_path"] not in candidate_paths
    )
    selected = select_portfolio_candidates(
        candidate_pool,
        requested_count,
        diversity_strength=config.diversity,
        feedback=feedback,
    )
    for order, record in enumerate(selected, start=1):
        record["portfolio_selected"] = True
        record["portfolio_selection_order"] = order
    audit.set_count("selected", len(selected))

    audit.set_phase("reporting")
    # Rewrite with the selection columns now populated.
    _write_evaluation_csv(evaluation_file, records)

    feedback_template = run_dir / "feedback.csv"
    write_feedback_template(
        feedback_template,
        _global_quality_order(records),
        {record["file_path"] for record in selected},
    )
    audit.add_output("feedback", feedback_template)

    if config.contact_sheet_count > 0:
        from image_loader import load_image

        audit.set_phase("contact_sheet")
        review_candidates = candidate_pool[: config.contact_sheet_count]
        contact_sheet = run_dir / "review.html"
        generated, thumbnail_failures = generate_contact_sheet(
            contact_sheet,
            review_candidates,
            lambda path: load_image(path, max_dim=480).pil_image,
        )
        for file_path, error in thumbnail_failures:
            audit.record_failure(file_path, "contact_sheet", error)
        audit.data["counts"]["contact_sheet_images"] = generated
        audit.add_output("contact_sheet", contact_sheet)

    if selected:
        audit.set_phase("export")
        picks_dir = run_dir / "picks"
        picks_dir.mkdir(parents=True, exist_ok=False)
        export_plan = build_export_plan(
            folder,
            (Path(record["file_path"]) for record in selected),
        )
        copied_count = copy_export_plan(picks_dir, export_plan)

        xmp_outputs: list[Path] = []
        if config.write_xmp:
            rated_families: set[tuple[Path, str]] = set()
            for rank, record in enumerate(selected, start=1):
                exported_image = picks_dir / Path(record["file_path"]).relative_to(folder)
                family_key = (exported_image.parent, logical_asset_stem(exported_image))
                if family_key in rated_families:
                    continue
                rated_families.add(family_key)
                try:
                    xmp_outputs.append(
                        write_xmp_rating(
                            exported_image,
                            rating_for_rank(rank, len(selected)),
                        )
                    )
                except Exception as error:
                    audit.record_failure(Path(record["file_path"]), "xmp", error)

        copied_destinations = {
            (picks_dir / item.relative_destination).resolve() for item in export_plan
        }
        resolved_xmp_outputs = {path.resolve() for path in xmp_outputs}
        manifest_rows = [
            {
                "source": str(item.source),
                "destination": str(picks_dir / item.relative_destination),
                "source_size_bytes": item.source.stat().st_size,
                "destination_size_bytes": (picks_dir / item.relative_destination).stat().st_size,
                "kind": (
                    "copied_and_rated"
                    if (picks_dir / item.relative_destination).resolve() in resolved_xmp_outputs
                    else "copied"
                ),
            }
            for item in export_plan
        ]
        manifest_rows.extend(
            {
                "source": "",
                "destination": str(xmp_path),
                "source_size_bytes": "",
                "destination_size_bytes": xmp_path.stat().st_size,
                "kind": "generated_xmp",
            }
            for xmp_path in xmp_outputs
            if xmp_path.resolve() not in copied_destinations
        )
        audit.set_count(
            "exported_files",
            copied_count + sum(path.resolve() not in copied_destinations for path in xmp_outputs),
        )
        export_manifest = run_dir / "export_manifest.csv"
        atomic_write_csv(
            export_manifest,
            (
                "source",
                "destination",
                "source_size_bytes",
                "destination_size_bytes",
                "kind",
            ),
            manifest_rows,
        )
        audit.add_output("picks", picks_dir)
        audit.add_output("export_manifest", export_manifest)

        console.print(
            Panel.fit(
                f"Copied [bold green]{len(selected)}[/bold green] selections "
                f"([cyan]{copied_count}[/cyan] files) to\n"
                f"[bold cyan]{escape(str(picks_dir))}[/bold cyan]",
                title="Export Finished",
            )
        )
    else:
        console.print(
            "[dim]Selection skipped; reports and review artifacts were still created.[/dim]"
        )

    audit.finish("completed")
    console.print(f"[bold green]Run report:[/bold green] {escape(str(evaluation_file))}")
    return 0


def _evaluate_pending(
    pending_files: list[tuple[Path, FileFingerprint]],
    metadata_by_path: dict[Path, CaptureMetadata],
    runtime: Any,
    profile: ScoringProfile,
    config: PipelineConfig,
    assumed_timezone: Any,
    evaluation_cache: EvaluationCache | None,
    cache_signature: str,
    audit: RunAudit,
) -> list[dict[str, Any]]:
    from advanced_analysis import analyze_portrait

    evaluated: list[dict[str, Any]] = []
    completed_count = 0
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console,
        disable=config.plain or not console.is_terminal,
    )

    with ThreadPoolExecutor(max_workers=config.workers) as executor, progress:
        task = progress.add_task("[cyan]Evaluating photos...", total=len(pending_files))
        batches = iter(_chunks(pending_files, config.batch_size))

        def submit_batch(batch: list[tuple[Path, FileFingerprint]]) -> list[Any]:
            return [
                executor.submit(
                    _prepare_image,
                    file_path,
                    fingerprint,
                    metadata_by_path.get(file_path.resolve()),
                    assumed_timezone,
                )
                for file_path, fingerprint in batch
            ]

        current_batch = next(batches)
        current_futures = submit_batch(current_batch)
        while True:
            prepared: list[PreparedImage] = []
            for (file_path, _), future in zip(current_batch, current_futures, strict=True):
                try:
                    item = future.result()
                except Exception as error:
                    audit.record_failure(file_path, "decode", error)
                    _warning(f"Skipped {file_path}: {error}")
                    progress.advance(task)
                    continue
                prepared.append(item)
                audit.accumulate_stage("decode", item.decode_seconds)
                audit.accumulate_stage("cpu_metrics", item.metrics_seconds)
                audit.accumulate_stage("phash", item.phash_seconds)

            next_batch = next(batches, None)
            next_futures = submit_batch(next_batch) if next_batch is not None else None

            if profile.name == "portrait":
                for item in prepared:
                    try:
                        started = perf_counter()
                        portrait = analyze_portrait(item.loaded.cv_image)
                        audit.accumulate_stage("portrait", perf_counter() - started)
                        item.face_count = portrait.face_count
                        item.eye_count = portrait.eye_count
                        item.eye_factor = portrait.eye_factor
                        item.eye_warning = portrait.warning
                    except Exception as error:
                        audit.record_failure(item.file_path, "portrait_analysis", error)
                        _warning(f"Portrait analysis unavailable for {item.file_path}: {error}")

            musiq_ready: list[PreparedImage] = []
            for item in prepared:
                try:
                    started = perf_counter()
                    item.musiq_score = runtime.infer_musiq(item.loaded.cv_image)
                    audit.accumulate_stage("musiq", perf_counter() - started)
                    musiq_ready.append(item)
                except Exception as error:
                    audit.record_failure(item.file_path, "musiq", error)
                    _warning(f"Skipped {item.file_path}: {error}")
                    progress.advance(task)

            inference_results: list[Any] = []
            if musiq_ready:
                try:
                    started = perf_counter()
                    inference_results = runtime.infer_clip_batch(
                        [item.loaded.pil_image for item in musiq_ready]
                    )
                    audit.accumulate_stage("clip", perf_counter() - started, len(musiq_ready))
                except Exception:
                    inference_results = []
                    isolated_items: list[PreparedImage] = []
                    for item in musiq_ready:
                        try:
                            started = perf_counter()
                            inference_results.extend(
                                runtime.infer_clip_batch([item.loaded.pil_image])
                            )
                            audit.accumulate_stage("clip", perf_counter() - started)
                            isolated_items.append(item)
                        except Exception as error:
                            audit.record_failure(item.file_path, "clip", error)
                            _warning(f"Skipped {item.file_path}: {error}")
                            progress.advance(task)
                    musiq_ready = isolated_items

            for item, inference in zip(musiq_ready, inference_results, strict=True):
                metadata = item.loaded.metadata
                record = {
                    "file_name": item.file_path.name,
                    "file_path": str(item.file_path.resolve()),
                    "timestamp": metadata.capture_time,
                    "timestamp_source": metadata.timestamp_source,
                    "timezone_source": metadata.timezone_source,
                    "camera_model": metadata.camera_model,
                    "camera_serial": metadata.camera_serial,
                    "sequence_number": metadata.sequence_number,
                    "autofocus_info": metadata.autofocus_info,
                    "phash": item.phash,
                    "focus_score": item.focus_score,
                    "musiq_score": item.musiq_score,
                    "blown_pct": item.blown_pct,
                    "crushed_pct": item.crushed_pct,
                    "exposure_penalty": item.exposure_penalty,
                    "aesthetic_score": inference.aesthetic_score,
                    "embedding": inference.embedding,
                    "subject_integrity": inference.subject_integrity,
                    "face_count": item.face_count,
                    "eye_count": item.eye_count,
                    "eye_factor": item.eye_factor,
                    "eye_warning": item.eye_warning,
                    "cache_hit": False,
                }
                try:
                    if FileFingerprint.from_path(item.file_path) != item.fingerprint:
                        raise RuntimeError("File changed while it was being evaluated")
                except Exception as error:
                    # A file that changed while it was read has an invalid record,
                    # so this stays the one condition that discards the result.
                    audit.record_failure(item.file_path, "checkpoint", error)
                    _warning(f"Skipped {item.file_path}: {error}")
                    progress.advance(task)
                    continue

                if evaluation_cache is not None:
                    try:
                        evaluation_cache.put(
                            item.file_path,
                            item.fingerprint,
                            cache_signature,
                            record_to_cache(record),
                        )
                    except Exception as error:
                        # A failed commit degrades to an uncached success. The
                        # evaluation is complete, so it still ranks and exports.
                        audit.record_failure(item.file_path, "cache_write", error)
                        _warning(f"Could not cache {item.file_path}: {error}")

                evaluated.append(record)
                completed_count += 1
                progress.advance(task)
            audit.set_count("evaluated", completed_count)

            if next_batch is None or next_futures is None:
                break
            current_batch = next_batch
            current_futures = next_futures

    return evaluated


def _prepare_image(
    file_path: Path,
    fingerprint: FileFingerprint,
    metadata: CaptureMetadata | None,
    assumed_timezone: Any,
) -> PreparedImage:
    import imagehash

    from image_loader import load_image

    started = perf_counter()
    loaded = load_image(
        file_path,
        max_dim=MAX_IMAGE_DIMENSION,
        metadata=metadata,
        assumed_timezone=assumed_timezone,
    )
    decoded_at = perf_counter()
    focus_score = calculate_top_percentile_focus(loaded.cv_image)
    blown_fraction, crushed_fraction, exposure_penalty = check_exposure_clipping(loaded.cv_image)
    measured_at = perf_counter()
    phash = imagehash.phash(loaded.pil_image)
    hashed_at = perf_counter()
    return PreparedImage(
        file_path=file_path,
        fingerprint=fingerprint,
        loaded=loaded,
        focus_score=focus_score,
        blown_pct=blown_fraction * 100.0,
        crushed_pct=crushed_fraction * 100.0,
        exposure_penalty=exposure_penalty,
        phash=phash,
        decode_seconds=decoded_at - started,
        metrics_seconds=measured_at - decoded_at,
        phash_seconds=hashed_at - measured_at,
    )


def _assign_ranks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_burst: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        by_burst.setdefault(int(record["burst_id"]), []).append(record)

    for group in by_burst.values():
        ordered = sorted(group, key=_quality_sort_key)
        for burst_rank, record in enumerate(ordered, start=1):
            record["burst_rank"] = burst_rank
            record["burst_winner"] = burst_rank == 1

    for global_rank, record in enumerate(_global_quality_order(records), start=1):
        record["global_quality_rank"] = global_rank

    winners = sorted(
        (record for record in records if record["burst_winner"]),
        key=_quality_sort_key,
    )
    for selection_rank, record in enumerate(winners, start=1):
        record["selection_rank"] = selection_rank
    for record in records:
        record.setdefault("selection_rank", "")
    return winners


def _report_row(record: dict[str, Any]) -> dict[str, Any]:
    capture_time = record["timestamp"]
    row = dict(record)
    row["timestamp"] = capture_time.isoformat()
    row["timestamp_utc"] = capture_time.astimezone(UTC).isoformat()
    return row


def _show_environment(
    config: PipelineConfig,
    folder: Path,
    output_root: Path,
    device: str,
    metadata_backend: str,
    file_count: int,
) -> None:
    console.print(
        Panel.fit(
            f"[bold green]Compute:[/bold green] [cyan]{device.upper()}[/cyan]\n"
            f"[bold green]Source:[/bold green] {escape(str(folder))}\n"
            f"[bold green]Output:[/bold green] {escape(str(output_root))}\n"
            f"[bold green]Images:[/bold green] {file_count} primary assets\n"
            f"[bold green]Metadata:[/bold green] {metadata_backend}\n"
            f"[bold green]Preset:[/bold green] {config.preset}\n"
            f"[bold green]Burst grouping:[/bold green] {'off' if config.no_group else 'on'}",
            title="Photo Cull",
        )
    )


def _show_summary(winners: list[dict[str, Any]]) -> None:
    table = Table(title="Top Selection Candidates", header_style="bold magenta")
    for name, justification in (
        ("Select", "center"),
        ("Global", "center"),
        ("Filename", "left"),
        ("Burst", "center"),
        ("Focus", "right"),
        ("MUSIQ", "right"),
        ("Aesthetic", "right"),
        ("Composite", "right"),
    ):
        table.add_column(name, justify=justification)
    for record in winners[:10]:
        table.add_row(
            str(record["selection_rank"]),
            str(record["global_quality_rank"]),
            escape(record["file_name"]),
            str(record["burst_id"]),
            f"{record['focus_score']:.1f}",
            f"{record['musiq_score']:.1f}",
            f"{record['aesthetic_score']:.2f}",
            f"{record['composite_score']:.2f}",
        )
    console.print(table)
    console.print(f"[cyan]Found {len(winners)} unique candidates.[/cyan]")


def _write_evaluation_csv(destination: Path, records: list[dict[str, Any]]) -> None:
    atomic_write_csv(
        destination,
        REPORT_FIELDS,
        (_report_row(record) for record in _global_quality_order(records)),
    )


def _selection_count(value: str | None, available: int) -> int:
    if value is None and console.is_terminal and sys.stdin.isatty():
        try:
            value = console.input(
                f"[bold yellow]Select how many to export (1-{available}, all, or Enter to skip): [/bold yellow]"
            ).strip()
        except EOFError:
            value = "none"
    elif value is None:
        value = "none"
        console.print("[dim]Non-interactive input detected; export skipped.[/dim]")

    normalized = (value or "none").strip().casefold()
    if normalized in {"", "none", "skip", "0"}:
        return 0
    if normalized == "all":
        return available
    try:
        count = int(normalized)
    except ValueError as error:
        raise ValueError("--select must be a positive integer, 'all', or 'none'") from error
    if not 1 <= count <= available:
        raise ValueError(f"Selection count must be between 1 and {available}")
    return count


def _cache_signature(
    config: PipelineConfig,
    metadata_backend: str,
    model_manifest: dict[str, str],
) -> str:
    return ";".join(
        (
            f"algorithm={EVALUATION_ALGORITHM_VERSION}",
            f"clip={model_manifest['clip']}",
            f"musiq={model_manifest['musiq']}",
            f"musiq_sha256={model_manifest['musiq_sha256']}",
            f"aesthetic={model_manifest['aesthetic_sha256']}",
            f"metadata={metadata_backend}",
            f"assumed_timezone={config.assumed_timezone or 'system'}",
            f"max_dim={MAX_IMAGE_DIMENSION}",
            f"preset={config.preset}",
        )
    )


def _validate_selection(value: str | None) -> None:
    """Reject a malformed --select before any evaluation work begins.

    The count is still checked against the available candidates later, because
    that number is only known after ranking.
    """
    if value is None:
        return
    normalized = value.strip().casefold()
    if normalized in {"", "none", "skip", "all"}:
        return
    try:
        count = int(normalized)
    except ValueError:
        raise ValueError("--select must be a positive integer, 'all', or 'none'") from None
    if count < 0:
        raise ValueError("--select cannot be negative")


def _validate_config(config: PipelineConfig) -> None:
    _validate_selection(config.selection)
    if config.time_window < 0:
        raise ValueError("--burst-window cannot be negative")
    if config.max_burst_duration < config.time_window:
        raise ValueError("--max-burst-duration must be at least --burst-window")
    if not 0.0 <= config.sim_threshold <= 1.0:
        raise ValueError("--sim-threshold must be between 0 and 1")
    if config.phash_threshold < 0:
        raise ValueError("--phash-threshold cannot be negative")
    if config.batch_size < 1 or config.workers < 1:
        raise ValueError("--batch-size and --workers must be positive")
    if not 0.0 <= config.diversity <= 1.0:
        raise ValueError("--diversity must be between 0 and 1")
    if config.contact_sheet_count < 0:
        raise ValueError("--contact-sheet cannot be negative")
    if config.aesthetic_head is not None and not config.aesthetic_head.expanduser().is_file():
        raise ValueError(f"Aesthetic head does not exist: {config.aesthetic_head}")
    if config.feedback_file is not None and not config.feedback_file.expanduser().is_file():
        raise ValueError(f"Feedback file does not exist: {config.feedback_file}")


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _timestamp_order(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda record: (record["timestamp"], record["file_path"].casefold()),
    )


def _quality_sort_key(record: dict[str, Any]) -> tuple[float, str]:
    return -float(record["composite_score"]), str(record["file_path"]).casefold()


def _global_quality_order(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=_quality_sort_key)


def _status(message: str, config: PipelineConfig):
    if config.plain or not console.is_terminal:
        console.print(message)
        return nullcontext()
    return console.status(f"[cyan]{message}...[/cyan]")


def _warning(message: str) -> None:
    console.log(f"[yellow]{escape(message)}[/yellow]")


def _error(message: str) -> None:
    console.print(f"[bold red]Error:[/bold red] {escape(message)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="photo-cull",
        description="Rank and review large photo collections using local models.",
    )
    parser.add_argument("folder", type=Path, help="Directory containing photos")
    parser.add_argument(
        "--output-dir", type=Path, help="Output root (default: <folder>/.photo-cull)"
    )
    parser.add_argument("--preset", choices=sorted(SCORING_PROFILES), default="balanced")
    parser.add_argument(
        "--primary",
        choices=("raw", "jpeg", "all"),
        default="raw",
        help="Preferred file in RAW+JPEG pairs",
    )
    parser.add_argument("--burst-window", type=float, default=2.0)
    parser.add_argument("--max-burst-duration", type=float, default=10.0)
    parser.add_argument("--phash-threshold", type=int, default=8)
    parser.add_argument("--sim-threshold", type=float, default=0.88)
    parser.add_argument("--no-group", action="store_true")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--no-mixed-precision", action="store_true")
    parser.add_argument(
        "--metadata-backend", choices=("auto", "exiftool", "pillow"), default="auto"
    )
    parser.add_argument(
        "--assume-timezone", help="IANA timezone for EXIF timestamps without offsets"
    )
    parser.add_argument("--select", dest="selection", help="Positive count, 'all', or 'none'")
    parser.add_argument(
        "--diversity", type=float, default=0.0, help="Portfolio diversity strength from 0 to 1"
    )
    parser.add_argument(
        "--feedback", type=Path, help="CSV containing file_path and keep/reject decision columns"
    )
    parser.add_argument(
        "--contact-sheet", type=int, default=100, help="Maximum review thumbnails; 0 disables"
    )
    parser.add_argument(
        "--write-xmp", action="store_true", help="Write ratings only beside exported copies"
    )
    parser.add_argument(
        "--aesthetic-head", type=Path, help="Optional compatible personal aesthetic-head weights"
    )
    parser.add_argument("--plain", action="store_true", help="Disable animated progress")
    parser.add_argument("--debug", action="store_true", help="Show exception tracebacks")
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument("--no-cache", action="store_true")
    cache_group.add_argument("--refresh-cache", action="store_true")
    return parser


def config_from_args(args: argparse.Namespace) -> PipelineConfig:
    cache_mode = "none" if args.no_cache else "refresh" if args.refresh_cache else "use"
    return PipelineConfig(
        input_folder=args.folder,
        output_folder=args.output_dir,
        time_window=args.burst_window,
        max_burst_duration=args.max_burst_duration,
        phash_threshold=args.phash_threshold,
        sim_threshold=args.sim_threshold,
        no_group=args.no_group,
        cache_mode=cache_mode,
        metadata_backend=args.metadata_backend,
        assumed_timezone=args.assume_timezone,
        device=args.device,
        batch_size=args.batch_size,
        workers=args.workers,
        mixed_precision=not args.no_mixed_precision,
        preset=args.preset,
        primary=args.primary,
        selection=args.selection,
        diversity=args.diversity,
        feedback_file=args.feedback,
        contact_sheet_count=args.contact_sheet,
        write_xmp=args.write_xmp,
        aesthetic_head=args.aesthetic_head,
        plain=args.plain,
        debug=args.debug,
    )


def main(argv: list[str] | None = None) -> int:
    global console
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console(no_color=args.plain)
    try:
        return run_pipeline(config_from_args(args))
    except (OSError, RuntimeError, ValueError) as error:
        _error(str(error))
        if args.debug:
            raise
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
