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
        self.assertEqual(fake_console.log.call_count, 2)

    def test_noninteractive_default_explains_that_feedback_keeps_apply(self) -> None:
        fake_console = Mock()
        fake_console.is_terminal = False

        with patch.object(cull, "console", fake_console):
            count = _selection_count(None, 4)

        self.assertEqual(count, 0)
        message = fake_console.print.call_args.args[0]
        self.assertIn("zero automatic selections", message)
        self.assertIn("Feedback keeps still apply", message)

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

        self.assertIn("feedback keeps still apply", help_text)
        self.assertIn("static output without color", help_text)

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
        winner = {
            "selection_rank": 1,
            "global_quality_rank": 1,
            "file_name": "[red]literal-name.jpg[/red]",
            "burst_id": 1,
            "focus_score": 100.0,
            "musiq_score": 75.0,
            "aesthetic_score": 6.5,
            "composite_score": 4.5,
        }

        with patch.object(cull, "console", test_console):
            cull._show_summary([winner])

        rendered = stream.getvalue()
        self.assertIn("[red]lite", rendered)
        self.assertIn("Found 1 unique candidates", rendered)


if __name__ == "__main__":
    unittest.main()
