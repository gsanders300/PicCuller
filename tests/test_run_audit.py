import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from run_audit import RunAudit, atomic_write_text, installed_version


def read_failure_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class RunAuditTests(unittest.TestCase):
    def test_manifest_tracks_counts_outputs_and_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            audit = RunAudit(
                root / "run",
                input_root=root,
                output_root=root / "output",
                configuration={"preset": "balanced"},
                models={"clip": "model@revision"},
                discovered_count=3,
            )
            audit.set_phase("evaluation")
            audit.increment("cached")
            audit.set_count("winners", 2)
            audit.add_output("evaluation", audit.run_dir / "evaluation.csv")
            audit.finish()

            manifest = json.loads(audit.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["counts"]["cached"], 1)
        self.assertEqual(manifest["counts"]["winners"], 2)
        self.assertIn("evaluation", manifest["outputs"])

    def test_failure_is_written_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            audit = RunAudit(
                root / "run",
                input_root=root,
                output_root=root / "output",
                configuration={},
                models={},
                discovered_count=1,
            )
            audit.record_failure(root / "bad.jpg", "decode", ValueError("broken"))

            failures = audit.failures_path.read_text(encoding="utf-8")

        self.assertIn("bad.jpg", failures)
        self.assertIn("ValueError", failures)
        self.assertIn("broken", failures)

    def test_many_failures_are_appended_not_rewritten(self) -> None:
        """Recording F failures must cost O(F) bytes, not O(F squared)."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            audit = RunAudit(
                root / "run",
                input_root=root,
                output_root=root / "output",
                configuration={},
                models={},
                discovered_count=200,
            )
            row_writes = 0
            original_writerow = csv.DictWriter.writerow

            def counting_writerow(self, rowdict):
                nonlocal row_writes
                row_writes += 1
                return original_writerow(self, rowdict)

            with (
                patch("run_audit.atomic_write_csv") as rewrite,
                patch.object(csv.DictWriter, "writerow", counting_writerow),
            ):
                for index in range(200):
                    audit.record_failure(root / f"bad{index}.jpg", "decode", ValueError("broken"))
                audit.finish()

            rows = read_failure_rows(audit.failures_path)

            # The whole file is never rewritten from an accumulated list.
            rewrite.assert_not_called()
            self.assertEqual(len(rows), 200)
            self.assertEqual(rows[0]["file_path"], str(root / "bad0.jpg"))
            self.assertEqual(rows[-1]["file_path"], str(root / "bad199.jpg"))
            # One row write per failure. Rewriting would cost 1+2+...+200 = 20,100.
            self.assertEqual(row_writes, 200)

    def test_failures_are_readable_before_finish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            audit = RunAudit(
                root / "run",
                input_root=root,
                output_root=root / "output",
                configuration={},
                models={},
                discovered_count=1,
            )
            audit.record_failure(root / "a.jpg", "decode", ValueError("first"))
            audit.record_failure(None, "metadata_bulk", RuntimeError("second"))

            rows = read_failure_rows(audit.failures_path)

            self.assertEqual([row["stage"] for row in rows], ["decode", "metadata_bulk"])
            self.assertEqual(rows[1]["file_path"], "")
            manifest = json.loads(audit.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["counts"]["failed"], 2)

    def test_empty_failures_file_has_only_a_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            audit = RunAudit(
                root / "run",
                input_root=root,
                output_root=root / "output",
                configuration={},
                models={},
                discovered_count=1,
            )
            audit.finish()

            self.assertEqual(read_failure_rows(audit.failures_path), [])
            self.assertTrue(
                audit.failures_path.read_text(encoding="utf-8").startswith("file_path,stage")
            )

    def test_failure_messages_are_protected_from_formula_injection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            audit = RunAudit(
                root / "run",
                input_root=root,
                output_root=root / "output",
                configuration={},
                models={},
                discovered_count=1,
            )
            audit.record_failure(root / "a.jpg", "decode", ValueError("=cmd|'/c calc'!A1"))

            rows = read_failure_rows(audit.failures_path)

            self.assertTrue(rows[0]["message"].startswith("'="))

    def test_stage_timings_accumulate_without_rewriting_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            audit = RunAudit(
                root / "run",
                input_root=root,
                output_root=root / "output",
                configuration={},
                models={},
                discovered_count=2,
            )
            with patch.object(RunAudit, "_write_manifest") as write:
                audit.accumulate_stage("decode", 0.25)
                audit.accumulate_stage("decode", 0.75)
                audit.accumulate_stage("clip", 1.5, count=8)
                write.assert_not_called()
            audit.finish()

            manifest = json.loads(audit.manifest_path.read_text(encoding="utf-8"))

            self.assertAlmostEqual(manifest["stage_seconds"]["decode"], 1.0)
            self.assertAlmostEqual(manifest["stage_seconds"]["clip"], 1.5)
            self.assertEqual(manifest["stage_counts"]["decode"], 2)
            self.assertEqual(manifest["stage_counts"]["clip"], 8)

    def test_manifest_reports_only_real_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            audit = RunAudit(
                root / "run",
                input_root=root,
                output_root=root / "output",
                configuration={},
                models={},
                discovered_count=1,
            )
            audit.finish()

            manifest = json.loads(audit.manifest_path.read_text(encoding="utf-8"))
            packages = manifest["environment"]["packages"]

            # pandas is not a dependency, so reporting it always said not-installed.
            self.assertNotIn("pandas", packages)
            self.assertEqual(packages["numpy"], installed_version("numpy"))

    def test_atomic_write_replaces_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "value.txt"
            atomic_write_text(destination, "first")
            atomic_write_text(destination, "second")

            self.assertEqual(destination.read_text(encoding="utf-8"), "second")


if __name__ == "__main__":
    unittest.main()
