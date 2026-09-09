"""Focus and exposure metrics computed in cull.py.

Both feed the composite score directly, so a wrong value silently changes which
photograph wins a burst.
"""

import unittest

import cv2
import numpy as np

from cull import calculate_top_percentile_focus, check_exposure_clipping


def reference_tile_focus(cv_image, grid_size: int = 16, top_k_pct: float = 0.03) -> float:
    """The straightforward per-tile loop, kept as an oracle."""
    gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    if height // grid_size < 8 or width // grid_size < 8:
        return float(cv2.Laplacian(gray, cv2.CV_32F).var())
    laplacian = cv2.Laplacian(gray, cv2.CV_32F)
    scores = [
        float(patch.var())
        for row in np.array_split(laplacian, grid_size, axis=0)
        for patch in np.array_split(row, grid_size, axis=1)
    ]
    count = max(1, round(len(scores) * top_k_pct))
    return float(np.mean(sorted(scores, reverse=True)[:count]))


def noise(height: int, width: int, seed: int = 0):
    return np.random.default_rng(seed).integers(0, 256, (height, width, 3), dtype=np.uint8)


def gradient(height: int, width: int):
    rows, columns = np.mgrid[0:height, 0:width]
    plane = ((columns * 3 + rows * 2) % 256).astype(np.uint8)
    return np.stack([plane] * 3, axis=2)


class FocusTests(unittest.TestCase):
    def test_matches_the_reference_tile_loop(self) -> None:
        """The summed-area rewrite must not change any score."""
        cases = [(683, 1024), (768, 1024), (1024, 1024), (700, 1000), (1023, 769)]
        for height, width in cases:
            for name, image in (
                ("noise", noise(height, width)),
                ("gradient", gradient(height, width)),
                ("flat", np.full((height, width, 3), 128, np.uint8)),
            ):
                with self.subTest(size=f"{width}x{height}", content=name):
                    got = calculate_top_percentile_focus(image)
                    expected = reference_tile_focus(image)
                    self.assertAlmostEqual(got, expected, delta=max(abs(expected) * 1e-6, 1e-9))

    def test_uneven_tile_boundaries_keep_remainder_pixels(self) -> None:
        # 1023 and 769 divide unevenly by 16, so tiles differ in size and the
        # boundaries must match numpy.array_split exactly.
        image = noise(1023, 769, seed=3)

        self.assertAlmostEqual(
            calculate_top_percentile_focus(image),
            reference_tile_focus(image),
            delta=abs(reference_tile_focus(image)) * 1e-6,
        )

    def test_a_flat_image_has_no_focus_energy(self) -> None:
        self.assertEqual(calculate_top_percentile_focus(np.full((400, 400, 3), 200, np.uint8)), 0.0)

    def test_variance_is_never_negative(self) -> None:
        """Cancellation in the summed-area table must not produce a negative."""
        for value in (0, 1, 128, 254, 255):
            with self.subTest(value=value):
                image = np.full((300, 300, 3), value, np.uint8)
                self.assertGreaterEqual(calculate_top_percentile_focus(image), 0.0)

    def test_small_images_fall_back_to_a_full_frame_variance(self) -> None:
        image = noise(100, 100, seed=7)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        self.assertAlmostEqual(
            calculate_top_percentile_focus(image),
            float(cv2.Laplacian(gray, cv2.CV_32F).var()),
            places=3,
        )

    def test_a_sharp_image_scores_above_a_blurred_one(self) -> None:
        sharp = noise(683, 1024, seed=11)
        blurred = cv2.GaussianBlur(sharp, (0, 0), 3.0)

        self.assertGreater(
            calculate_top_percentile_focus(sharp),
            calculate_top_percentile_focus(blurred),
        )


class ExposureTests(unittest.TestCase):
    def test_a_saturated_channel_counts_as_blown(self) -> None:
        """The luminance conversion hid single-channel clipping entirely."""
        # BGR (50, 100, 255): a clipped red that converts to mid grey.
        sunset = np.zeros((100, 100, 3), np.uint8)
        sunset[:, :] = (50, 100, 255)
        gray = cv2.cvtColor(sunset, cv2.COLOR_BGR2GRAY)

        blown, _, _ = check_exposure_clipping(sunset)

        self.assertEqual(blown, 1.0)
        # Proof the old luminance measurement saw nothing at all.
        self.assertEqual(float(np.mean(gray >= 254)), 0.0)

    def test_pure_white_and_black_are_unchanged(self) -> None:
        white_blown, white_crushed, _ = check_exposure_clipping(
            np.full((50, 50, 3), 255, np.uint8)
        )
        black_blown, black_crushed, _ = check_exposure_clipping(np.zeros((50, 50, 3), np.uint8))

        self.assertEqual((white_blown, white_crushed), (1.0, 0.0))
        self.assertEqual((black_blown, black_crushed), (0.0, 1.0))

    def test_one_zero_channel_is_not_shadow_clipping(self) -> None:
        """A saturated colour has a zero channel and is not crushed."""
        saturated_blue = np.zeros((50, 50, 3), np.uint8)
        saturated_blue[:, :] = (255, 0, 0)

        blown, crushed, _ = check_exposure_clipping(saturated_blue)

        self.assertEqual(blown, 1.0)
        self.assertEqual(crushed, 0.0)

    def test_a_clean_frame_takes_no_penalty(self) -> None:
        _, _, penalty = check_exposure_clipping(np.full((50, 50, 3), 128, np.uint8))

        self.assertEqual(penalty, 1.0)

    def test_penalties_stay_within_their_documented_floors(self) -> None:
        _, _, blown_penalty = check_exposure_clipping(np.full((50, 50, 3), 255, np.uint8))
        _, _, crushed_penalty = check_exposure_clipping(np.zeros((50, 50, 3), np.uint8))

        self.assertAlmostEqual(blown_penalty, 0.4)
        self.assertAlmostEqual(crushed_penalty, 0.6)

    def test_a_greyscale_frame_is_accepted(self) -> None:
        """Defensive: a two-dimensional array must not raise."""
        blown, crushed, penalty = check_exposure_clipping(np.full((40, 40), 255, np.uint8))

        self.assertEqual(blown, 1.0)
        self.assertEqual(crushed, 0.0)
        self.assertAlmostEqual(penalty, 0.4)

    def test_partial_clipping_scales_the_penalty(self) -> None:
        image = np.full((100, 100, 3), 128, np.uint8)
        image[:20, :, 2] = 255  # 20 percent of pixels with a blown red channel

        blown, _, penalty = check_exposure_clipping(image)

        self.assertAlmostEqual(blown, 0.2)
        self.assertAlmostEqual(penalty, max(0.4, 1.0 - 5.0 * (0.2 - 0.02)))


if __name__ == "__main__":
    unittest.main()
