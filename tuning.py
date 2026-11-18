"""Tune the selection after evaluation: mode, target, weights, diversity, and bursts.

Evaluation is the slow part of a run, and its metrics are already in memory when the
export question comes. Every setting here only regroups, rescores, or reselects those
metrics, so a change redraws in well under a second. Nothing here loads a model. The
one decode is the preview, which reuses the shared thumbnail store.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from file_ops import logical_asset_stem
from scoring import SCORING_PROFILES, ScoringProfile

MODES = ("bursts", "photos")
BURST_LEVELS = ("tighter", "normal", "looser")
MAX_WEIGHT = 3.0
# Photos on each side of the cut that a preview shows and the background warms.
PREVIEW_LIMIT = 25


@dataclass(frozen=True, slots=True)
class Slider:
    name: str
    field: str
    description: str


SLIDERS = (
    Slider(
        "appeal",
        "aesthetic_weight",
        "Visual appeal from the aesthetic model. 0 ranks on the technical factors alone.",
    ),
    Slider("sharpness", "absolute_focus_weight", "Sharpness compared with the whole shoot."),
    Slider(
        "burst-sharpness",
        "relative_focus_exponent",
        "Sharpness compared with the sharpest frame of the same burst.",
    ),
    Slider("quality", "musiq_weight", "Technical quality from MUSIQ: noise, blur, compression."),
    Slider("exposure", "exposure_weight", "Penalty for blown highlights and crushed shadows."),
    Slider(
        "eyes",
        "eye_weight",
        "Penalty for likely closed or hidden eyes. Has data only with --preset portrait.",
    ),
    Slider(
        "subject",
        "subject_weight",
        "Whether the subject is whole and well framed, using the --preset prompts.",
    ),
)
SLIDER_BY_NAME = {slider.name: slider for slider in SLIDERS}


@dataclass(frozen=True, slots=True)
class BurstRules:
    window: float
    duration: float
    phash_threshold: int
    sim_threshold: float


def burst_rules(base: BurstRules, level: str) -> BurstRules:
    """Scale the configured burst limits together.

    `looser` doubles both time limits and relaxes both look-alike tests, and `tighter`
    does the reverse. The steps are uncalibrated, like the defaults they scale.
    """
    if level == "normal":
        return base
    if level == "looser":
        return BurstRules(
            base.window * 2.0,
            base.duration * 2.0,
            base.phash_threshold + 2,
            max(0.0, base.sim_threshold - 0.04),
        )
    if level == "tighter":
        return BurstRules(
            base.window / 2.0,
            base.duration / 2.0,
            max(0, base.phash_threshold - 2),
            min(1.0, base.sim_threshold + 0.04),
        )
    raise ValueError(f"Burst setting must be one of: {', '.join(BURST_LEVELS)}")


def parse_target(text: str) -> str:
    """Normalize a count, a percentage, `all`, or `none`."""
    normalized = text.strip().casefold()
    if normalized in {"", "none", "skip", "0", "0%"}:
        return "none"
    if normalized == "all":
        return "all"
    if normalized.endswith("%"):
        try:
            percent = float(normalized[:-1].strip())
        except ValueError:
            raise ValueError(f"{text.strip()!r} is not a percentage") from None
        if not math.isfinite(percent) or not 0.0 < percent <= 100.0:
            raise ValueError("A percentage must be above 0 and at most 100")
        return f"{percent:g}%"
    try:
        count = int(normalized)
    except ValueError:
        raise ValueError(
            "The target must be a count, a percentage such as 10%, 'all', or 'none'"
        ) from None
    if count < 0:
        raise ValueError("The target cannot be negative")
    return str(count)


def resolve_target(target: str, base: int) -> tuple[int, bool]:
    """Return the requested count, and whether a count above `base` was capped."""
    if target == "none":
        return 0, False
    if target == "all":
        return base, False
    if target.endswith("%"):
        if base == 0:
            return 0, False
        count = max(1, int(base * float(target[:-1]) / 100.0 + 0.5))
        return min(count, base), False
    count = int(target)
    return min(count, base), count > base


def parse_weight(text: str) -> tuple[str, float]:
    """Read one `NAME=VALUE` weight override, as given to `--weight`."""
    name, separator, value = text.partition("=")
    if not separator:
        raise ValueError(f"--weight must look like NAME=VALUE, not {text!r}")
    return name.strip().casefold(), _weight_value(name.strip().casefold(), value)


def _weight_value(name: str, value: str) -> float:
    if name not in SLIDER_BY_NAME:
        raise ValueError(f"Unknown weight {name!r}; choose from {', '.join(SLIDER_BY_NAME)}")
    try:
        number = float(value)
    except ValueError:
        raise ValueError(f"The {name} weight must be a number, not {value.strip()!r}") from None
    if not math.isfinite(number) or not 0.0 <= number <= MAX_WEIGHT:
        raise ValueError(f"The {name} weight must be from 0 to {MAX_WEIGHT:g}")
    return number


def apply_weights(profile: ScoringProfile, weights: Iterable[tuple[str, float]]) -> ScoringProfile:
    """Return `profile` with each named slider weight replaced."""
    changes = {SLIDER_BY_NAME[name].field: value for name, value in weights}
    return replace(profile, **changes) if changes else profile


@dataclass(frozen=True, slots=True)
class TuningSettings:
    mode: str
    target: str
    profile: ScoringProfile
    diversity: float
    burst_level: str = "normal"

    def weights(self) -> dict[str, float]:
        return {slider.name: getattr(self.profile, slider.field) for slider in SLIDERS}

    def audit_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "target": self.target,
            "starting_preset": self.profile.name,
            "weights": self.weights(),
            "diversity": self.diversity,
            "burst_level": self.burst_level,
        }


@dataclass(frozen=True, slots=True)
class Selection:
    """The result of ranking and selecting under one set of settings."""

    selected: tuple[dict[str, Any], ...]
    pool: tuple[dict[str, Any], ...]
    base: int
    photo_count: int
    requested: int
    capped: bool
    keep_paths: frozenset[str]

    @property
    def selected_paths(self) -> frozenset[str]:
        return frozenset(str(record["file_path"]) for record in self.selected)


@dataclass(frozen=True, slots=True)
class Command:
    action: str
    settings: TuningSettings | None = None


def parse_command(text: str, settings: TuningSettings) -> Command:
    """Read one line typed at the tuning prompt."""
    words = text.strip().casefold().split()
    if not words:
        return Command("export")
    head, arguments = words[0], words[1:]
    if head in {"help", "?"}:
        return Command("help")
    if head == "settings":
        return Command("settings")
    if head == "show":
        return Command("show")
    if head in SLIDER_BY_NAME or head in {"mode", "bursts", "start", "diversity", "target"}:
        if len(arguments) != 1:
            raise ValueError(f"Type '{head}' followed by one value")
        value = arguments[0]
        if head == "mode":
            if value not in MODES:
                raise ValueError(f"Mode must be one of: {', '.join(MODES)}")
            return Command("set", replace(settings, mode=value))
        if head == "bursts":
            if value not in BURST_LEVELS:
                raise ValueError(f"Bursts must be one of: {', '.join(BURST_LEVELS)}")
            return Command("set", replace(settings, burst_level=value))
        if head == "start":
            if value not in SCORING_PROFILES:
                raise ValueError(f"Start from one of: {', '.join(sorted(SCORING_PROFILES))}")
            return Command("set", replace(settings, profile=SCORING_PROFILES[value]))
        if head == "diversity":
            try:
                diversity = float(value)
            except ValueError:
                raise ValueError("Diversity must be a number from 0 to 1") from None
            if not math.isfinite(diversity) or not 0.0 <= diversity <= 1.0:
                raise ValueError("Diversity must be from 0 to 1")
            return Command("set", replace(settings, diversity=diversity))
        if head == "target":
            return Command("set", replace(settings, target=parse_target(value)))
        weight = _weight_value(head, value)
        profile = apply_weights(settings.profile, [(head, weight)])
        return Command("set", replace(settings, profile=profile))
    return Command("set", replace(settings, target=parse_target(text)))


class FamilySizes:
    """Count the files and bytes an export of some photos would copy.

    It applies the export plan's family rule (same directory, same logical stem,
    regular files only) once per directory, so a redraw costs no file-system calls.
    The figure is for display; the export plan stays the authority.
    """

    def __init__(self, paths: Iterable[str]):
        by_parent: dict[Path, dict[str, list[tuple[Path, int]]]] = {}
        self._families: dict[str, tuple[tuple[Path, int], ...]] = {}
        for text in paths:
            path = Path(text)
            index = by_parent.get(path.parent)
            if index is None:
                index = defaultdict(list)
                try:
                    for candidate in path.parent.iterdir():
                        if candidate.is_file() and not candidate.is_symlink():
                            index[logical_asset_stem(candidate)].append(
                                (candidate, candidate.stat().st_size)
                            )
                except OSError:
                    pass
                by_parent[path.parent] = index
            self._families[text] = tuple(index.get(logical_asset_stem(path), ()))

    def total(self, paths: Iterable[str]) -> tuple[int, int]:
        members = {member for text in paths for member in self._families.get(text, ())}
        return len(members), sum(size for _, size in members)


def open_folder(path: Path) -> None:
    """Show a folder in Explorer, Finder, or the desktop's file manager."""
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=True)
    else:
        subprocess.run(["xdg-open", str(path)], check=True)


