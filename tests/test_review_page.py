"""The review page is the only labeling interface, so it decides what can be learned.

A page built from burst winners alone can never show the frame a pick beat, which is
the one comparison that sets the within-burst weights. These tests hold the page to
carrying whole bursts, and to doing it inside the existing thumbnail budget.
"""

import tempfile
import unittest
from pathlib import Path

from cull import _review_groups
from portfolio import generate_contact_sheet


def record(burst_id: int, burst_rank: int, name: str = "") -> dict:
    return {
        "file_path": f"/photos/b{burst_id}_r{burst_rank}{name}.ARW",
        "burst_id": burst_id,
        "burst_rank": burst_rank,
        "burst_size": 0,
        "burst_winner": burst_rank == 1,
        "selection_rank": burst_id if burst_rank == 1 else "",
        "composite_score": 10.0 - burst_rank,
        "focus_score": 100.0,
        "musiq_score": 70.0,
        "aesthetic_score": 6.0,
    }


def bursts(*sizes: int) -> tuple[list[dict], list[dict]]:
    """Build records for bursts of the given sizes, plus the winners-only pool."""
    records = [
        record(burst_id, burst_rank)
        for burst_id, size in enumerate(sizes, start=1)
        for burst_rank in range(1, size + 1)
    ]
    pool = [item for item in records if item["burst_winner"]]
    return records, pool


def paths(groups: list[list[dict]]) -> list[list[str]]:
    return [[item["file_path"] for item in group] for group in groups]


class ReviewGroupingTests(unittest.TestCase):
    def test_a_winner_arrives_with_the_frames_it_beat(self) -> None:
        records, pool = bursts(3)

        groups = _review_groups(pool, records, 100)

        self.assertEqual(
            paths(groups),
            [["/photos/b1_r1.ARW", "/photos/b1_r2.ARW", "/photos/b1_r3.ARW"]],
        )
        self.assertTrue(groups[0][0]["burst_winner"], "the pick must come first")

    def test_the_limit_caps_thumbnails_not_bursts(self) -> None:
        """--contact-sheet is documented as a thumbnail count, so decode cost is flat."""
        records, pool = bursts(3, 3, 3)

        groups = _review_groups(pool, records, 7)

        self.assertEqual(len(groups), 2)
        self.assertEqual(sum(len(group) for group in groups), 6)

    def test_the_walk_stops_rather_than_skipping_a_burst_for_a_smaller_one(self) -> None:
        """Skipping mid-list would silently drop a higher-ranked burst from the page."""
        records, pool = bursts(2, 5, 1)

        groups = _review_groups(pool, records, 4)

        self.assertEqual(paths(groups), [["/photos/b1_r1.ARW", "/photos/b1_r2.ARW"]])

    def test_a_burst_larger_than_the_whole_limit_is_truncated_not_dropped(self) -> None:
        records, pool = bursts(5)

        groups = _review_groups(pool, records, 3)

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 3)
        self.assertTrue(groups[0][0]["burst_winner"])

    def test_a_forced_keep_does_not_repeat_its_own_burst(self) -> None:
        """A feedback keep is appended to the pool and can be a non-winner."""
        records, pool = bursts(3)
        pool = pool + [records[2]]

        groups = _review_groups(pool, records, 100)

        self.assertEqual(len(groups), 1)

    def test_single_frame_bursts_keep_the_old_flat_behavior(self) -> None:
        records, pool = bursts(1, 1, 1, 1, 1)

        groups = _review_groups(pool, records, 3)

        self.assertEqual(
            paths(groups),
            [["/photos/b1_r1.ARW"], ["/photos/b2_r1.ARW"], ["/photos/b3_r1.ARW"]],
        )

    def test_frames_are_ordered_by_burst_rank_not_input_order(self) -> None:
        records, pool = bursts(3)
        records.reverse()

        groups = _review_groups(pool, records, 100)

        self.assertEqual([item["burst_rank"] for item in groups[0]], [1, 2, 3])


class ContactSheetRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.destination = root / "review.html"
        self.thumbnail = root / "source.jpg"
        self.thumbnail.write_bytes(b"not really a jpeg")
        self.addCleanup(self.directory.cleanup)

    def provider(self, _source: Path) -> Path:
        return self.thumbnail

    def test_a_burst_renders_as_one_block_with_the_pick_marked(self) -> None:
        records, pool = bursts(3)
        groups = _review_groups(pool, records, 100)

        count, failures = generate_contact_sheet(self.destination, groups, self.provider)
        page = self.destination.read_text(encoding="utf-8")

        self.assertEqual((count, failures), (3, []))
        self.assertEqual(page.count('<section class="burst">'), 1)
        self.assertEqual(page.count('class="badge pick">Pick'), 1)
        self.assertEqual(page.count('class="badge sibling">Lost'), 2)
        self.assertEqual(page.count("data-path="), 3)

    def test_single_frame_groups_share_one_grid(self) -> None:
        """A --no-group run must not become a one-card-per-row column."""
        records, pool = bursts(1, 1, 1)
        groups = _review_groups(pool, records, 100)

        count, _ = generate_contact_sheet(self.destination, groups, self.provider)
        page = self.destination.read_text(encoding="utf-8")

        self.assertEqual(count, 3)
        self.assertNotIn('<section class="burst">', page)
        self.assertEqual(page.count('<div class="grid">'), 1)

    def test_solo_frames_around_a_burst_keep_their_order(self) -> None:
        records, pool = bursts(1, 2, 1)
        groups = _review_groups(pool, records, 100)

        generate_contact_sheet(self.destination, groups, self.provider)
        page = self.destination.read_text(encoding="utf-8")

        self.assertLess(page.index("b1_r1"), page.index("b2_r1"))
        self.assertLess(page.index("b2_r2"), page.index("b3_r1"))
        self.assertEqual(page.count('<div class="grid">'), 3)

    def test_one_thumbnail_failure_removes_only_that_card(self) -> None:
        records, pool = bursts(3)
        groups = _review_groups(pool, records, 100)

        def failing(source: Path) -> Path:
            if source.name.endswith("r2.ARW"):
                raise OSError("thumbnail unavailable")
            return self.thumbnail

        count, failures = generate_contact_sheet(self.destination, groups, failing)
        page = self.destination.read_text(encoding="utf-8")

        self.assertEqual(count, 2)
        self.assertEqual([path.name for path, _ in failures], ["b1_r2.ARW"])
        self.assertNotIn("b1_r2", page)
        self.assertIn("b1_r1", page)

    def test_a_group_whose_thumbnails_all_fail_renders_no_empty_block(self) -> None:
        records, pool = bursts(2, 1)
        groups = _review_groups(pool, records, 100)

        def failing(source: Path) -> Path:
            if source.name.startswith("b1_"):
                raise OSError("thumbnail unavailable")
            return self.thumbnail

        count, failures = generate_contact_sheet(self.destination, groups, failing)
        page = self.destination.read_text(encoding="utf-8")

        self.assertEqual((count, len(failures)), (1, 2))
        self.assertNotIn('<section class="burst">', page)

    def test_a_hostile_file_name_cannot_inject_markup(self) -> None:
        # No slash in the payload: a closing tag would be read as a path separator
        # and `Path.name` would truncate it, leaving the heading untested.
        payload = 'a"><img src=x onerror=alert(1)>.jpg'
        for in_burst, sizes in ((False, (1,)), (True, (2,))):
            with self.subTest(in_burst=in_burst):
                records, pool = bursts(*sizes)
                records[0]["file_path"] = f"/photos/{payload}"
                groups = _review_groups(pool, records, 100)

                generate_contact_sheet(self.destination, groups, self.provider)
                page = self.destination.read_text(encoding="utf-8")

                self.assertIn(payload, str(Path(f"/photos/{payload}")))
                self.assertNotIn("<img src=x onerror=alert(1)>", page)
                self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page)


if __name__ == "__main__":
    unittest.main()
