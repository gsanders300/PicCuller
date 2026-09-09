"""Decode path: draft scaling, orientation, and size bounds.

This module had no test file, yet it owns the "EXIF orientation is applied
before every image metric" and single-decode invariants. Synthetic JPEG and TIFF
fixtures need no network and no model download.
"""

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from image_loader import draft_box, load_image

MAX_DIM = 1024


def written_jpeg(directory: Path, width: int, height: int, name: str = "frame.jpg") -> Path:
    path = directory / name
    pixels = np.random.default_rng(4).integers(0, 256, (height, width, 3), dtype=np.uint8)
    Image.fromarray(pixels).save(path, quality=88)
    return path


class DraftBoxTests(unittest.TestCase):
    def test_box_follows_the_aspect_ratio(self) -> None:
        self.assertEqual(draft_box((1200, 800), 300), (300, 200))
        self.assertEqual(draft_box((800, 1200), 300), (200, 300))
        self.assertEqual(draft_box((1000, 1000), 300), (300, 300))

    def test_a_square_box_is_not_used_for_a_non_square_image(self) -> None:
        """The square box measured the short side and lost a DCT step."""
        self.assertNotEqual(draft_box((1200, 800), 300), (300, 300))

    def test_extreme_panoramas_keep_a_positive_box(self) -> None:
        for size in ((20000, 500), (500, 20000), (12000, 1)):
            with self.subTest(size=size):
                width, height = draft_box(size, 1024)
                self.assertGreaterEqual(width, 1)
                self.assertGreaterEqual(height, 1)

    def test_degenerate_sizes_fall_back_to_a_square_box(self) -> None:
        self.assertEqual(draft_box((0, 0), 512), (512, 512))
        self.assertEqual(draft_box((-1, 100), 512), (512, 512))

    def test_the_box_selects_a_coarser_dct_scale_than_a_square_box(self) -> None:
        # Pillow picks scale = min(width // box[0], height // box[1]).
        for width, height in ((6000, 4000), (3000, 2000), (2000, 3000)):
            with self.subTest(size=f"{width}x{height}"):
                square = min(width // MAX_DIM, height // MAX_DIM)
                box = draft_box((width, height), MAX_DIM)
                aspect = min(width // box[0], height // box[1])
                self.assertGreater(aspect, square)


class LoadImageTests(unittest.TestCase):
    def test_output_is_bounded_by_max_dim_and_keeps_aspect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            loaded = load_image(written_jpeg(root, 3000, 2000), max_dim=MAX_DIM)

            self.assertEqual(loaded.pil_image.size, (1024, 683))
            self.assertLessEqual(max(loaded.pil_image.size), MAX_DIM)

    def test_a_small_image_is_never_upscaled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            loaded = load_image(written_jpeg(root, 320, 240), max_dim=MAX_DIM)

            self.assertEqual(loaded.pil_image.size, (320, 240))

    def test_cv_and_pil_views_agree_in_shape_and_channel_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            path = root / "red.jpg"
            Image.new("RGB", (64, 48), (255, 0, 0)).save(path, quality=95)

            loaded = load_image(path, max_dim=MAX_DIM)
            width, height = loaded.pil_image.size

            self.assertEqual(loaded.cv_image.shape, (height, width, 3))
            # OpenCV order is BGR, so a red frame is high in the last channel.
            self.assertGreater(int(loaded.cv_image[0, 0, 2]), 200)
            self.assertLess(int(loaded.cv_image[0, 0, 0]), 60)

    def test_exif_orientation_is_applied_before_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            path = root / "rotated.jpg"
            exif = Image.Exif()
            exif[0x0112] = 6  # rotate 90 degrees clockwise on display
            Image.new("RGB", (400, 200), "green").save(path, quality=90, exif=exif)

            loaded = load_image(path, max_dim=MAX_DIM)

            # A landscape frame tagged for rotation must load as portrait.
            self.assertEqual(loaded.pil_image.size, (200, 400))

    def test_a_png_without_draft_support_still_loads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            path = root / "frame.png"
            Image.new("RGB", (2000, 1000), "blue").save(path)

            loaded = load_image(path, max_dim=MAX_DIM)

            self.assertEqual(loaded.pil_image.size, (1024, 512))

    def test_a_palette_image_is_converted_to_rgb(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            path = root / "palette.png"
            Image.new("P", (300, 200)).save(path)

            loaded = load_image(path, max_dim=MAX_DIM)

            self.assertEqual(loaded.pil_image.mode, "RGB")
            self.assertEqual(loaded.cv_image.shape[2], 3)

    def test_metadata_is_returned_from_the_same_decode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            loaded = load_image(written_jpeg(root, 640, 480), max_dim=MAX_DIM)

            self.assertIsNotNone(loaded.metadata.capture_time)
            self.assertIsNotNone(loaded.metadata.capture_time.tzinfo)
            self.assertEqual(loaded.metadata.timestamp_source, "filesystem_mtime")


if __name__ == "__main__":
    unittest.main()
