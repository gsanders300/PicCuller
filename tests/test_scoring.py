import unittest
from datetime import UTC, datetime, timedelta

from scoring import (
    assign_session_focus_factors,
    calculate_composite_score,
    get_scoring_profile,
    group_bursts,
)


def record(
    name: str,
    focus: float,
    timestamp: datetime | None = None,
    phash: int = 0,
) -> dict:
    return {
        "file_path": name,
        "timestamp": timestamp or datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC),
        "phash": phash,
        "embedding": (1.0, 0.0),
        "focus_score": focus,
        "musiq_score": 80.0,
        "exposure_penalty": 1.0,
        "aesthetic_score": 7.0,
        "camera_serial": "camera-a",
    }


class ScoringTests(unittest.TestCase):
    def test_single_image_receives_neutral_absolute_focus_factor(self) -> None:
        records = [record("only.jpg", 10.0)]

        assign_session_focus_factors(records)

        self.assertEqual(records[0]["focus_percentile"], 1.0)
        self.assertEqual(records[0]["absolute_focus_factor"], 1.0)

    def test_standalone_focus_changes_score(self) -> None:
        records = [record("blurred.jpg", 10.0), record("sharp.jpg", 100.0)]
        assign_session_focus_factors(records)

        blurred_score = calculate_composite_score(records[0])
        sharp_score = calculate_composite_score(records[1])

        self.assertLess(blurred_score, sharp_score)
        self.assertAlmostEqual(sharp_score / blurred_score, 4.0 / 3.0)

    def test_equal_focus_scores_receive_equal_percentiles(self) -> None:
        records = [record("a.jpg", 10.0), record("b.jpg", 10.0)]

        assign_session_focus_factors(records)

        self.assertEqual(records[0]["focus_percentile"], 0.75)
        self.assertEqual(records[1]["focus_percentile"], 0.75)

    def test_grouping_is_deterministic_when_timestamps_match(self) -> None:
        timestamp = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
        records = [record("b.jpg", 10.0, timestamp), record("a.jpg", 20.0, timestamp)]
        assign_session_focus_factors(records)

        grouped = group_bursts(records)

        self.assertEqual([item["file_path"] for item in grouped], ["a.jpg", "b.jpg"])
        self.assertEqual({item["burst_id"] for item in grouped}, {1})

    def test_time_gap_starts_a_new_burst(self) -> None:
        timestamp = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
        records = [
            record("a.jpg", 10.0, timestamp),
            record("b.jpg", 20.0, timestamp + timedelta(seconds=3)),
        ]
        assign_session_focus_factors(records)

        grouped = group_bursts(records, time_window_seconds=2.0)

        self.assertEqual([item["burst_id"] for item in grouped], [1, 2])

    def test_burst_duration_is_bounded_despite_adjacent_matches(self) -> None:
        timestamp = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
        records = [
            record(f"{index}.jpg", 10.0, timestamp + timedelta(seconds=index * 2))
            for index in range(7)
        ]
        assign_session_focus_factors(records)

        grouped = group_bursts(records, max_burst_duration_seconds=10.0)

        self.assertEqual([item["burst_id"] for item in grouped], [1, 1, 1, 1, 1, 1, 2])

    def test_known_different_cameras_do_not_share_a_burst(self) -> None:
        records = [record("a.jpg", 10.0), record("b.jpg", 10.0)]
        records[1]["camera_serial"] = "camera-b"
        assign_session_focus_factors(records)

        grouped = group_bursts(records)

        self.assertEqual([item["burst_id"] for item in grouped], [1, 2])

    def test_portrait_profile_uses_eye_factor(self) -> None:
        item = record("portrait.jpg", 10.0)
        item["absolute_focus_factor"] = 1.0
        item["eye_factor"] = 0.5

        balanced = calculate_composite_score(item, profile=get_scoring_profile("balanced"))
        portrait = calculate_composite_score(item, profile=get_scoring_profile("portrait"))

        self.assertLess(portrait, balanced)

    def test_landscape_profile_uses_subject_integrity(self) -> None:
        """Landscape defines CLIP prompts, so the score must reach the composite."""
        intact = record("wide.jpg", 10.0)
        intact["absolute_focus_factor"] = 1.0
        intact["subject_integrity"] = 0.95
        poor = dict(intact, subject_integrity=0.10)
        landscape = get_scoring_profile("landscape")

        self.assertLess(
            calculate_composite_score(poor, profile=landscape),
            calculate_composite_score(intact, profile=landscape),
        )

    def test_balanced_profile_ignores_subject_integrity(self) -> None:
        """Balanced defines no prompts, so the value is a constant it must not use."""
        intact = record("any.jpg", 10.0)
        intact["absolute_focus_factor"] = 1.0
        intact["subject_integrity"] = 0.95
        poor = dict(intact, subject_integrity=0.10)
        balanced = get_scoring_profile("balanced")

        self.assertEqual(
            calculate_composite_score(poor, profile=balanced),
            calculate_composite_score(intact, profile=balanced),
        )


if __name__ == "__main__":
    unittest.main()
