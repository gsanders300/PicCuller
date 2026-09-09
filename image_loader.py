"""Single-pass, orientation-aware image and RAW-preview loading."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import tzinfo
from pathlib import Path

import cv2
import numpy as np
import rawpy
from PIL import Image, ImageOps

from file_ops import RAW_EXTENSIONS
from metadata_reader import CaptureMetadata, metadata_from_pillow


@dataclass(slots=True)
class LoadedImage:
    cv_image: np.ndarray
    pil_image: Image.Image
    metadata: CaptureMetadata


def load_image(
    file_path: Path,
    *,
    max_dim: int = 1024,
    metadata: CaptureMetadata | None = None,
    assumed_timezone: tzinfo | None = None,
) -> LoadedImage:
    """Decode pixels and fallback metadata in one pass."""
    if file_path.suffix.casefold() in RAW_EXTENSIONS:
        pil_image, embedded_metadata = _load_raw_preview(file_path, max_dim, assumed_timezone)
    else:
        pil_image, embedded_metadata = _load_standard_image(
            file_path,
            max_dim,
            assumed_timezone,
        )

    if max(pil_image.size) > max_dim:
        pil_image.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")

    rgb = np.asarray(pil_image)
    cv_image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return LoadedImage(
        cv_image=cv_image,
        pil_image=pil_image,
        metadata=metadata or embedded_metadata,
    )


def draft_box(size: tuple[int, int], max_dim: int) -> tuple[int, int]:
    """Return an aspect-correct box for Image.draft.

    Pillow chooses the JPEG DCT scale from whichever axis is most constrained,
    `min(width // box[0], height // box[1])`. A square box therefore measures the
    short side against `max_dim` and lands one power of two coarser than the
    longest-side target the caller actually wants. On a 3:2 frame the square box
    can select no reduction at all.
    """
    width, height = size
    if width <= 0 or height <= 0:
        return (max_dim, max_dim)
    if width >= height:
        return (max_dim, max(1, round(max_dim * height / width)))
    return (max(1, round(max_dim * width / height)), max_dim)


def _load_standard_image(
    file_path: Path,
    max_dim: int,
    assumed_timezone: tzinfo | None,
) -> tuple[Image.Image, CaptureMetadata]:
    with Image.open(file_path) as opened:
        capture_metadata = metadata_from_pillow(opened, file_path, assumed_timezone)
        if opened.format == "JPEG":
            opened.draft("RGB", draft_box(opened.size, max_dim))
        opened.load()
        oriented = ImageOps.exif_transpose(opened)
        return oriented.convert("RGB"), capture_metadata


def _load_raw_preview(
    file_path: Path,
    max_dim: int,
    assumed_timezone: tzinfo | None,
) -> tuple[Image.Image, CaptureMetadata]:
    with rawpy.imread(str(file_path)) as raw:
        try:
            thumbnail = raw.extract_thumb()
            if thumbnail.format == rawpy.ThumbFormat.JPEG:
                with Image.open(io.BytesIO(thumbnail.data)) as opened:
                    capture_metadata = metadata_from_pillow(
                        opened,
                        file_path,
                        assumed_timezone,
                    )
                    # An embedded preview is often full sensor resolution, so it
                    # benefits from the same draft. This must precede load(),
                    # where draft becomes a silent no-op.
                    opened.draft("RGB", draft_box(opened.size, max_dim))
                    opened.load()
                    oriented = ImageOps.exif_transpose(opened)
                    return oriented.convert("RGB"), capture_metadata
            pil_image = Image.fromarray(thumbnail.data).convert("RGB")
        except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
            rgb = raw.postprocess(half_size=True, use_camera_wb=True)
            pil_image = Image.fromarray(rgb).convert("RGB")

    return pil_image, metadata_from_pillow(pil_image, file_path, assumed_timezone)
