"""Portrait heuristics. Advisory only, but they feed the composite score."""

import unittest

import numpy as np

from advanced_analysis import SUBJECT_PROMPTS, analyze_portrait


class PortraitAnalysisTests(unittest.TestCase):
    def test_an_absent_detection_is_neutral_not_a_penalty(self) -> None:
        """A cascade miss says nothing about the photograph.

        Frontal Haar cascades fail on profiles, hats, sunglasses, and backlight.
        Returning a factor below one marked down images with no defect.
        """
        blank = np.full((240, 320, 3), 90, np.uint8)

        result = analyze_portrait(blank)

        self.assertEqual(result.face_count, 0)
        self.assertEqual(result.eye_factor, 1.0)
        # The observation is still reported, just not scored.
        self.assertEqual(result.warning, "No face detected")

    def test_noise_also_yields_a_neutral_factor(self) -> None:
        noise = np.random.default_rng(2).integers(0, 256, (240, 320, 3), dtype=np.uint8)

        result = analyze_portrait(noise)

        if result.face_count == 0:
            self.assertEqual(result.eye_factor, 1.0)
        else:  # pragma: no cover - the cascade rarely fires on noise
            self.assertLessEqual(result.eye_factor, 1.0)

    def test_the_factor_never_leaves_the_unit_interval(self) -> None:
        for value in (0, 128, 255):
            with self.subTest(value=value):
                result = analyze_portrait(np.full((200, 200, 3), value, np.uint8))
                self.assertGreaterEqual(result.eye_factor, 0.0)
                self.assertLessEqual(result.eye_factor, 1.0)


class SubjectPromptTests(unittest.TestCase):
    def test_every_prompt_set_is_a_positive_and_negative_pair(self) -> None:
        for preset, prompts in SUBJECT_PROMPTS.items():
            with self.subTest(preset=preset):
                self.assertEqual(len(prompts), 2)
                self.assertNotEqual(prompts[0], prompts[1])

    def test_every_prompted_preset_actually_weights_the_score(self) -> None:
        """A preset that computes subject integrity must not discard it."""
        from scoring import SCORING_PROFILES

        for preset in SUBJECT_PROMPTS:
            with self.subTest(preset=preset):
                self.assertGreater(
                    SCORING_PROFILES[preset].subject_weight,
                    0.0,
                    f"{preset} pays for a CLIP subject score and then ignores it",
                )


if __name__ == "__main__":
    unittest.main()
