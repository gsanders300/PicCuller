"""Filesystem discovery and safe export helpers for Photo Cull."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

RAW_EXTENSIONS = {
    ".3fr",
    ".arw",
    ".cr2",
    ".cr3",
    ".dng",
    ".erf",
    ".iiq",
    ".kdc",
    ".mos",
    ".mrw",
    ".nef",
    ".orf",
    ".pef",
    ".raf",
    ".rw2",
    ".sr2",
    ".srw",
    ".x3f",
}
STANDARD_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
ALL_IMAGE_EXTENSIONS = RAW_EXTENSIONS | STANDARD_EXTENSIONS

# Sidecars are sometimes named IMG_0001.xmp and sometimes IMG_0001.ARW.xmp.
COMPOUND_SIDECAR_EXTENSIONS = {".cos", ".dop", ".on1", ".pp3", ".xmp"}
GENERATED_DIRECTORY_PATTERN = re.compile(
    r"^(?:picks|run)_\d{8}_\d{6}(?:_\d{6})?$",
    re.IGNORECASE,
)
# Operating-system bookkeeping directories that hold deleted or derived copies.
# Culling those as if they were originals wastes inference and pollutes ranking.
SYSTEM_DIRECTORY_NAMES = {
    ".fseventsd",
    ".spotlight-v100",
    ".temporaryitems",
    ".trash",
    ".trashes",
    "$recycle.bin",
    "@eadir",
    "lost+found",
    "system volume information",
}
# AppleDouble companions appear whenever macOS writes to exFAT or FAT media. They
# carry an image extension but no image data, so they only ever fail to decode.
APPLEDOUBLE_PREFIX = "._"


class ExportCollisionError(RuntimeError):
    """Raised when two source files would occupy the same export path."""


@dataclass(frozen=True, slots=True)
class ExportItem:
    """A source file and its destination relative to the picks directory."""

    source: Path
    relative_destination: Path


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _is_generated_directory(path: Path, output_root: Path | None) -> bool:
    resolved = path.resolve()
    if output_root is not None and (
        resolved == output_root or _is_relative_to(resolved, output_root)
    ):
        return True
    if path.name.casefold() in SYSTEM_DIRECTORY_NAMES:
        return True
    return path.name == ".photo-cull" or bool(GENERATED_DIRECTORY_PATTERN.fullmatch(path.name))


def discover_image_files(
    folder: Path,
    output_root: Path | None = None,
    on_error: Callable[[OSError], None] | None = None,
) -> list[Path]:
    """Return supported images deterministically while pruning generated output.

    An unreadable directory is reported through `on_error` rather than silently
    omitted, so a permission problem cannot quietly shrink the candidate set.
    """
    folder = folder.resolve()
    output_root = output_root.resolve() if output_root is not None else None
    discovered: list[Path] = []

    def handle_error(error: OSError) -> None:
        if on_error is not None:
            on_error(error)

    for current_root, directory_names, file_names in os.walk(
        folder,
        followlinks=False,
        onerror=handle_error,
    ):
        current_path = Path(current_root)
        directory_names[:] = sorted(
            (
                name
                for name in directory_names
                if not _is_generated_directory(current_path / name, output_root)
            ),
            key=str.casefold,
        )
        for name in sorted(file_names, key=str.casefold):
            if name.startswith(APPLEDOUBLE_PREFIX):
                continue
            candidate = current_path / name
            if (
                candidate.suffix.casefold() in ALL_IMAGE_EXTENSIONS
                and candidate.is_file()
                and not candidate.is_symlink()
            ):
                discovered.append(candidate)

    return discovered


def logical_asset_stem(path: Path) -> str:
    """Normalize direct and compound sidecar names to one asset-family key."""
    stem_path = Path(path.stem)
    if (
        path.suffix.casefold() in COMPOUND_SIDECAR_EXTENSIONS
        and stem_path.suffix.casefold() in ALL_IMAGE_EXTENSIONS
    ):
        stem_path = Path(stem_path.stem)
    return stem_path.name.casefold()


def find_associated_files(file_path: Path) -> list[Path]:
    """Find all files in the same directory belonging to an image asset family."""
    family_key = logical_asset_stem(file_path)
    return sorted(
        (
            candidate
            for candidate in file_path.parent.iterdir()
            if candidate.is_file() and logical_asset_stem(candidate) == family_key
        ),
        key=lambda path: path.name.casefold(),
    )


def build_export_plan(
    input_root: Path,
    lead_files: Iterable[Path],
) -> list[ExportItem]:
    """Create a deduplicated, collision-checked plan preserving relative paths."""
    input_root = input_root.resolve(strict=True)
    parent_indexes: dict[Path, dict[str, list[Path]]] = {}
    sources_by_destination: dict[str, Path] = {}
    export_items: list[ExportItem] = []

    for lead_file in lead_files:
        lead_file = lead_file.resolve(strict=True)
        if not _is_relative_to(lead_file, input_root):
            raise ValueError(f"Selection is outside the input directory: {lead_file}")

        parent = lead_file.parent
        if parent not in parent_indexes:
            index: dict[str, list[Path]] = defaultdict(list)
            for candidate in parent.iterdir():
                if candidate.is_file() and not candidate.is_symlink():
                    index[logical_asset_stem(candidate)].append(candidate.resolve(strict=True))
            parent_indexes[parent] = index

        family = parent_indexes[parent][logical_asset_stem(lead_file)]
        for source in sorted(family, key=lambda path: path.name.casefold()):
            relative_destination = source.relative_to(input_root)
            # Windows destinations are case-insensitive. Treat all platforms that
            # way here so a plan prepared on macOS remains safe when moved.
            destination_key = relative_destination.as_posix().casefold()
            prior_source = sources_by_destination.get(destination_key)
            if prior_source is not None:
                if prior_source == source:
                    continue
                raise ExportCollisionError(
                    "Export collision between "
                    f"'{prior_source}' and '{source}' at '{relative_destination}'"
                )

            sources_by_destination[destination_key] = source
            export_items.append(ExportItem(source, relative_destination))

    return sorted(
        export_items,
        key=lambda item: item.relative_destination.as_posix().casefold(),
    )


def select_primary_images(image_files: Iterable[Path], preference: str) -> list[Path]:
    """Collapse paired RAW/JPEG assets while retaining their files for export."""
    files = list(image_files)
    if preference == "all":
        return files
    if preference not in {"raw", "jpeg"}:
        raise ValueError("Primary preference must be raw, jpeg, or all")

    grouped: dict[tuple[Path, str], list[Path]] = defaultdict(list)
    for path in files:
        grouped[(path.parent.resolve(), logical_asset_stem(path))].append(path)

    selected: list[Path] = []
    for family in grouped.values():
        preferred = [
            path
            for path in family
            if (path.suffix.casefold() in RAW_EXTENSIONS) == (preference == "raw")
        ]
        selected.append(min(preferred or family, key=lambda path: path.name.casefold()))
    return sorted(selected, key=lambda path: path.as_posix().casefold())


def copy_export_plan(picks_dir: Path, export_items: Iterable[ExportItem]) -> int:
    """Copy a plan without overwriting existing files; publish each file atomically."""
    export_items = list(export_items)
    required_bytes = sum(item.source.stat().st_size for item in export_items)
    free_bytes = shutil.disk_usage(picks_dir.parent).free
    if required_bytes > free_bytes:
        raise OSError(f"Export needs {required_bytes} bytes but only {free_bytes} bytes are free")

    copied_count = 0
    for item in export_items:
        destination = picks_dir / item.relative_destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite export file: {destination}")

        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".part",
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            shutil.copy2(item.source, temporary_path)
            if destination.exists():
                raise FileExistsError(f"Refusing to overwrite export file: {destination}")
            temporary_path.replace(destination)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        copied_count += 1

    return copied_count
