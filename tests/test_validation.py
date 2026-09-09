from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from validation import evaluate_feedback


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


if __name__ == "__main__":
    unittest.main()
