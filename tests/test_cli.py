import io
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rich.console import Console

import cull
from cull import _selection_count, _validate_selection


class SelectionInputTests(unittest.TestCase):
    def test_explicit_noninteractive_values(self) -> None:
        self.assertEqual(_selection_count("none", 4), 0)
        self.assertEqual(_selection_count("all", 4), 4)
        self.assertEqual(_selection_count("2", 4), 2)

    def test_invalid_selection_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _selection_count("five", 4)
        with self.assertRaises(ValueError):
            _selection_count("5", 4)

    def test_interactive_selection_reprompts_after_invalid_values(self) -> None:
        fake_console = Mock()
        fake_console.is_terminal = True
        fake_console.input.side_effect = ["five", "9", "2"]
        fake_stdin = Mock()
        fake_stdin.isatty.return_value = True

        with (
            patch.object(cull, "console", fake_console),
            patch.object(cull.sys, "stdin", fake_stdin),
        ):
            count = _selection_count(None, 4)

        self.assertEqual(count, 2)
        self.assertEqual(fake_console.input.call_count, 3)
        warnings = [
            call.args[0] for call in fake_console.print.call_args_list if "Warning:" in call.args[0]
        ]
        self.assertEqual(len(warnings), 2)

    def test_the_prompt_explains_that_feedback_keeps_count_toward_the_number(self) -> None:
        fake_console = Mock()
        fake_console.is_terminal = True
        fake_console.input.return_value = "3"
        fake_stdin = Mock()
        fake_stdin.isatty.return_value = True

        with (
            patch.object(cull, "console", fake_console),
            patch.object(cull.sys, "stdin", fake_stdin),
        ):
            count = _selection_count(None, 4, keep_count=2)

        self.assertEqual(count, 3)
        note = fake_console.print.call_args.args[0]
        self.assertIn("2 photos marked keep", note)
        self.assertIn("count toward this number", note)
        self.assertIn("How many photos to export", fake_console.input.call_args.args[0])

    def test_noninteractive_default_explains_that_feedback_keeps_apply(self) -> None:
        fake_console = Mock()
        fake_console.is_terminal = False

        with patch.object(cull, "console", fake_console):
            count = _selection_count(None, 4)

        self.assertEqual(count, 0)
        message = fake_console.print.call_args.args[0]
        self.assertIn("no photos are exported automatically", message)
        self.assertNotIn("marked keep", message)

        with patch.object(cull, "console", fake_console):
            _selection_count(None, 4, keep_count=1)

        message = fake_console.print.call_args.args[0]
        self.assertIn("1 photo marked keep in the feedback file will still be exported", message)

    def test_redirected_input_disables_the_prompt_with_a_terminal_display(self) -> None:
        fake_console = Mock()
        fake_console.is_terminal = True
        fake_stdin = Mock()
        fake_stdin.isatty.return_value = False

        with (
            patch.object(cull, "console", fake_console),
            patch.object(cull.sys, "stdin", fake_stdin),
        ):
            count = _selection_count(None, 4)

        self.assertEqual(count, 0)
        fake_console.input.assert_not_called()