class Preview:
    """Copy thumbnails of photos near the cut into `preview/in` and `preview/out`.

    Thumbnails come from the shared store, so a photo already in it costs a file
    copy. `warm` decodes the photos nearest the cut in the background while you
    read the summary, so a later `show` usually decodes nothing. The folders hold
    copies of review thumbnails, never links to originals: editing a linked
    original in a photo viewer would modify the source.
    """

    def __init__(
        self,
        root: Path,
        thumbnail: Callable[[Path], Path],
        workers: int,
        opener: Callable[[Path], None] = open_folder,
    ):
        self.root = root
        self._thumbnail = thumbnail
        self._opener = opener
        self._executor = ThreadPoolExecutor(max_workers=max(1, workers))
        self._futures: dict[str, Future[Path]] = {}

    def warm(self, paths: Iterable[str]) -> None:
        for path in paths:
            self._submit(path)

    def write(self, entered: list[str], left: list[str]) -> list[tuple[str, BaseException]]:
        """Replace the preview folders. Return the photos whose thumbnail failed."""
        if self.root.exists():
            shutil.rmtree(self.root)
        failures: list[tuple[str, BaseException]] = []
        for name, paths in (("in", entered), ("out", left)):
            folder = self.root / name
            folder.mkdir(parents=True)
            for position, path in enumerate(paths, start=1):
                try:
                    thumbnail = self._submit(path).result()
                except Exception as error:
                    failures.append((path, error))
                    continue
                shutil.copyfile(thumbnail, folder / f"{position:03d}_{Path(path).stem}.jpg")
        return failures

    def open(self) -> None:
        self._opener(self.root)

    def close(self) -> None:
        """Cancel queued decodes and wait only for the ones already running."""
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _submit(self, path: str) -> Future[Path]:
        future = self._futures.get(path)
        if future is None:
            future = self._executor.submit(self._thumbnail, Path(path))
            self._futures[path] = future
        return future


