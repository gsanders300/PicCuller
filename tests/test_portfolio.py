import tempfile
import unittest
from pathlib import Path

from portfolio import load_feedback, select_portfolio_candidates


def candidate(name: str, score: float, embedding: tuple[float, ...]) -> dict:
    return {
        "file_path": str(Path(name).resolve()),
        "composite_score": score,
        "embedding": embedding,
    }


class PortfolioTests(unittest.TestCase):
    def test_zero_diversity_preserves_quality_order(self) -> None:
        candidates = [
            candidate("a.jpg", 3.0, (1.0, 0.0)),
            candidate("b.jpg", 2.0, (0.0, 1.0)),
        ]

        selected = select_portfolio_candidates(candidates, 2)

        self.assertEqual([item["composite_score"] for item in selected], [3.0, 2.0])

    def test_diversity_can_choose_a_dissimilar_candidate(self) -> None:
        candidates = [
            candidate("a.jpg", 3.0, (1.0, 0.0)),
            candidate("similar.jpg", 2.9, (1.0, 0.0)),
            candidate("different.jpg", 2.8, (0.0, 1.0)),
        ]

        selected = select_portfolio_candidates(candidates, 2, diversity_strength=0.6)

        self.assertEqual(Path(selected[1]["file_path"]).name, "different.jpg")

    def test_feedback_pins_keeps_and_removes_rejects(self) -> None:
        keep = candidate("keep.jpg", 1.0, (1.0, 0.0))
        reject = candidate("reject.jpg", 3.0, (0.0, 1.0))
        neutral = candidate("neutral.jpg", 2.0, (0.5, 0.5))
        feedback = {
            keep["file_path"]: "keep",
            reject["file_path"]: "reject",
        }

        selected = select_portfolio_candidates([keep, reject, neutral], 2, feedback=feedback)

        self.assertEqual(
            {Path(item["file_path"]).name for item in selected}, {"keep.jpg", "neutral.jpg"}
        )

    def test_feedback_paths_can_be_relative_to_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            feedback_file = root / "feedback.csv"
            feedback_file.write_text(
                "file_path,decision\nphotos/a.jpg,keep\nphotos/b.jpg,reject\n",
                encoding="utf-8",
            )

            feedback = load_feedback(feedback_file, root)

        self.assertEqual(feedback[str((root / "photos/a.jpg").resolve())], "keep")
        self.assertEqual(feedback[str((root / "photos/b.jpg").resolve())], "reject")


if __name__ == "__main__":
    unittest.main()
