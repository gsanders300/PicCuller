import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

import cull
from evaluation_cache import EvaluationCache


class FakeModelRuntime:
    def __init__(self, device, **_kwargs):
        self.device = device
        self.aesthetic_sha256 = "fake-aesthetic-hash"
        self.musiq_sha256 = "fake-musiq-hash"

    def infer_musiq(self, cv_image):
        return 70.0 + float(cv_image.mean() / 255.0)

    def infer_clip_batch(self, images):
        results = []
        for index, _image in enumerate(images):
            embedding = np.asarray((1.0, float(index)), dtype=np.float32)
            embedding /= np.linalg.norm(embedding)
            results.append(
                SimpleNamespace(
                    aesthetic_score=6.0 + index,
                    embedding=embedding,
                    subject_integrity=1.0,
                )
            )
        return results


def build_config(source: Path, output: Path, **overrides) -> cull.PipelineConfig:
    settings = {
        "input_folder": source,
        "output_folder": output,
        "time_window": 2.0,
        "max_burst_duration": 10.0,
        "phash_threshold": 8,
        "sim_threshold": 0.88,
        "no_group": True,
        "cache_mode": "none",
        "metadata_backend": "pillow",
        "assumed_timezone": "UTC",
        "device": "cpu",
        "batch_size": 2,
        "workers": 2,
        "mixed_precision": False,
        "preset": "balanced",
        "primary": "all",
        "selection": "all",
        "diversity": 0.0,
        "feedback_file": None,
        "contact_sheet_count": 2,
        "write_xmp": True,
        "aesthetic_head": None,
        "plain": True,
        "debug": True,
    }
    settings.update(overrides)
    return cull.PipelineConfig(**settings)


def make_photos(source: Path) -> None:
    (source / "day-one").mkdir(parents=True)
    (source / "day-two").mkdir(parents=True)
    Image.new("RGB", (64, 48), "red").save(source / "day-one" / "same.jpg")
    Image.new("RGB", (64, 48), "blue").save(source / "day-two" / "same.jpg")


def mocked_runtime():
    return (
        patch("model_runtime.ModelRuntime", FakeModelRuntime),
        patch("model_runtime.resolve_device", return_value=SimpleNamespace(type="cpu")),
    )


def run_with_mocks(config: cull.PipelineConfig) -> int:
    runtime_patch, device_patch = mocked_runtime()
    with runtime_patch, device_patch:
        return cull.run_pipeline(config)


def latest_run(output: Path) -> Path:
    return max(output.glob("run_*"), key=lambda path: path.name)


def read_manifest(run_dir: Path) -> dict:
    return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class PipelineIntegrationTests(unittest.TestCase):
    def test_noninteractive_run_creates_audited_review_and_safe_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)

            config = build_config(source, output)

            exit_code = run_with_mocks(config)

            run_directories = list(output.glob("run_*"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(run_directories), 1)
            run_dir = run_directories[0]
            manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(manifest["counts"]["selected"], 2)
            self.assertEqual(manifest["counts"]["exported_files"], 4)
            self.assertTrue((run_dir / "evaluation.csv").is_file())
            self.assertTrue((run_dir / "failures.csv").is_file())
            self.assertTrue((run_dir / "review.html").is_file())
            self.assertTrue((run_dir / "export_manifest.csv").is_file())
            self.assertTrue((run_dir / "picks/day-one/same.jpg").is_file())
            self.assertTrue((run_dir / "picks/day-two/same.jpg").is_file())
            self.assertTrue((run_dir / "picks/day-one/same.xmp").is_file())
            self.assertTrue((run_dir / "picks/day-two/same.xmp").is_file())
            export_manifest = (run_dir / "export_manifest.csv").read_text(encoding="utf-8")
            self.assertIn("generated_xmp", export_manifest)


class CacheHitPipelineTests(unittest.TestCase):
    """The resumable path: a second run must reuse rows and run no inference."""

    def test_second_run_reuses_every_cached_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            config = build_config(source, output, cache_mode="use", selection="none")

            self.assertEqual(run_with_mocks(config), 0)
            first = read_manifest(latest_run(output))
            self.assertEqual(first["counts"]["cached"], 0)
            self.assertEqual(first["counts"]["evaluated"], 2)
            self.assertTrue((output / "evaluation_cache.sqlite3").is_file())

            # A second run must not need the model runtime at all. Patching it to
            # raise proves no inference happens on a fully cached pass.
            def explode(*_args, **_kwargs):
                raise AssertionError("the model runtime must not load on a cached run")

            with patch("model_runtime.ModelRuntime", explode):
                self.assertEqual(cull.run_pipeline(config), 0)

            second_dir = latest_run(output)
            second = read_manifest(second_dir)
            self.assertEqual(second["counts"]["cached"], 2)
            self.assertEqual(second["counts"]["evaluated"], 0)
            self.assertEqual(second["environment"]["resolved_device"], "cache-only")

            rows = read_rows(second_dir / "evaluation.csv")
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["cache_hit"] == "True" for row in rows))

    def test_cached_metrics_match_the_first_run_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            config = build_config(source, output, cache_mode="use", selection="none")

            run_with_mocks(config)
            first_rows = {
                row["file_path"]: row for row in read_rows(latest_run(output) / "evaluation.csv")
            }
            run_with_mocks(config)
            second_rows = {
                row["file_path"]: row for row in read_rows(latest_run(output) / "evaluation.csv")
            }

            self.assertEqual(set(first_rows), set(second_rows))
            for path, first in first_rows.items():
                for field in (
                    "focus_score",
                    "musiq_score",
                    "aesthetic_score",
                    "composite_score",
                    "exposure_penalty",
                    "timestamp",
                    "global_quality_rank",
                ):
                    with self.subTest(path=Path(path).name, field=field):
                        self.assertEqual(first[field], second_rows[path][field])

    def test_a_changed_source_file_is_re_evaluated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            config = build_config(source, output, cache_mode="use", selection="none")

            run_with_mocks(config)
            Image.new("RGB", (72, 56), "green").save(source / "day-one" / "same.jpg")
            run_with_mocks(config)

            counts = read_manifest(latest_run(output))["counts"]
            self.assertEqual(counts["cached"], 1)
            self.assertEqual(counts["evaluated"], 1)