class SelectionValidationTests(unittest.TestCase):
    """--select must fail before the evaluation pass, not after it."""

    def test_malformed_values_are_rejected_up_front(self) -> None:
        for value in ("3O", "five", "1.5", "-5", "2,3"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _validate_selection(value)

    def test_accepted_values_pass(self) -> None:
        for value in (None, "", "none", "skip", "all", "ALL", "0", "7", " 3 "):
            with self.subTest(value=value):
                _validate_selection(value)

    def test_count_above_available_is_left_to_the_ranking_stage(self) -> None:
        # The available count is unknown before ranking, so an over-large value
        # must pass the early gate and still be rejected later.
        _validate_selection("100000")
        with self.assertRaises(ValueError):
            _selection_count("100000", 4)


class TerminalBehaviorTests(unittest.TestCase):
    def test_help_explains_plain_output_and_feedback_keeps(self) -> None:
        help_text = " ".join(cull.build_parser().format_help().split())

        self.assertIn("Feedback keeps are always exported", help_text)
        self.assertIn("static output without color", help_text)
        self.assertIn("(default: 2.0)", help_text)

    def test_every_option_has_help_text(self) -> None:
        for action in cull.build_parser()._actions:
            with self.subTest(option=action.dest):
                self.assertTrue(action.help)

    def test_warnings_do_not_show_a_source_location(self) -> None:
        stream = io.StringIO()
        test_console = Console(file=stream, force_terminal=True, no_color=True, width=100)

        with patch.object(cull, "console", test_console):
            cull._warning("Skipped IMG_0042.CR3: cannot identify image file")

        rendered = stream.getvalue()
        self.assertIn("Warning: Skipped IMG_0042.CR3", rendered)
        self.assertNotIn("cull.py", rendered)

    def test_decode_executor_cancels_queued_work_on_interrupt(self) -> None:
        executor = Mock()

        with (
            patch.object(cull, "ThreadPoolExecutor", return_value=executor),
            self.assertRaises(KeyboardInterrupt),
            cull._decode_executor(4),
        ):
            raise KeyboardInterrupt

        executor.shutdown.assert_called_once_with(wait=True, cancel_futures=True)

    def test_download_reporter_throttles_progress_and_names_the_file(self) -> None:
        stream = io.StringIO()
        test_console = Console(file=stream, force_terminal=False, no_color=True)

        with patch.object(cull, "console", test_console):
            report = cull._model_download_reporter()
            destination = Path("weights.bin")
            for downloaded in (0, 5, 10, 15, 100):
                report(destination, downloaded, 100)

        rendered = stream.getvalue()
        self.assertIn("Downloading weights.bin: 0%", rendered)
        self.assertIn("Downloading weights.bin: 10%", rendered)
        self.assertIn("Downloading weights.bin: 100%", rendered)
        self.assertNotIn("5%", rendered)

    def test_narrow_terminal_displays_markup_characters_as_text(self) -> None:
        stream = io.StringIO()
        test_console = Console(
            file=stream,
            force_terminal=True,
            no_color=True,
            width=40,
        )
        root = Path("/photos")
        winner = {
            "selection_rank": 1,
            "file_path": str(root / "[red]literal-name.jpg[/red]"),
            "composite_score": 4.5,
            "score_reason": "single shot",
        }

        with patch.object(cull, "console", test_console):
            cull._show_summary([winner], 1, root)

        rendered = stream.getvalue()
        self.assertIn("[red]lite", rendered)
        self.assertIn("burst winners", rendered)

    def test_summary_shows_collection_paths_and_the_reason(self) -> None:
        stream = io.StringIO()
        test_console = Console(file=stream, force_terminal=False, no_color=True, width=120)
        root = Path("/photos")
        winners = [
            {
                "selection_rank": rank,
                "file_path": str(root / folder / "same.jpg"),
                "composite_score": 5.0 - rank,
                "score_reason": f"best of {rank + 1} in burst",
            }
            for rank, folder in ((1, "day-one"), (2, "day-two"))
        ]

        with patch.object(cull, "console", test_console):
            cull._show_summary(winners, 7, root)

        rendered = stream.getvalue()
        self.assertIn(str(Path("day-one") / "same.jpg"), rendered)
        self.assertIn(str(Path("day-two") / "same.jpg"), rendered)
        self.assertIn("best of 3 in burst", rendered)
        self.assertIn("from 7 photos", rendered)

    def test_export_list_says_why_each_photo_was_selected(self) -> None:
        stream = io.StringIO()
        test_console = Console(file=stream, force_terminal=False, no_color=True, width=120)
        root = Path("/photos")
        selected = [
            {
                "file_path": str(root / "winner.jpg"),
                "composite_score": 4.0,
                "selection_rank": 1,
                "feedback_decision": "",
            },
            {
                "file_path": str(root / "kept.jpg"),
                "composite_score": 0.5,
                "selection_rank": "",
                "feedback_decision": "keep",
            },
        ]

        with patch.object(cull, "console", test_console):
            cull._show_exported(selected, root, diversity=0.0)

        rendered = stream.getvalue()
        self.assertIn("burst winner #1", rendered)
        self.assertIn("marked keep in feedback", rendered)
        self.assertNotIn("Diversity", rendered)


if __name__ == "__main__":
    unittest.main()
