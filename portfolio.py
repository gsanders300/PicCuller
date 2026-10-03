"""Human review artifacts, feedback overrides, and diverse selection."""

from __future__ import annotations

import csv
import os
import shutil
from collections.abc import Callable, Iterable
from html import escape
from pathlib import Path
from typing import Any

from run_audit import atomic_write_text

VALID_DECISIONS = {"keep", "reject"}


def load_feedback(
    feedback_file: Path | None,
    input_root: Path,
) -> dict[str, str]:
    """Load keep/reject overrides keyed by resolved source path."""
    if feedback_file is None:
        return {}

    decisions: dict[str, str] = {}
    with feedback_file.expanduser().open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "file_path" not in reader.fieldnames:
            raise ValueError("Feedback CSV must contain a file_path column")
        for row_number, row in enumerate(reader, start=2):
            decision = (row.get("decision") or "").strip().casefold()
            if not decision:
                continue
            if decision not in VALID_DECISIONS:
                raise ValueError(f"Invalid feedback decision {decision!r} on row {row_number}")
            raw_path = Path(row["file_path"])
            resolved = (
                raw_path.resolve() if raw_path.is_absolute() else (input_root / raw_path).resolve()
            )
            decisions[str(resolved)] = decision
    return decisions


def select_portfolio_candidates(
    candidates: list[dict[str, Any]],
    count: int,
    *,
    diversity_strength: float = 0.0,
    feedback: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Select high-quality candidates with optional CLIP-space diversity.

    The result is ordered by composite score, so selection rank and any rating
    derived from it always agree with measured quality. Feedback keeps are still
    forced into the selection, but they no longer displace better-scoring images.
    """
    if count < 0:
        raise ValueError("Selection count cannot be negative")
    if not 0.0 <= diversity_strength <= 1.0:
        raise ValueError("Diversity strength must be between 0 and 1")

    feedback = feedback or {}
    eligible = [
        candidate for candidate in candidates if _decision(candidate, feedback) != "reject"
    ]
    eligible.sort(key=_quality_key)
    pinned = [candidate for candidate in eligible if _decision(candidate, feedback) == "keep"]
    pinned_identities = {_identity(candidate) for candidate in pinned}
    available = [
        candidate for candidate in eligible if _identity(candidate) not in pinned_identities
    ]
    target_count = min(len(eligible), max(count, len(pinned)))

    if diversity_strength == 0.0:
        remaining = max(0, target_count - len(pinned))
        selected = pinned + available[:remaining]
    else:
        selected = _select_with_diversity(
            pinned,
            available,
            eligible,
            target_count,
            diversity_strength,
        )

    return sorted(selected, key=_quality_key)


def _select_with_diversity(
    pinned: list[dict[str, Any]],
    available: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    target_count: int,
    diversity_strength: float,
) -> list[dict[str, Any]]:
    """Run maximal marginal relevance over normalized CLIP embeddings.

    Similarity is kept as a running per-candidate maximum against the already
    selected set, so each pick costs one matrix-vector product instead of
    recomputing every pair. That is O(K x N x D) rather than O(K^2 x N x D).
    """
    # numpy stays a local import: portfolio is reachable from the CLI help and
    # cache-only paths, which must not initialize the machine-learning stack.
    import numpy as np

    selected = list(pinned)
    if not available or len(selected) >= target_count:
        return selected

    qualities = [float(candidate["composite_score"]) for candidate in eligible]
    minimum_quality = min(qualities, default=0.0)
    quality_span = max(qualities, default=0.0) - minimum_quality

    embeddings = np.asarray(
        [np.asarray(candidate["embedding"], dtype=np.float32) for candidate in available]
    )
    scores = np.asarray(
        [float(candidate["composite_score"]) for candidate in available],
        dtype=np.float64,
    )
    quality = (
        (scores - minimum_quality) / quality_span
        if quality_span > 0
        else np.ones(len(available), dtype=np.float64)
    )
    paths = [str(candidate["file_path"]).casefold() for candidate in available]

    if selected:
        chosen_embeddings = np.asarray(
            [np.asarray(candidate["embedding"], dtype=np.float32) for candidate in selected]
        )
        maximum_similarity = ((embeddings @ chosen_embeddings.T) + 1.0) / 2.0
        maximum_similarity = maximum_similarity.max(axis=1).astype(np.float64)
    else:
        maximum_similarity = np.zeros(len(available), dtype=np.float64)

    unselected = np.ones(len(available), dtype=bool)
    while len(selected) < target_count and bool(unselected.any()):
        objective = (1.0 - diversity_strength) * quality
        objective -= diversity_strength * maximum_similarity
        objective = np.where(unselected, objective, -np.inf)
        # Ties resolve by higher quality, then by normalized path order, matching
        # the pure-quality path so the diversity value cannot reverse a tie.
        tied = np.flatnonzero(objective == objective.max())
        index = (
            int(tied[0])
            if tied.size == 1
            else int(min(tied, key=lambda position: (-quality[position], paths[position])))
        )
        selected.append(available[index])
        unselected[index] = False
        np.maximum(
            maximum_similarity,
            ((embeddings @ embeddings[index]) + 1.0) / 2.0,
            out=maximum_similarity,
        )

    return selected


def _identity(candidate: dict[str, Any]) -> str:
    return str(Path(candidate["file_path"]).resolve())


def _decision(candidate: dict[str, Any], feedback: dict[str, str]) -> str:
    return feedback.get(_identity(candidate), "")


def _quality_key(candidate: dict[str, Any]) -> tuple[float, str]:
    return -float(candidate["composite_score"]), str(candidate["file_path"]).casefold()


def write_feedback_template(
    destination: Path,
    candidates: Iterable[dict[str, Any]],
    selected_paths: set[str] | None = None,
) -> None:
    from run_audit import atomic_write_csv

    selected_paths = selected_paths or set()
    fieldnames = (
        "file_path",
        "decision",
        "selection_rank",
        "global_quality_rank",
        "composite_score",
    )
    rows = []
    for candidate in candidates:
        resolved = str(Path(candidate["file_path"]).resolve())
        rows.append(
            {
                "file_path": resolved,
                "decision": "keep" if resolved in selected_paths else "",
                "selection_rank": candidate.get("selection_rank", ""),
                "global_quality_rank": candidate.get("global_quality_rank", ""),
                "composite_score": candidate.get("composite_score", ""),
            }
        )
    atomic_write_csv(destination, fieldnames, rows)


def link_or_copy(source: Path, destination: Path) -> None:
    """Publish a shared thumbnail into a run directory without copying bytes.

    A hard link keeps the run directory self-contained (the link is the file, so
    moving or archiving the directory carries the pixels) while several runs over
    the same collection share one copy on disk. Falls back to a copy when the
    filesystem refuses links.
    """
    destination.unlink(missing_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def generate_contact_sheet(
    destination: Path,
    candidates: list[dict[str, Any]],
    thumbnail_provider: Callable[[Path], Path],
    selected_paths: set[str] | None = None,
) -> tuple[int, list[tuple[Path, Exception]]]:
    """Generate a self-contained local review page with downloadable feedback.

    `thumbnail_provider` returns a path to a ready thumbnail for a source image.
    Keeping generation behind that callable lets the caller reuse thumbnails
    across runs instead of decoding every source again.
    """
    selected_paths = selected_paths or set()
    thumbnail_dir = destination.parent / "thumbnails"
    thumbnail_dir.mkdir(parents=True, exist_ok=True)
    cards: list[str] = []
    failures: list[tuple[Path, Exception]] = []
    exported_count = 0

    for index, candidate in enumerate(candidates, start=1):
        source = Path(candidate["file_path"])
        thumbnail_name = f"{index:04d}.jpg"
        try:
            link_or_copy(thumbnail_provider(source), thumbnail_dir / thumbnail_name)
        except Exception as error:
            failures.append((source, error))
            continue

        path_text = str(source.resolve())
        metrics = (
            f"Score {float(candidate['composite_score']):.2f} · "
            f"Aesthetic {float(candidate['aesthetic_score']):.2f} · "
            f"MUSIQ {float(candidate['musiq_score']):.1f}"
        )
        rank = candidate.get("selection_rank", "")
        # A forced keep that lost its burst has no winner rank to show.
        label = f"#{rank}" if rank != "" else "Feedback keep"
        exported = path_text in selected_paths
        exported_count += exported
        badge = ' <span class="badge">Exported</span>' if exported else ""
        cards.append(
            f"""
            <article class="card" data-path="{escape(path_text, quote=True)}">
              <img src="thumbnails/{thumbnail_name}" alt="{escape(source.name, quote=True)}" loading="lazy">
              <h2>{escape(label)} {escape(source.name)}{badge}</h2>
              <p>{escape(metrics)}</p>
              <p class="reason">{escape(str(candidate.get("score_reason", "")))}</p>
              <div><button data-decision="keep">Keep</button><button data-decision="reject">Reject</button></div>
            </article>
            """
        )

    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Photo Cull Review</title>
<style>
body{{font:15px system-ui;background:#111;color:#eee;margin:1rem}}header{{position:sticky;top:0;background:#111;padding:.5rem;z-index:2}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:1rem}}.card{{background:#222;padding:.75rem;border-radius:8px}}
.card img{{width:100%;height:320px;object-fit:contain;background:#000;cursor:zoom-in}}h2{{font-size:1rem;overflow-wrap:anywhere}}p{{color:#bbb}}
button{{margin-right:.5rem;padding:.5rem 1rem}}.keep{{outline:3px solid #3c6}}.reject{{opacity:.4;outline:3px solid #d55}}
.reason{{font-size:.85rem}}.badge{{font-size:.75rem;background:#3c6;color:#111;padding:.1rem .4rem;border-radius:4px}}
#viewer{{position:fixed;inset:0;z-index:3;display:flex;flex-direction:column;background:#000}}#viewer[hidden]{{display:none}}
#viewer-bar{{display:flex;gap:1rem;align-items:center;padding:.5rem;background:#111}}#viewer-name{{flex:1;overflow-wrap:anywhere}}
#viewer-frame{{flex:1;min-height:0;display:flex;overflow:auto}}#viewer-frame img{{margin:auto;max-width:100%;max-height:100%;cursor:zoom-in}}
#viewer-frame.zoomed img{{max-width:none;max-height:none;cursor:zoom-out}}
</style></head><body><header><h1>Photo Cull Review</h1><p>{len(cards)} photos, {exported_count} exported. Select a photo to view it full screen. Mark decisions, then download feedback for a future run.</p>
<button id="download">Download feedback.csv</button></header><main class="grid">{"".join(cards)}</main>
<div id="viewer" hidden><div id="viewer-bar"><span id="viewer-name"></span><span>Click to zoom · ← → move · K keep · R reject · Esc close</span><button id="viewer-close">Close</button></div>
<div id="viewer-frame"><img id="viewer-image" alt=""></div></div>
<script>
const decisions={{}};
const csvCell=value=>'"'+String(value).replaceAll('"','""')+'"';
const cards=[...document.querySelectorAll('.card')];
const viewer=document.querySelector('#viewer'),frame=document.querySelector('#viewer-frame'),large=document.querySelector('#viewer-image'),caption=document.querySelector('#viewer-name');
let current=0;
function show(index){{current=(index+cards.length)%cards.length;const card=cards[current],image=card.querySelector('img'),decision=decisions[card.dataset.path];large.src=image.src;large.alt=image.alt;frame.classList.remove('zoomed');caption.textContent=(current+1)+' of '+cards.length+': '+image.alt+(decision?' ('+decision+')':'');viewer.hidden=false;}}
function closeViewer(){{viewer.hidden=true;cards[current].scrollIntoView({{block:'nearest'}});}}
function mark(card,decision){{decisions[card.dataset.path]=decision;card.classList.remove('keep','reject');card.classList.add(decision);if(!viewer.hidden&&card===cards[current])show(current);}}
cards.forEach((card,index)=>card.querySelector('img').onclick=()=>show(index));
document.querySelectorAll('.card button').forEach(button=>button.onclick=()=>mark(button.closest('.card'),button.dataset.decision));
// Zoom to the preview's full resolution, centred on the clicked point, to check focus.
large.onclick=event=>{{const box=large.getBoundingClientRect(),x=(event.clientX-box.left)/box.width,y=(event.clientY-box.top)/box.height;frame.classList.toggle('zoomed');if(frame.classList.contains('zoomed')){{frame.scrollLeft=x*large.naturalWidth-frame.clientWidth/2;frame.scrollTop=y*large.naturalHeight-frame.clientHeight/2;}}}};
document.querySelector('#viewer-close').onclick=closeViewer;
document.addEventListener('keydown',event=>{{if(viewer.hidden)return;const key=event.key.toLowerCase();if(key==='escape')closeViewer();else if(key==='arrowright')show(current+1);else if(key==='arrowleft')show(current-1);else if(key==='k')mark(cards[current],'keep');else if(key==='r')mark(cards[current],'reject');else return;event.preventDefault();}});
document.querySelector('#download').onclick=()=>{{let csv='file_path,decision\\n';for(const [path,decision] of Object.entries(decisions)){{csv+=csvCell(path)+','+decision+'\\n';}}const link=document.createElement('a');link.href=URL.createObjectURL(new Blob([csv],{{type:'text/csv'}}));link.download='feedback.csv';link.click();URL.revokeObjectURL(link.href);}};
</script></body></html>"""
    atomic_write_text(destination, document)
    return len(cards), failures
