import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from rich.console import Console

from scoring import SCORING_PROFILES
from tuning import (
    BurstRules,
    FamilySizes,
    Preview,
    Selection,
    TuningSettings,
    apply_weights,
    boundary,
    burst_rules,
    changes,
    parse_command,
    parse_target,
    parse_weight,
    resolve_target,
    run_tuning_loop,
)


def settings(**overrides) -> TuningSettings:
    values = {
        "mode": "bursts",
        "target": "none",
        "profile": SCORING_PROFILES["balanced"],
        "diversity": 0.0,
    }
    values.update(overrides)
    return TuningSettings(**values)


def record(name: str, score: float) -> dict:
    return {"file_path": f"/photos/{name}", "composite_score": score}


def selection(records: list[dict], count: int, keeps: frozenset[str] = frozenset()) -> Selection:
    ordered = sorted(records, key=lambda item: -item["composite_score"])
    return Selection(
        selected=tuple(ordered[:count]),
        pool=tuple(records),
        base=len(records),
        photo_count=len(records),
        requested=count,
        capped=False,
        keep_paths=keeps,
    )


class TargetTests(unittest.TestCase):
    def test_counts_percentages_and_aliases_normalize(self) -> None:
        cases = {
            "": "none",
            "none": "none",
            "SKIP": "none",
            "0": "none",
            "0%": "none",
            "all": "all",
            " 12 ": "12",
            "10%": "10%",
            "2.50 %": "2.5%",
            "100%": "100%",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_target(text), expected)

    def test_malformed_targets_are_rejected(self) -> None:
        for text in ("five", "1.5", "-3", "101%", "-1%", "nan%", "inf%", "x%", "%"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_target(text)

    def test_percentages_round_half_up_with_at_least_one_photo(self) -> None:
        self.assertEqual(resolve_target("10%", 1840), (184, False))
        self.assertEqual(resolve_target("50%", 3), (2, False))
        self.assertEqual(resolve_target("33%", 3), (1, False))
        self.assertEqual(resolve_target("1%", 10), (1, False))
        self.assertEqual(resolve_target("100%", 7), (7, False))
        self.assertEqual(resolve_target("10%", 0), (0, False))

    def test_a_count_above_the_base_is_capped_and_flagged(self) -> None:
        self.assertEqual(resolve_target("40", 612), (40, False))
        self.assertEqual(resolve_target("612", 612), (612, False))
        self.assertEqual(resolve_target("920", 612), (612, True))
        self.assertEqual(resolve_target("all", 612), (612, False))
        self.assertEqual(resolve_target("none", 612), (0, False))


class WeightTests(unittest.TestCase):
    def test_weights_parse_and_replace_only_the_named_field(self) -> None:
        self.assertEqual(parse_weight("Sharpness=1.5"), ("sharpness", 1.5))
        profile = apply_weights(SCORING_PROFILES["wildlife"], [("sharpness", 1.5), ("appeal", 0)])
        self.assertEqual(profile.absolute_focus_weight, 1.5)
        self.assertEqual(profile.aesthetic_weight, 0.0)
        self.assertEqual(profile.relative_focus_exponent, 1.8)
        self.assertEqual(profile.absolute_focus_floor, 0.55)

    def test_bad_weights_are_rejected(self) -> None:
        for text in ("sharpness", "focus=1", "sharpness=x", "sharpness=-0.1", "sharpness=3.5"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_weight(text)

    def test_audit_lists_every_slider(self) -> None:
        audit = settings(target="10%").audit_dict()
        self.assertEqual(audit["target"], "10%")
        self.assertEqual(audit["starting_preset"], "balanced")
        self.assertEqual(audit["weights"]["sharpness"], 0.5)
        self.assertEqual(len(audit["weights"]), 7)


class BurstRuleTests(unittest.TestCase):
    base = BurstRules(2.0, 10.0, 8, 0.88)

    def test_normal_keeps_the_configured_limits(self) -> None:
        self.assertEqual(burst_rules(self.base, "normal"), self.base)

    def test_looser_and_tighter_move_every_limit_together(self) -> None:
        self.assertEqual(burst_rules(self.base, "looser"), BurstRules(4.0, 20.0, 10, 0.84))
        tighter = burst_rules(self.base, "tighter")
        self.assertEqual((tighter.window, tighter.duration, tighter.phash_threshold), (1.0, 5.0, 6))
        self.assertAlmostEqual(tighter.sim_threshold, 0.92)

    def test_limits_stay_in_range(self) -> None:
        edge = BurstRules(0.0, 0.0, 1, 0.99)
        self.assertEqual(burst_rules(edge, "tighter").phash_threshold, 0)
        self.assertEqual(burst_rules(edge, "tighter").sim_threshold, 1.0)
        self.assertEqual(burst_rules(BurstRules(1.0, 1.0, 0, 0.02), "looser").sim_threshold, 0.0)
        with self.assertRaises(ValueError):
            burst_rules(self.base, "loose")


class CommandTests(unittest.TestCase):
    def test_actions(self) -> None:
        current = settings()
        self.assertEqual(parse_command("", current).action, "export")
        self.assertEqual(parse_command("  ", current).action, "export")
        self.assertEqual(parse_command("help", current).action, "help")
        self.assertEqual(parse_command("?", current).action, "help")
        self.assertEqual(parse_command("Settings", current).action, "settings")
        self.assertEqual(parse_command("show", current).action, "show")

    def test_changes(self) -> None:
        current = settings()
        self.assertEqual(parse_command("10%", current).settings.target, "10%")
        self.assertEqual(parse_command("target 60", current).settings.target, "60")
        self.assertEqual(parse_command("mode photos", current).settings.mode, "photos")
        self.assertEqual(parse_command("bursts looser", current).settings.burst_level, "looser")
        self.assertEqual(parse_command("diversity 0.25", current).settings.diversity, 0.25)
        changed = parse_command("burst-sharpness 2", current).settings
        self.assertEqual(changed.profile.relative_focus_exponent, 2.0)
        started = parse_command("start landscape", current).settings
        self.assertEqual(started.profile, SCORING_PROFILES["landscape"])

    def test_bad_input_raises_without_changing_settings(self) -> None:
        current = settings()
        for text in (
            "mode raw",
            "bursts loose",
            "start sports",
            "diversity 2",
            "diversity x",
            "sharpness 4",
            "sharpness",
            "mode photos bursts",
            "five",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_command(text, current)
        self.assertEqual(current, settings())


class FamilySizeTests(unittest.TestCase):
    def test_a_family_counts_once_with_its_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "IMG_1.ARW").write_bytes(b"a" * 100)
            (root / "IMG_1.JPG").write_bytes(b"b" * 10)
            (root / "IMG_1.ARW.xmp").write_bytes(b"c" * 1)
            (root / "IMG_2.ARW").write_bytes(b"d" * 1000)
            names = ("IMG_1.ARW", "IMG_1.JPG", "IMG_2.ARW")
            raw, jpeg, other = (str(root / name) for name in names)

            sizes = FamilySizes([raw, jpeg, other])

            self.assertEqual(sizes.total([raw]), (3, 111))
            self.assertEqual(sizes.total([raw, jpeg]), (3, 111))
            self.assertEqual(sizes.total([raw, other]), (4, 1111))
            self.assertEqual(sizes.total([]), (0, 0))


class BoundaryTests(unittest.TestCase):
    def test_boundary_lists_nearest_the_cut_first_and_skips_keeps(self) -> None:
        records = [record(name, score) for name, score in (("a", 5), ("b", 4), ("c", 3), ("d", 2))]
        current = selection(records, 3, keeps=frozenset({"/photos/c"}))

        inside, outside = boundary(current)

        self.assertEqual(inside, ["/photos/b", "/photos/a"])
        self.assertEqual(outside, ["/photos/d"])

    def test_changes_order_entered_and_left_by_closeness_to_the_cut(self) -> None:
        records = [record(name, score) for name, score in (("a", 5), ("b", 4), ("c", 3), ("d", 2))]
        scores = {item["file_path"]: item["composite_score"] for item in records}

        entered, left, entered_total, left_total = changes(
            selection(records, 1), selection(records, 3), scores.__getitem__
        )
        self.assertEqual(
            (entered, left, entered_total, left_total), (["/photos/c", "/photos/b"], [], 2, 0)
        )
        entered, left, _, left_total = changes(
            selection(records, 3), selection(records, 1), scores.__getitem__, limit=1
        )
        self.assertEqual((entered, left, left_total), ([], ["/photos/b"], 2))
        self.assertEqual(changes(None, selection(records, 1), scores.__getitem__), ([], [], 0, 0))


class PreviewTests(unittest.TestCase):
    def test_write_replaces_the_folders_with_thumbnail_copies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = root / "store"
            store.mkdir()
            opened: list[Path] = []

            def thumbnail(source: Path) -> Path:
                if source.name == "broken.jpg":
                    raise OSError("cannot decode")
                target = store / f"{source.stem}-thumb.jpg"
                target.write_bytes(source.name.encode())
                return target

            preview = Preview(root / "preview", thumbnail, workers=2, opener=opened.append)
            try:
                preview.warm(["/photos/a.jpg"])
                failures = preview.write(["/photos/a.jpg", "/photos/broken.jpg"], ["/x/b.jpg"])
                preview.open()
                self.assertEqual([path for path, _ in failures], ["/photos/broken.jpg"])
                written = sorted(
                    path.relative_to(root / "preview").as_posix()
                    for path in (root / "preview").rglob("*.jpg")
                )
                self.assertEqual(written, ["in/001_a.jpg", "out/001_b.jpg"])
                self.assertEqual((root / "preview" / "in" / "001_a.jpg").read_bytes(), b"a.jpg")
                self.assertEqual(opened, [root / "preview"])

                preview.write([], ["/photos/a.jpg"])
                self.assertEqual(list((root / "preview" / "in").iterdir()), [])
                self.assertTrue((root / "preview" / "out" / "001_a.jpg").is_file())
                # The store files are copied, never moved or linked.
                self.assertTrue((store / "a-thumb.jpg").is_file())
            finally:
                preview.close()


class LoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records = [
            record(name, score) for name, score in (("a", 5), ("b", 4), ("c", 3), ("d", 2))
        ]
        self.stream = io.StringIO()
        self.console = Console(file=self.stream, force_terminal=False, no_color=True, width=200)
        self.warnings: list[str] = []
        self.evaluated: list[TuningSettings] = []

    def evaluate(self, current: TuningSettings) -> Selection:
        self.evaluated.append(current)
        count, _ = resolve_target(current.target, len(self.records))
        return selection(self.records, count)

    def run_loop(self, lines: list[str], preview=None, start=None):
        answers = iter(lines)

        def read_line(_prompt: str) -> str:
            try:
                return next(answers)
            except StopIteration:
                raise EOFError from None

        return run_tuning_loop(
            self.console,
            start or settings(),
            self.records,
            self.evaluate,
            read_line,
            FamilySizes([]),
            preview,
            self.warnings.append,
        )

    def test_changes_redraw_and_enter_exports_the_current_settings(self) -> None:
        final, result = self.run_loop(["50%", "five", "mode photos", ""])

        self.assertEqual((final.target, final.mode), ("50%", "photos"))
        self.assertEqual(len(result.selected), 2)
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("Type 'help'", self.warnings[0])
        output = self.stream.getvalue()
        self.assertIn("Selection settings", output)
        self.assertIn("score at the cut 4.00", output)
        self.assertIn("+2 in, -0 out", output)

    def test_end_of_input_exports_nothing(self) -> None:
        final, result = self.run_loop(["3"])

        self.assertEqual(final.target, "none")
        self.assertEqual(result.selected, ())

    def test_show_without_the_thumbnail_store_warns(self) -> None:
        self.run_loop(["show", ""])

        self.assertIn("--contact-sheet 0", self.warnings[0])

    def test_show_previews_the_last_change_or_the_boundary(self) -> None:
        written: list[tuple[list[str], list[str]]] = []

        class FakePreview:
            root = Path("preview")

            def warm(self, _paths) -> None:
                pass

            def write(self, entered, left):
                written.append((entered, left))
                return []

            def open(self) -> None:
                pass

        self.run_loop(["2", "show", "3", "show", ""], preview=FakePreview(), start=settings())

        self.assertEqual(written[0], (["/photos/b", "/photos/a"], []))
        self.assertEqual(written[1], (["/photos/c"], []))
        self.assertIn("crossed the cut", self.stream.getvalue())

    def test_capped_and_keep_overflow_notes(self) -> None:
        capped = replace(selection(self.records, 4), requested=4, capped=True)
        self.assertIn("capped", summary(capped))
        keeps = replace(
            selection(self.records, 2),
            requested=1,
            keep_paths=frozenset({"/photos/a", "/photos/b"}),
        )
        self.assertIn("2 feedback keeps exceed the target of 1", summary(keeps))


def summary(result: Selection) -> str:
    from tuning import summary_text

    return summary_text(settings(target="9"), result, FamilySizes([]), None)


if __name__ == "__main__":
    unittest.main()
