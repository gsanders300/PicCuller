from datetime import datetime, timedelta
import unittest

from scoring import (
    assign_session_focus_factors,
    calculate_composite_score,
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
        "timestamp": timestamp or datetime(2026, 9, 9, 12, 0, 0),
        "phash": phash,
        "embedding": (1.0, 0.0),
        "focus_score": focus,
        "musiq_score": 80.0,
        "exposure_penalty": 1.0,
        "aesthetic_score": 7.0,
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
        timestamp = datetime(2026, 9, 9, 12, 0, 0)
        records = [record("b.jpg", 10.0, timestamp), record("a.jpg", 20.0, timestamp)]
        assign_session_focus_factors(records)

        grouped = group_bursts(records)

        self.assertEqual([item["file_path"] for item in grouped], ["a.jpg", "b.jpg"])
        self.assertEqual({item["burst_id"] for item in grouped}, {1})

    def test_time_gap_starts_a_new_burst(self) -> None:
        timestamp = datetime(2026, 9, 9, 12, 0, 0)
        records = [
            record("a.jpg", 10.0, timestamp),
            record("b.jpg", 20.0, timestamp + timedelta(seconds=3)),
        ]
        assign_session_focus_factors(records)

        grouped = group_bursts(records, time_window_seconds=2.0)

        self.assertEqual([item["burst_id"] for item in grouped], [1, 2])


if __name__ == "__main__":
    unittest.main()
