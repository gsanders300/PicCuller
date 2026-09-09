import unittest

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


if __name__ == "__main__":
    unittest.main()
