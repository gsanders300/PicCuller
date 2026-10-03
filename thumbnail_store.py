"""Review thumbnails shared by every run over a collection."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from time import perf_counter

from evaluation_cache import relative_key

# Large enough to judge focus and expression in the review page's full-window view.
MAX_THUMBNAIL_DIMENSION = 2048
# Roughly 0.5 to 1 MB per entry, so the default bounds the shared store near 2 GB.
THUMBNAIL_STORE_LIMIT = 2000


class ThumbnailStore:
    """Content-addressed review thumbnails, decoded only on a miss.

    Source files are read-only and unchanged between runs, so a repeat pass over
    the same shoot regenerates byte-identical previews. Reusing them removes the
    last decode from an otherwise fully cached run.
    """

    def __init__(self, root: Path, source_root: Path):
        self.root = root
        self.source_root = source_root
        self.decoded = 0
        self.decode_seconds = 0.0

    def thumbnail(self, source: Path) -> Path:
        """Return a ready thumbnail for a source image, decoding it only on a miss."""
        from image_loader import load_image

        target = self.root / f"{self._key(source)}.jpg"
        if target.exists():
            return target

        started = perf_counter()
        self.root.mkdir(parents=True, exist_ok=True)
        image = load_image(source, max_dim=MAX_THUMBNAIL_DIMENSION).pil_image
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.root,
            prefix=f".{target.name}.",
            suffix=".part",
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            image.save(temporary_path, "JPEG", quality=86, optimize=True)
            temporary_path.replace(target)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        self.decoded += 1
        self.decode_seconds += perf_counter() - started
        return target

    def prune(self, keep: int = THUMBNAIL_STORE_LIMIT) -> int:
        """Bound the store, discarding the least recently modified entries."""
        if not self.root.is_dir():
            return 0
        entries = sorted(
            (path for path in self.root.glob("*.jpg") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        removed = 0
        for path in entries[keep:]:
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def _key(self, source: Path) -> str:
        """Content identity for a review thumbnail.

        The collection-relative path keeps the key stable when the collection moves,
        matching the evaluation cache. Size and nanosecond modification time make an
        edited file a different thumbnail.
        """
        stat = source.stat()
        raw = (
            f"{relative_key(source, self.source_root)}|{stat.st_size}"
            f"|{stat.st_mtime_ns}|{MAX_THUMBNAIL_DIMENSION}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
