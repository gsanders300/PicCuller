from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from validation import _common_input_root, evaluate_feedback


class ValidationTests(unittest.TestCase):
    def test_feedback_metrics_measure_rank_agreement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            evaluation = root / "evaluation.csv"
            feedback = root / "feedback.csv"
            with evaluation.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=("file_path", "composite_score", "global_quality_rank"),
                )
                writer.writeheader()
                writer.writerows(
                    (
                        {
                            "file_path": str(root / "a.jpg"),
                            "composite_score": 3,
                            "global_quality_rank": 1,
                        },
                        {
                            "file_path": str(root / "b.jpg"),
                            "composite_score": 2,
                            "global_quality_rank": 2,
                        },
                        {
                            "file_path": str(root / "c.jpg"),
                            "composite_score": 1,
                            "global_quality_rank": 3,
                        },
                    )
                )
            feedback.write_text(
                f"file_path,decision\n{root / 'a.jpg'},keep\n{root / 'c.jpg'},reject\n",
                encoding="utf-8",
            )

            result = evaluate_feedback(evaluation, feedback)

        self.assertEqual(result["precision_at_keep_count"], 1.0)
        self.assertEqual(result["pairwise_accuracy"], 1.0)
        self.assertEqual(result["mean_keep_global_rank"], 1.0)
        self.assertEqual(result["mean_reject_global_rank"], 3.0)


class CommonInputRootTests(unittest.TestCase):
    """The upward walk could not terminate: a filesystem root is its own parent."""

    def test_nested_paths_resolve_to_their_shared_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            (root / "day-one").mkdir()
            (root / "day-two").mkdir()
            rows = [
                {"file_path": str(root / "day-one" / "a.jpg")},
                {"file_path": str(root / "day-two" / "b.jpg")},
            ]

            self.assertEqual(_common_input_root(rows), root)

    def test_a_single_row_resolves_to_its_own_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            rows = [{"file_path": str(root / "only.jpg")}]

            self.assertEqual(_common_input_root(rows), root)

    def test_rows_without_a_shared_root_raise_instead_of_hanging(self) -> None:
        # On Windows two drives share no root, and Path("C:/").parent is Path("C:/"),
        # so the previous upward walk spun forever. Simulated here with a patched
        # commonpath so the behavior is asserted on every platform.
        rows = [{"file_path": "/a/one.jpg"}, {"file_path": "/b/two.jpg"}]
        with (
            patch("validation.os.path.commonpath", side_effect=ValueError("different drives")),
            self.assertRaises(ValueError) as raised,
        ):
            _common_input_root(rows)

        self.assertIn("common directory", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
