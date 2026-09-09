import unittest

from cull import _selection_count


class SelectionInputTests(unittest.TestCase):
    def test_explicit_noninteractive_values(self) -> None:
        self.assertEqual(_selection_count("none", 4, None), 0)
        self.assertEqual(_selection_count("all", 4, None), 4)
        self.assertEqual(_selection_count("2", 4, None), 2)

    def test_invalid_selection_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _selection_count("five", 4, None)
        with self.assertRaises(ValueError):
            _selection_count("5", 4, None)


if __name__ == "__main__":
    unittest.main()