def boundary(selection: Selection, limit: int = PREVIEW_LIMIT) -> tuple[list[str], list[str]]:
    """Return the lowest selected and highest unselected photos, nearest the cut first."""
    chosen = selection.selected_paths
    ordered = sorted(
        selection.pool,
        key=lambda record: (-float(record["composite_score"]), str(record["file_path"]).casefold()),
    )
    paths = [str(record["file_path"]) for record in ordered]
    inside = [
        path for path in reversed(paths) if path in chosen and path not in selection.keep_paths
    ]
    outside = [path for path in paths if path not in chosen]
    return inside[:limit], outside[:limit]


def changes(
    previous: Selection | None,
    current: Selection,
    score_of: Callable[[str], float],
    limit: int = PREVIEW_LIMIT,
) -> tuple[list[str], list[str], int, int]:
    """Return the photos that entered and left, nearest the cut first, and both totals."""
    if previous is None:
        return [], [], 0, 0
    entered = sorted(
        current.selected_paths - previous.selected_paths,
        key=lambda path: (score_of(path), path.casefold()),
    )
    left = sorted(
        previous.selected_paths - current.selected_paths,
        key=lambda path: (-score_of(path), path.casefold()),
    )
    return entered[:limit], left[:limit], len(entered), len(left)


PROMPT = (
    "[bold yellow]Type a change, 'show', or 'help' (Enter on an empty line exports): "
    "[/bold yellow]"
)


