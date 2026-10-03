import unittest
from datetime import UTC, datetime, timedelta

from scoring import (
    SCORE_MULTIPLIER_FIELDS,
    SCORING_PROFILES,
    _ordinal,
    assign_session_focus_factors,
    calculate_composite_score,
    describe_score_reasons,
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
        "file_name": name,
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
        # Factors 1.0 and 0.75, under the balanced absolute focus weight of 0.5.
        self.assertAlmostEqual(sharp_score / blurred_score, (4.0 / 3.0) ** 0.5)

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


def ranked(records: list[dict], **grouping) -> list[dict]:
    """Group, score, and rank records the way the pipeline does before reasons."""
    assign_session_focus_factors(records)
    group_bursts(records, **grouping)
    by_burst: dict[int, list[dict]] = {}
    for item in records:
        by_burst.setdefault(item["burst_id"], []).append(item)
    for group in by_burst.values():
        group.sort(key=lambda item: (-item["composite_score"], item["file_path"]))
        for rank, item in enumerate(group, start=1):
            item["burst_rank"] = rank
    return records


def by_name(records: list[dict]) -> dict[str, dict]:
    return {item["file_path"]: item for item in records}


class ScoreMultiplierTests(unittest.TestCase):
    def test_refactored_composite_matches_the_original_formula_exactly(self) -> None:
        item = record("any.jpg", 10.0)
        item.update(
            absolute_focus_factor=0.83,
            musiq_score=64.2,
            exposure_penalty=0.77,
            eye_factor=0.7,
            subject_integrity=0.61,
        )
        for profile in SCORING_PROFILES.values():
            with self.subTest(profile=profile.name):
                original = (
                    7.0
                    * 0.83**profile.absolute_focus_weight
                    * 0.4**profile.relative_focus_exponent
                    * 0.642**profile.musiq_weight
                    * 0.77**profile.exposure_weight
                    * 0.7**profile.eye_weight
                    * 0.61**profile.subject_weight
                )
                self.assertEqual(calculate_composite_score(item, 0.4, profile), original)

    def test_the_aesthetic_score_times_the_multipliers_is_the_composite(self) -> None:
        timestamp = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
        records = [
            record("a.jpg", 40.0, timestamp),
            record("b.jpg", 10.0, timestamp + timedelta(seconds=1)),
        ]
        records[1]["exposure_penalty"] = 0.6

        for item in ranked(records, profile=get_scoring_profile("wildlife")):
            product = float(item["aesthetic_score"])
            for field in SCORE_MULTIPLIER_FIELDS:
                product *= item[field]
            self.assertEqual(product, item["composite_score"])


class ScoreReasonTests(unittest.TestCase):
    def burst(self, *focus_values: float) -> dict[str, dict]:
        timestamp = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
        records = ranked(
            [
                record(f"{index}.jpg", focus, timestamp + timedelta(seconds=index))
                for index, focus in enumerate(focus_values)
            ]
        )
        describe_score_reasons(records)
        return by_name(records)

    def test_a_winner_names_the_runner_up_the_gap_and_the_main_factor(self) -> None:
        reasons = self.burst(100.0, 80.0, 20.0)

        self.assertTrue(
            reasons["0.jpg"]["score_reason"].startswith(
                "best of 3 in burst; next best 1.jpg scored 35% lower, mainly on sharpness"
            )
        )

    def test_a_loser_names_the_winner_and_why_it_lost(self) -> None:
        reasons = self.burst(100.0, 80.0, 20.0)

        self.assertTrue(
            reasons["2.jpg"]["score_reason"].startswith(
                "3rd of 3 in burst; scored 93% lower than 0.jpg, mainly on sharpness"
            )
        )

    def test_the_main_factor_can_be_the_aesthetic_score(self) -> None:
        timestamp = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
        records = [record("a.jpg", 50.0, timestamp), record("b.jpg", 50.0, timestamp)]
        records[1]["aesthetic_score"] = 5.0
        describe_score_reasons(ranked(records))

        self.assertIn("mainly on aesthetic", by_name(records)["a.jpg"]["score_reason"])

    def test_equal_scores_are_explained_as_path_order(self) -> None:
        reasons = self.burst(50.0, 50.0)

        self.assertTrue(
            reasons["0.jpg"]["score_reason"].startswith(
                "best of 2 in burst; ties 1.jpg on score and wins on path order"
            )
        )
        self.assertTrue(
            reasons["1.jpg"]["score_reason"].startswith(
                "2nd of 2 in burst; ties 0.jpg on score and loses on path order"
            )
        )

    def test_gap_wording_at_the_extremes(self) -> None:
        self.assertIn("scored 100% lower", self.burst(100.0, 0.0)["0.jpg"]["score_reason"])
        self.assertIn(
            "scored more than 99% lower", self.burst(100.0, 1.0)["0.jpg"]["score_reason"]
        )
        timestamp = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
        records = [record("a.jpg", 50.0, timestamp), record("b.jpg", 50.0, timestamp)]
        records[1]["aesthetic_score"] = 6.99
        describe_score_reasons(ranked(records))
        self.assertIn("scored less than 1% lower", by_name(records)["a.jpg"]["score_reason"])

    def test_a_lone_frame_is_a_single_shot(self) -> None:
        records = ranked([record("only.jpg", 10.0)])
        describe_score_reasons(records)

        self.assertEqual(records[0]["score_reason"], "single shot")

    def test_disabled_grouping_omits_the_burst_clause(self) -> None:
        records = ranked([record("only.jpg", 10.0)])
        describe_score_reasons(records, grouped=False)

        self.assertEqual(records[0]["score_reason"], "no standout strength or penalty")

    def test_standing_is_named_only_in_the_top_or_bottom_quarter(self) -> None:
        timestamp = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
        records = [
            record(f"{index}.jpg", 10.0, timestamp + timedelta(minutes=index))
            for index in range(4)
        ]
        for index, item in enumerate(records):
            item["aesthetic_score"] = 4.0 + index
        describe_score_reasons(ranked(records))
        reasons = by_name(records)

        self.assertIn("aesthetic in top 25% of all photos", reasons["3.jpg"]["score_reason"])
        self.assertIn("aesthetic in bottom 25% of all photos", reasons["0.jpg"]["score_reason"])
        self.assertEqual(reasons["1.jpg"]["score_reason"], "single shot")

    def test_large_penalties_are_named_and_small_ones_are_not(self) -> None:
        timestamp = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
        clipped = record("clipped.jpg", 10.0, timestamp)
        clipped.update(exposure_penalty=0.8, blown_pct=6.0, crushed_pct=0.0)
        mild = record("mild.jpg", 10.0, timestamp + timedelta(minutes=1))
        mild.update(exposure_penalty=0.96, blown_pct=2.8, crushed_pct=0.0)
        records = ranked([clipped, mild])
        describe_score_reasons(records)
        reasons = by_name(records)

        self.assertIn(
            "clipping (-20%): 6.0% highlights blown, 0.0% shadows crushed",
            reasons["clipped.jpg"]["score_reason"],
        )
        self.assertNotIn("clipping", reasons["mild.jpg"]["score_reason"])

    def test_portrait_eye_warnings_are_reported(self) -> None:
        timestamp = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
        closed = record("closed.jpg", 10.0, timestamp)
        closed.update(eye_factor=0.7, eye_warning="Possible closed/obscured eyes")
        faceless = record("faceless.jpg", 10.0, timestamp + timedelta(minutes=1))
        faceless.update(eye_factor=1.0, eye_warning="No face detected")
        records = ranked([closed, faceless], profile=get_scoring_profile("portrait"))
        describe_score_reasons(records)
        reasons = by_name(records)

        self.assertIn("possible closed/obscured eyes (-12%)", reasons["closed.jpg"]["score_reason"])
        self.assertIn("no face detected", reasons["faceless.jpg"]["score_reason"])
        self.assertNotIn("(-", reasons["faceless.jpg"]["score_reason"])

    def test_reasons_are_deterministic(self) -> None:
        first = {name: item["score_reason"] for name, item in self.burst(9.0, 7.0, 7.0).items()}
        second = {name: item["score_reason"] for name, item in self.burst(9.0, 7.0, 7.0).items()}

        self.assertEqual(first, second)

    def test_ordinals(self) -> None:
        expected = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 11: "11th", 12: "12th", 13: "13th"}
        expected.update({21: "21st", 22: "22nd", 111: "111th", 102: "102nd"})
        for number, text in expected.items():
            with self.subTest(number=number):
                self.assertEqual(_ordinal(number), text)


if __name__ == "__main__":
    unittest.main()