class CacheWriteFailureTests(unittest.TestCase):
    def test_a_failed_cache_write_keeps_the_completed_evaluation(self) -> None:
        """A SQLite fault must cost the cache row, not the computed record."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            config = build_config(source, output, cache_mode="use", selection="all")

            def broken_put(*_args, **_kwargs):
                raise sqlite3.OperationalError("database is locked")

            runtime_patch, device_patch = mocked_runtime()
            with runtime_patch, device_patch, patch.object(EvaluationCache, "put", broken_put):
                exit_code = cull.run_pipeline(config)

            run_dir = latest_run(output)
            manifest = read_manifest(run_dir)

            # The run still succeeds and still exports both photos.
            self.assertEqual(exit_code, 0)
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(manifest["counts"]["evaluated"], 2)
            self.assertEqual(len(read_rows(run_dir / "evaluation.csv")), 2)
            self.assertEqual(manifest["counts"]["selected"], 2)

            # The failure is visible rather than hidden.
            stages = {row["stage"] for row in read_rows(run_dir / "failures.csv")}
            self.assertIn("cache_write", stages)

    def test_a_file_changed_mid_evaluation_is_still_discarded(self) -> None:
        """The fingerprint re-check stays the one condition that drops a record."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            config = build_config(source, output, cache_mode="use", selection="none")

            real_prepare = cull._prepare_image
            already_changed: set[Path] = set()

            def prepare_then_modify(file_path, fingerprint, metadata, assumed_timezone):
                prepared = real_prepare(file_path, fingerprint, metadata, assumed_timezone)
                if not already_changed:
                    already_changed.add(file_path)
                    # As if the card were written to while the run was reading it.
                    with file_path.open("ab") as handle:
                        handle.write(b"appended after the decode")
                return prepared

            runtime_patch, device_patch = mocked_runtime()
            with (
                runtime_patch,
                device_patch,
                patch.object(cull, "_prepare_image", prepare_then_modify),
            ):
                exit_code = cull.run_pipeline(config)

            run_dir = latest_run(output)
            failures = read_rows(run_dir / "failures.csv")

            # The changed photo is dropped; the untouched one still completes.
            self.assertEqual(exit_code, 0)
            self.assertEqual([row["stage"] for row in failures], ["checkpoint"])
            self.assertIn("changed while", failures[0]["message"])
            self.assertEqual(read_manifest(run_dir)["counts"]["evaluated"], 1)
            self.assertEqual(len(read_rows(run_dir / "evaluation.csv")), 1)


class ReportBeforePromptTests(unittest.TestCase):
    def test_evaluation_csv_survives_an_interrupt_at_the_selection_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            config = build_config(source, output, selection=None)

            def interrupt(*_args, **_kwargs):
                raise KeyboardInterrupt

            runtime_patch, device_patch = mocked_runtime()
            with runtime_patch, device_patch, patch.object(cull, "_selection_count", interrupt):
                exit_code = cull.run_pipeline(config)

            run_dir = latest_run(output)
            manifest = read_manifest(run_dir)

            self.assertEqual(exit_code, 130)
            self.assertEqual(manifest["status"], "interrupted")
            # The expensive metrics are on disk despite never reaching selection.
            evaluation = run_dir / "evaluation.csv"
            self.assertTrue(evaluation.is_file())
            rows = read_rows(evaluation)
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["composite_score"] for row in rows))
            self.assertEqual(manifest["outputs"]["evaluation"], str(evaluation))

    def test_a_bad_select_value_never_starts_the_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            config = build_config(source, output, selection="3O", debug=False)

            def explode(*_args, **_kwargs):
                raise AssertionError("evaluation must not begin with an invalid --select")

            with patch("model_runtime.ModelRuntime", explode), self.assertRaises(ValueError):
                cull.run_pipeline(config)

            # No run directory is created, so nothing is written to the output root.
            self.assertEqual(list(output.glob("run_*")), [])


if __name__ == "__main__":
    unittest.main()
