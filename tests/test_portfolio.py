import os
import random
import tempfile
import unittest
from pathlib import Path

from portfolio import generate_contact_sheet, load_feedback, select_portfolio_candidates
from xmp_rating import rating_for_rank


def candidate(name: str, score: float, embedding: tuple[float, ...]) -> dict:
    return {
        "file_path": str(Path(name).resolve()),
        "composite_score": score,
        "embedding": embedding,
    }


def reference_mmr(
    candidates: list[dict],
    count: int,
    diversity_strength: float,
) -> list[str]:
    """Straightforward O(K^2 x N) maximal marginal relevance, for comparison."""
    eligible = sorted(
        candidates,
        key=lambda item: (-float(item["composite_score"]), str(item["file_path"]).casefold()),
    )
    qualities = [float(item["composite_score"]) for item in eligible]
    minimum = min(qualities)
    span = max(qualities) - minimum
    available = list(eligible)
    selected: list[dict] = []

    def cosine(first: dict, second: dict) -> float:
        return sum(
            float(a) * float(b)
            for a, b in zip(first["embedding"], second["embedding"], strict=True)
        )

    while available and len(selected) < min(len(eligible), count):
        best = None
        for item in available:
            quality = (float(item["composite_score"]) - minimum) / span if span > 0 else 1.0
            similarity = (
                max((cosine(item, chosen) + 1.0) / 2.0 for chosen in selected) if selected else 0.0
            )
            objective = (1.0 - diversity_strength) * quality - diversity_strength * similarity
            key = (objective, quality, -1.0)
            if best is None or key > best[0]:
                best = (key, item)
        assert best is not None
        selected.append(best[1])
        available.remove(best[1])
    return sorted(str(item["file_path"]) for item in selected)


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


class SelectionOrderTests(unittest.TestCase):
    """A pinned keep must not take rank 1, and so must not take five stars."""

    def _pinned_worst(self) -> tuple[list[dict], dict[str, str]]:
        candidates = [
            candidate("a.jpg", 10.0, (1.0, 0.0)),
            candidate("b.jpg", 9.0, (0.9, 0.1)),
            candidate("c.jpg", 8.0, (0.8, 0.2)),
            candidate("d.jpg", 7.0, (0.2, 0.8)),
            candidate("e.jpg", 6.0, (0.0, 1.0)),
        ]
        return candidates, {candidates[-1]["file_path"]: "keep"}

    def test_pinned_keep_does_not_displace_better_images(self) -> None:
        candidates, feedback = self._pinned_worst()

        for strength in (0.0, 0.5):
            with self.subTest(diversity=strength):
                selected = select_portfolio_candidates(
                    candidates, 3, diversity_strength=strength, feedback=feedback
                )
                scores = [item["composite_score"] for item in selected]

                self.assertEqual(scores, sorted(scores, reverse=True))
                self.assertEqual(scores[0], 10.0)
                # The pin is still honored, just not at rank 1.
                self.assertIn(6.0, scores)

    def test_five_stars_go_to_the_best_image_not_the_pinned_one(self) -> None:
        candidates, feedback = self._pinned_worst()

        selected = select_portfolio_candidates(candidates, 3, feedback=feedback)
        ratings = {
            Path(item["file_path"]).name: rating_for_rank(rank, len(selected))
            for rank, item in enumerate(selected, start=1)
        }

        self.assertEqual(ratings["a.jpg"], 5)
        self.assertLess(ratings["e.jpg"], 5)

    def test_forced_keeps_can_exceed_the_requested_count(self) -> None:
        candidates = [candidate(f"{index}.jpg", float(index), (1.0, 0.0)) for index in range(5)]
        feedback = {item["file_path"]: "keep" for item in candidates[:4]}

        selected = select_portfolio_candidates(candidates, 1, feedback=feedback)

        self.assertEqual(len(selected), 4)


class DiversityDeterminismTests(unittest.TestCase):
    def test_tie_break_matches_the_pure_quality_path(self) -> None:
        candidates = [
            candidate("z.jpg", 5.0, (1.0, 0.0)),
            candidate("a.jpg", 5.0, (1.0, 0.0)),
            candidate("m.jpg", 5.0, (1.0, 0.0)),
        ]

        baseline = [
            Path(item["file_path"]).name
            for item in select_portfolio_candidates(candidates, 2, diversity_strength=0.0)
        ]

        for strength in (0.2, 0.4, 0.8, 1.0):
            with self.subTest(diversity=strength):
                names = [
                    Path(item["file_path"]).name
                    for item in select_portfolio_candidates(
                        candidates, 2, diversity_strength=strength
                    )
                ]
                self.assertEqual(names, baseline)

    def test_repeated_runs_are_stable(self) -> None:
        candidates = [
            candidate("one.jpg", 4.0, (1.0, 0.0)),
            candidate("two.jpg", 4.0, (0.0, 1.0)),
            candidate("three.jpg", 4.0, (0.7071, 0.7071)),
        ]

        results = {
            tuple(
                item["file_path"]
                for item in select_portfolio_candidates(candidates, 2, diversity_strength=0.5)
            )
            for _ in range(5)
        }

        self.assertEqual(len(results), 1)


