"""Keep the user-facing documentation consistent with the implementation.

`AGENTS.md` requires the reference and the specification to change with the code, but
nothing enforced it. These checks fail when an option loses its help text, when a
preset gains no reference section, when a subject prompt is reworded without the
reference following, or when the published weights table drifts from `scoring.py`.
"""

import re
import unittest
from pathlib import Path

from advanced_analysis import SUBJECT_PROMPTS
from cull import build_parser
from scoring import SCORING_PROFILES

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs" / "reference.md"
README = ROOT / "readme.md"

WEIGHT_COLUMN_ORDER = (
    "absolute_focus_floor",
    "absolute_focus_weight",
    "relative_focus_exponent",
    "musiq_weight",
    "exposure_weight",
    "eye_weight",
    "subject_weight",
)


def reference_text() -> str:
    return REFERENCE.read_text(encoding="utf-8")


def published_weights() -> dict[str, list[float]]:
    """Read the profile table under `### Scoring profiles` back out of the reference."""
    rows: dict[str, list[float]] = {}
    lines = reference_text().splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("| Profile | Focus floor |"):
            continue
        for row in lines[index + 2 :]:
            if not row.startswith("|"):
                break
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            rows[cells[0].strip("`")] = [float(cell) for cell in cells[1:]]
        break
    return rows


class CommandHelpTests(unittest.TestCase):
    def test_every_option_carries_help_text(self) -> None:
        """The documentation sends the user to `--help` for the current option list."""
        for action in build_parser()._actions:
            with self.subTest(option=action.option_strings or action.dest):
                self.assertTrue(
                    (action.help or "").strip(),
                    f"{action.option_strings or action.dest} prints its name and nothing else",
                )


class PresetDocumentationTests(unittest.TestCase):
    def test_every_preset_has_its_own_reference_section(self) -> None:
        text = reference_text()
        for preset in SCORING_PROFILES:
            with self.subTest(preset=preset):
                self.assertIn(
                    f"#### The `{preset}` preset",
                    text,
                    f"{preset} is selectable but undocumented",
                )

    def test_every_subject_prompt_appears_verbatim(self) -> None:
        """A prompt is the preset's behavior, so a reworded prompt is a doc change."""
        text = reference_text()
        for preset, prompts in SUBJECT_PROMPTS.items():
            for prompt in prompts:
                with self.subTest(preset=preset, prompt=prompt):
                    self.assertIn(prompt, text, f"{preset} prompt is not in the reference")

    def test_the_published_weights_match_scoring_py(self) -> None:
        published = published_weights()
        self.assertEqual(set(published), set(SCORING_PROFILES))
        for preset, profile in SCORING_PROFILES.items():
            values = published[preset]
            self.assertEqual(len(values), len(WEIGHT_COLUMN_ORDER))
            for column, value in zip(WEIGHT_COLUMN_ORDER, values, strict=True):
                with self.subTest(preset=preset, column=column):
                    self.assertAlmostEqual(getattr(profile, column), value, places=6)


class EyeFactorDocumentationTests(unittest.TestCase):
    def test_the_docs_quote_only_values_the_cascade_can_return(self) -> None:
        """`analyze_portrait` returns 0.7 or 1.0, and the README once claimed 0.85.

        Paragraphs, not sentences: a decimal contains a period, so splitting on
        periods hides the very number this check exists to catch.
        """
        returnable = {"0.7", "1.0"}
        paragraphs = [
            paragraph
            for document in (README, REFERENCE)
            for paragraph in document.read_text(encoding="utf-8").split("\n\n")
            if "eye factor" in paragraph and "|" not in paragraph
        ]
        self.assertTrue(paragraphs, "the eye factor is no longer described")
        for paragraph in paragraphs:
            for number in re.findall(r"\d+\.\d+", paragraph):
                with self.subTest(number=number):
                    self.assertIn(
                        number,
                        returnable,
                        f"the documentation quotes an eye factor of {number}, "
                        f"which analyze_portrait never returns",
                    )


if __name__ == "__main__":
    unittest.main()
