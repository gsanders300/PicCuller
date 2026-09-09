import json
import tempfile
import unittest
from pathlib import Path

from run_audit import RunAudit, atomic_write_text


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

    def test_atomic_write_replaces_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "value.txt"
            atomic_write_text(destination, "first")
            atomic_write_text(destination, "second")

            self.assertEqual(destination.read_text(encoding="utf-8"), "second")


if __name__ == "__main__":
    unittest.main()