class DiversityEquivalenceTests(unittest.TestCase):
    """The vectorized selection must agree with a plain reference implementation."""

    def test_matches_reference_mmr_on_random_inputs(self) -> None:
        generator = random.Random(20260909)
        for trial in range(8):
            dimension = 6
            candidates = []
            for index in range(24):
                raw = [generator.gauss(0.0, 1.0) for _ in range(dimension)]
                norm = sum(value * value for value in raw) ** 0.5
                candidates.append(
                    candidate(
                        f"img{index:03d}.jpg",
                        # Distinct scores, so no ties and both tie-break rules agree.
                        round(1.0 + index * 0.37, 4),
                        tuple(value / norm for value in raw),
                    )
                )
            strength = (trial + 1) / 10.0
            with self.subTest(trial=trial, diversity=strength):
                got = sorted(
                    str(item["file_path"])
                    for item in select_portfolio_candidates(
                        candidates, 7, diversity_strength=strength
                    )
                )
                self.assertEqual(got, reference_mmr(candidates, 7, strength))

    def test_scales_to_a_size_the_old_loop_could_not(self) -> None:
        generator = random.Random(7)
        candidates = [
            candidate(
                f"img{index:05d}.jpg",
                generator.uniform(1.0, 9.0),
                tuple(generator.uniform(-1.0, 1.0) for _ in range(64)),
            )
            for index in range(1500)
        ]

        selected = select_portfolio_candidates(candidates, 120, diversity_strength=0.3)

        self.assertEqual(len(selected), 120)
        self.assertEqual(len({item["file_path"] for item in selected}), 120)


class ReviewPageTests(unittest.TestCase):
    def test_cards_label_forced_keeps_and_mark_exports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            thumbnail = root / "store.jpg"
            thumbnail.write_bytes(b"jpeg")
            common = {"aesthetic_score": 6.0, "musiq_score": 70.0}
            winner = {
                **common,
                "file_path": str(root / "winner.jpg"),
                "composite_score": 4.0,
                "selection_rank": 1,
                "score_reason": "best of 3 in burst",
            }
            kept = {
                **common,
                "file_path": str(root / "kept.jpg"),
                "composite_score": 0.5,
                "selection_rank": "",
                "score_reason": "2nd of 3 in burst",
            }
            page = root / "run" / "review.html"

            generated, failures = generate_contact_sheet(
                page, [[winner], [kept]], lambda _source: thumbnail, {kept["file_path"]}
            )

            html = page.read_text(encoding="utf-8")
            self.assertEqual((generated, failures), (2, []))
            self.assertIn("<h3>#1 winner.jpg</h3>", html)
            self.assertIn(
                '<h3>Feedback keep kept.jpg <span class="badge exported">Exported</span></h3>', html
            )
            self.assertIn("2nd of 3 in burst", html)

    def test_page_offers_a_full_window_viewer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            thumbnail = root / "store.jpg"
            thumbnail.write_bytes(b"jpeg")
            candidate = {
                "file_path": str(root / "a.jpg"),
                "composite_score": 4.0,
                "aesthetic_score": 6.0,
                "musiq_score": 70.0,
                "selection_rank": 1,
            }
            page = root / "run" / "review.html"

            generate_contact_sheet(page, [[candidate]], lambda _source: thumbnail)

            html = page.read_text(encoding="utf-8")
            self.assertIn('<div id="viewer" hidden>', html)
            self.assertIn('<img id="viewer-image" alt="">', html)
            self.assertIn("K keep · R reject", html)

    def review_page(self, run_name: str) -> str:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            thumbnail = root / "store.jpg"
            thumbnail.write_bytes(b"jpeg")
            candidate = {
                "file_path": str(root / "a.jpg"),
                "composite_score": 4.0,
                "aesthetic_score": 6.0,
                "musiq_score": 70.0,
                "selection_rank": 1,
            }
            page = root / run_name / "review.html"
            generate_contact_sheet(page, [[candidate]], lambda _source: thumbnail)
            return page.read_text(encoding="utf-8")

    def test_decisions_are_remembered_per_run(self) -> None:
        """Browsers share one storage area for file:// pages, so the key names the run."""
        first = self.review_page("run_20260925_191033_757361")
        second = self.review_page("run_20260925_191401_886762")

        self.assertIn('const storageKey="photo-cull:run_20260925_191033_757361";', first)
        self.assertIn('const storageKey="photo-cull:run_20260925_191401_886762";', second)
        self.assertIn("localStorage.setItem(storageKey", first)

    @unittest.skipIf(os.name == "nt", "Windows does not allow < or > in a folder name")
    def test_a_hostile_run_folder_cannot_open_markup_in_the_script(self) -> None:
        # A folder name cannot hold "/", so "</script>" is impossible, but "<script" or
        # "<!--" inside a script block still changes how the browser parses it.
        html = self.review_page("run<script><!--")

        self.assertIn('const storageKey="photo-cull:run\\u003cscript>\\u003c!--";', html)
        self.assertNotIn("run<script>", html)


if __name__ == "__main__":
    unittest.main()