def run_tuning_loop(
    console: Any,
    settings: TuningSettings,
    records: list[dict[str, Any]],
    evaluate: Callable[[TuningSettings], Selection],
    read_line: Callable[[str], str],
    sizes: FamilySizes,
    preview: Preview | None,
    warn: Callable[[str], None],
    *,
    grouped: bool = True,
) -> tuple[TuningSettings, Selection]:
    """Redraw the selection after each change until Enter. Return the final state.

    End of input means `none`, as at the old count prompt, so a closed terminal
    never exports by accident. Feedback keeps still apply.
    """
    by_path = {str(record["file_path"]): record for record in records}

    def score_of(path: str) -> float:
        return float(by_path[path]["composite_score"])

    current = evaluate(settings)
    console.print(settings_table(settings))
    console.print(help_text())
    console.print(summary_text(settings, current, sizes, None))
    last_change: tuple[list[str], list[str], int, int] | None = None
    _warm(preview, current, last_change)
    while True:
        try:
            line = read_line(PROMPT)
        except EOFError:
            settings = replace(settings, target="none")
            return settings, evaluate(settings)
        try:
            command = parse_command(line, settings)
        except ValueError as error:
            warn(f"{error}. Type 'help' for the commands.")
            continue
        if command.action == "export":
            return settings, current
        if command.action == "help":
            console.print(help_text())
            continue
        if command.action == "settings":
            console.print(settings_table(settings))
            continue
        if command.action == "show":
            _show(console, preview, current, last_change, warn)
            continue
        assert command.settings is not None
        if command.settings.burst_level != settings.burst_level and not grouped:
            warn("Burst grouping is off (--no-group), so the bursts setting changes nothing.")
        previous, settings = current, command.settings
        current = evaluate(settings)
        last_change = changes(previous, current, score_of)
        console.print(summary_text(settings, current, sizes, last_change))
        _warm(preview, current, last_change)


def settings_table(settings: TuningSettings) -> Any:
    from rich.table import Table

    table = Table(
        title="Selection settings",
        caption=(
            f"Weights run from 0 to {MAX_WEIGHT:g}. 0 ignores a factor, and a higher weight "
            "makes it count more. Only the ratios between weights change the order."
        ),
        header_style="bold magenta",
    )
    table.add_column("Setting", no_wrap=True)
    table.add_column("Value", justify="right", no_wrap=True)
    table.add_column("What it does", overflow="fold")
    table.add_row(
        "mode",
        settings.mode,
        "bursts: each burst counts as 1 shot. photos: every photo counts.",
    )
    table.add_row("target", settings.target, "A count, a percentage, 'all', or 'none'.")
    weights = settings.weights()
    for slider in SLIDERS:
        table.add_row(slider.name, f"{weights[slider.name]:g}", slider.description)
    table.add_row("diversity", f"{settings.diversity:g}", "Prefer photos that look different.")
    table.add_row(
        "bursts",
        settings.burst_level,
        "How far apart frames of one burst can be: tighter, normal, or looser.",
    )
    return table


def summary_text(
    settings: TuningSettings,
    selection: Selection,
    sizes: FamilySizes,
    change: tuple[list[str], list[str], int, int] | None,
) -> str:
    unit = "bursts" if settings.mode == "bursts" else "photos"
    count = len(selection.selected)
    lines = [
        (
            f"[bold]{settings.mode} mode:[/bold] target {settings.target} of "
            f"{selection.base:,} {unit} (from {selection.photo_count:,} photos) selects "
            f"{count:,} {'photo' if count == 1 else 'photos'}."
        )
    ]
    details = []
    ranked = [
        float(record["composite_score"])
        for record in selection.selected
        if str(record["file_path"]) not in selection.keep_paths
    ]
    if ranked:
        details.append(f"lowest selected score {min(ranked):.2f}")
    if change is not None:
        details.append(f"last change: {change[2]} in, {change[3]} out")
    files, size = sizes.total(selection.selected_paths)
    if selection.selected:
        noun = "file" if files == 1 else "files"
        details.append(f"export copies {files:,} {noun}, {_format_size(size)}")
    if details:
        lines.append("  " + "; ".join(details) + ".")
    keeps = len(selection.keep_paths)
    if selection.capped:
        lines.append(
            f"  [yellow]Only {selection.base} {unit} exist, so the target is capped.[/yellow]"
        )
    if keeps > selection.requested:
        lines.append(
            f"  [yellow]{keeps} feedback keeps exceed the target of {selection.requested}; "
            "all of them export.[/yellow]"
        )
    elif len(selection.selected) < selection.requested:
        lines.append(
            f"  [yellow]Only {len(selection.selected)} photos are eligible after feedback "
            "rejects.[/yellow]"
        )
    return "\n".join(lines)


def _show(
    console: Any,
    preview: Preview | None,
    current: Selection,
    last_change: tuple[list[str], list[str], int, int] | None,
    warn: Callable[[str], None],
) -> None:
    if preview is None:
        warn("The preview needs the thumbnail store, which --contact-sheet 0 turns off.")
        return
    if last_change is not None and (last_change[0] or last_change[1]):
        entered, left, entered_total, left_total = last_change
        label = "photos that crossed the cut with your last change"
    else:
        entered, left = boundary(current)
        entered_total, left_total = len(entered), len(left)
        label = "photos on each side of the cut"
    from time import perf_counter

    from rich.markup import escape

    started = perf_counter()
    for path, error in preview.write(entered, left):
        warn(f"No preview thumbnail for {path}: {error}")
    try:
        preview.open()
    except Exception as error:
        warn(f"Could not open the preview folder: {error}")
    shown = f"{len(entered)} in, {len(left)} out"
    if (entered_total, left_total) != (len(entered), len(left)):
        shown += f" (nearest the cut, of {entered_total} in and {left_total} out)"
    console.print(
        f"Preview of the {label}: {shown}, ready in {perf_counter() - started:.1f} s at "
        f"{escape(str(preview.root))}"
    )


def _warm(
    preview: Preview | None,
    current: Selection,
    last_change: tuple[list[str], list[str], int, int] | None,
) -> None:
    if preview is None:
        return
    inside, outside = boundary(current)
    preview.warm(inside + outside)
    if last_change is not None:
        preview.warm(last_change[0] + last_change[1])


def _format_size(count: int) -> str:
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024.0 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    raise AssertionError("unreachable")


def help_text() -> str:
    return (
        "Type one change per line and press Enter. The summary redraws after each one.\n"
        "  60, 10%, all, none    how many to export\n"
        "  mode bursts|photos    bursts counts each burst once; photos counts every photo\n"
        "  bursts tighter|normal|looser\n"
        f"  <weight> 0-{MAX_WEIGHT:g}          {', '.join(SLIDER_BY_NAME)}\n"
        "  diversity 0-1         prefer photos that look different\n"
        f"  start <preset>        load the weights of {', '.join(sorted(SCORING_PROFILES))}\n"
        "  show                  open the photos near the cut in your file manager\n"
        "  settings              list every setting again\n"
        "Press Enter on an empty line to export with these settings."
    )
