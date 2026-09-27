import csv
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image
from rich.console import Console

import cull
from evaluation_cache import EvaluationCache
from thumbnail_store import ThumbnailStore


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
    def test_discovery_interrupt_returns_130_without_a_traceback_or_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            source.mkdir()
            stream = io.StringIO()
            test_console = Console(file=stream, force_terminal=False, no_color=True)

            with (
                patch.object(cull, "console", test_console),
                patch.object(cull, "discover_image_files", side_effect=KeyboardInterrupt),
            ):
                exit_code = cull.run_pipeline(build_config(source, output))

            self.assertEqual(exit_code, 130)
            self.assertFalse(output.exists())
            self.assertIn("Interrupted during discovery", stream.getvalue())
            self.assertNotIn("Traceback", stream.getvalue())

    def test_an_empty_readable_collection_returns_success_without_an_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            source.mkdir()

            exit_code = cull.run_pipeline(build_config(source, output))

            self.assertEqual(exit_code, 0)
            self.assertFalse(output.exists())

    def test_discovery_failure_without_images_is_audited_and_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            source.mkdir()

            def fail_discovery(_folder, _output_root, on_error):
                on_error(PermissionError(13, "Permission denied", str(source)))
                return []

            with patch.object(cull, "discover_image_files", fail_discovery):
                exit_code = cull.run_pipeline(build_config(source, output))

            run_dir = latest_run(output)
            manifest = read_manifest(run_dir)
            failures = read_rows(run_dir / "failures.csv")
            self.assertEqual(exit_code, 1)
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["counts"]["discovered"], 0)
            self.assertEqual([row["stage"] for row in failures], ["discovery"])

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

    def test_plain_run_prints_durable_progress_and_the_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            stream = io.StringIO()
            test_console = Console(
                file=stream,
                force_terminal=False,
                no_color=True,
                width=50,
            )
            config = build_config(
                source,
                output,
                selection="none",
                contact_sheet_count=0,
                write_xmp=False,
                plain=True,
            )

            with patch.object(cull, "console", test_console):
                exit_code = run_with_mocks(config)

            rendered = stream.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Run directory:", rendered)
            self.assertIn("Evaluation: 2/2 processed (100%)", rendered)
            self.assertIn("2 successful", rendered)
            self.assertNotIn("\x1b", rendered)
            self.assertLess(rendered.index("Run directory:"), rendered.index("Evaluation:"))

    def test_the_run_ends_with_the_export_list_and_every_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            stream = io.StringIO()
            test_console = Console(file=stream, force_terminal=False, no_color=True, width=120)
            config = build_config(source, output, selection="1", write_xmp=False)

            with patch.object(cull, "console", test_console):
                exit_code = run_with_mocks(config)

            rendered = stream.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Top Burst Winners", rendered)
            self.assertIn(str(Path("day-one") / "same.jpg"), rendered)
            self.assertIn("Exported Photos", rendered)
            self.assertIn("burst winner #1", rendered)
            finished = rendered[rendered.index("Run finished:") :]
            for name in ("evaluation.csv", "review.html", "picks/"):
                self.assertIn(name, finished)
            self.assertNotIn("failures.csv", finished)

    def test_evaluation_csv_explains_every_photo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)

            self.assertEqual(run_with_mocks(build_config(source, output, no_group=False)), 0)

            rows = read_rows(latest_run(output) / "evaluation.csv")
            self.assertEqual(len(rows), 2)
            for row in rows:
                self.assertTrue(row["score_reason"])
                product = float(row["aesthetic_score"])
                for field in cull.SCORE_MULTIPLIER_FIELDS:
                    product *= float(row[field])
                self.assertEqual(product, float(row["composite_score"]))

    def test_the_review_page_marks_exports_and_shows_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)

            self.assertEqual(run_with_mocks(build_config(source, output, selection="1")), 0)

            page = (latest_run(output) / "review.html").read_text(encoding="utf-8")
            self.assertEqual(page.count('<span class="badge">Exported</span>'), 1)
            self.assertIn("2 photos, 1 exported", page)
            self.assertIn('<p class="reason">', page)
            self.assertNotIn("<h2># ", page)


class StageInstrumentationTests(unittest.TestCase):
    """Per-phase wall clock overlaps, so throughput work needs per-stage totals."""

    def test_run_json_reports_per_stage_seconds_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)

            self.assertEqual(run_with_mocks(build_config(source, output)), 0)
            manifest = read_manifest(latest_run(output))

            for stage in ("decode", "cpu_metrics", "phash", "musiq", "clip"):
                with self.subTest(stage=stage):
                    self.assertIn(stage, manifest["stage_seconds"])
                    self.assertGreaterEqual(manifest["stage_seconds"][stage], 0.0)
                    self.assertEqual(manifest["stage_counts"][stage], 2)

    def test_a_cached_run_records_no_evaluation_stages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            config = build_config(source, output, cache_mode="use", selection="none")

            run_with_mocks(config)
            run_with_mocks(config)

            self.assertEqual(read_manifest(latest_run(output))["stage_seconds"], {})


class DiscoveryReportingTests(unittest.TestCase):
    def test_appledouble_and_system_directories_never_reach_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            # Both are routine on a Mac-written exFAT card and neither is an image.
            (source / "day-one" / "._same.jpg").write_bytes(b"appledouble stub")
            trashes = source / ".Trashes"
            trashes.mkdir()
            Image.new("RGB", (32, 24), "black").save(trashes / "deleted.jpg")

            self.assertEqual(run_with_mocks(build_config(source, output)), 0)
            run_dir = latest_run(output)
            manifest = read_manifest(run_dir)

            self.assertEqual(manifest["counts"]["discovered"], 2)
            self.assertEqual(manifest["counts"]["evaluated"], 2)
            self.assertEqual(read_rows(run_dir / "failures.csv"), [])


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

    def test_an_algorithm_version_bump_invalidates_the_cache(self) -> None:
        """Changing metric meaning must force re-evaluation, not reuse old values."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            config = build_config(source, output, cache_mode="use", selection="none")

            run_with_mocks(config)
            self.assertEqual(read_manifest(latest_run(output))["counts"]["cached"], 0)

            # Same settings, same files, later algorithm version.
            with patch.object(cull, "EVALUATION_ALGORITHM_VERSION", "999"):
                run_with_mocks(config)

            counts = read_manifest(latest_run(output))["counts"]
            self.assertEqual(counts["cached"], 0)
            self.assertEqual(counts["evaluated"], 2)

    def test_the_algorithm_version_is_part_of_the_cache_identity(self) -> None:
        config = build_config(Path("/photos"), Path("/out"))
        signature = cull._cache_signature(
            config,
            "pillow",
            {
                "clip": "clip@rev",
                "musiq": "musiq@rev",
                "musiq_sha256": "aa",
                "aesthetic_sha256": "bb",
            },
        )

        self.assertIn(f"algorithm={cull.EVALUATION_ALGORITHM_VERSION}", signature)

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


class ThumbnailReuseTests(unittest.TestCase):
    """A fully cached repeat run must perform no decode at all."""

    def test_a_repeat_run_reuses_every_thumbnail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            config = build_config(
                source, output, cache_mode="use", selection="none", contact_sheet_count=2
            )

            self.assertEqual(run_with_mocks(config), 0)
            first = read_manifest(latest_run(output))["counts"]
            self.assertEqual(first["thumbnails_decoded"], 2)
            self.assertEqual(first["thumbnails_reused"], 0)

            self.assertEqual(run_with_mocks(config), 0)
            second_dir = latest_run(output)
            second = read_manifest(second_dir)["counts"]

            self.assertEqual(second["evaluated"], 0)
            self.assertEqual(second["thumbnails_decoded"], 0)
            self.assertEqual(second["thumbnails_reused"], 2)
            # No decode anywhere in the run.
            self.assertEqual(read_manifest(second_dir)["stage_seconds"], {})

    def test_the_review_page_still_has_its_own_thumbnails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            config = build_config(
                source, output, cache_mode="use", selection="none", contact_sheet_count=2
            )

            run_with_mocks(config)
            run_with_mocks(config)
            run_dir = latest_run(output)
            page = (run_dir / "review.html").read_text(encoding="utf-8")

            # review.html keeps referring to its own directory, so the run stays
            # self-contained even though the bytes are shared.
            self.assertIn('src="thumbnails/0001.jpg"', page)
            for name in ("0001.jpg", "0002.jpg"):
                self.assertTrue((run_dir / "thumbnails" / name).is_file())
            self.assertTrue((output / "thumbnails").is_dir())

    def test_an_edited_source_regenerates_its_thumbnail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            config = build_config(
                source, output, cache_mode="use", selection="none", contact_sheet_count=2
            )

            run_with_mocks(config)
            Image.new("RGB", (96, 72), "purple").save(source / "day-one" / "same.jpg")
            run_with_mocks(config)

            counts = read_manifest(latest_run(output))["counts"]

            self.assertEqual(counts["thumbnails_decoded"], 1)
            self.assertEqual(counts["thumbnails_reused"], 1)

    def test_a_thumbnail_failure_is_recorded_and_does_not_stop_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            config = build_config(
                source, output, cache_mode="use", selection="none", contact_sheet_count=2
            )

            def refuse(*_args, **_kwargs):
                raise OSError("cannot decode")

            runtime_patch, device_patch = mocked_runtime()
            with runtime_patch, device_patch, patch.object(ThumbnailStore, "thumbnail", refuse):
                exit_code = cull.run_pipeline(config)

            run_dir = latest_run(output)
            stages = {row["stage"] for row in read_rows(run_dir / "failures.csv")}

            self.assertEqual(exit_code, 0)
            self.assertIn("contact_sheet", stages)
            self.assertEqual(read_manifest(run_dir)["counts"]["contact_sheet_images"], 0)


class PresetSwitchTests(unittest.TestCase):
    """Comparing presets must reuse the evaluation, not repeat it."""

    def test_switching_to_balanced_needs_no_model_at_all(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)

            wildlife = build_config(
                source, output, cache_mode="use", selection="none", preset="wildlife"
            )
            self.assertEqual(run_with_mocks(wildlife), 0)
            self.assertEqual(read_manifest(latest_run(output))["counts"]["evaluated"], 2)

            # Balanced defines no subject prompts, so its score is a constant and
            # the run must not construct a model runtime.
            def explode(*_args, **_kwargs):
                raise AssertionError("switching to balanced must not load a model")

            balanced = build_config(
                source, output, cache_mode="use", selection="none", preset="balanced"
            )
            with (
                patch("model_runtime.ModelRuntime", explode),
                patch("model_runtime.SubjectScorer", explode),
            ):
                self.assertEqual(cull.run_pipeline(balanced), 0)

            counts = read_manifest(latest_run(output))["counts"]
            self.assertEqual(counts["evaluated"], 0)
            self.assertEqual(counts["cached"], 2)
            self.assertEqual(counts["preset_refreshed"], 2)

    def test_switching_to_a_prompted_preset_scores_from_cached_embeddings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)

            balanced = build_config(
                source, output, cache_mode="use", selection="none", preset="balanced"
            )
            run_with_mocks(balanced)

            class FakeSubjectScorer:
                def __init__(self, device, preset):
                    self.preset = preset

                def score(self, embeddings):
                    return [0.75] * len(embeddings)

            def explode(*_args, **_kwargs):
                raise AssertionError("a preset switch must not run image inference")

            landscape = build_config(
                source, output, cache_mode="use", selection="none", preset="landscape"
            )
            with (
                patch("model_runtime.SubjectScorer", FakeSubjectScorer),
                patch("model_runtime.ModelRuntime", explode),
                patch("cull._prepare_image", explode),
                patch(
                    "model_runtime.resolve_device",
                    return_value=SimpleNamespace(type="cpu"),
                ),
            ):
                self.assertEqual(cull.run_pipeline(landscape), 0)

            run_dir = latest_run(output)
            counts = read_manifest(run_dir)["counts"]
            rows = read_rows(run_dir / "evaluation.csv")

            self.assertEqual(counts["evaluated"], 0)
            self.assertEqual(counts["preset_refreshed"], 2)
            self.assertTrue(all(row["subject_integrity"] == "0.75" for row in rows))

    def test_prompted_preset_refresh_retries_device_failures_on_cpu(self) -> None:
        def subject_scorer_type(failure_point, devices_seen):
            class FailingAcceleratorSubjectScorer:
                def __init__(self, device, _preset):
                    self.device = device
                    devices_seen.append(device.type)
                    if failure_point == "initialization" and device.type == "mps":
                        raise RuntimeError("MPS operation is not implemented")

                def score(self, embeddings):
                    if failure_point == "score" and self.device.type == "mps":
                        raise RuntimeError("MPS operation is not implemented")
                    return [0.75] * len(embeddings)

            return FailingAcceleratorSubjectScorer

        for failure_point in ("initialization", "score"):
            with (
                self.subTest(failure_point=failure_point),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                root = Path(temporary_directory).resolve()
                source = root / "photos"
                output = root / "output"
                make_photos(source)
                run_with_mocks(
                    build_config(
                        source,
                        output,
                        cache_mode="use",
                        selection="none",
                        preset="balanced",
                    )
                )

                devices_seen = []
                scorer_type = subject_scorer_type(failure_point, devices_seen)

                def resolve_device(preference):
                    return SimpleNamespace(type="cpu" if preference == "cpu" else "mps")

                landscape = build_config(
                    source,
                    output,
                    cache_mode="use",
                    selection="none",
                    preset="landscape",
                    device="auto",
                )
                with (
                    patch("model_runtime.SubjectScorer", scorer_type),
                    patch("model_runtime.resolve_device", side_effect=resolve_device),
                    patch.object(cull, "_show_environment") as show_environment,
                ):
                    exit_code = cull.run_pipeline(landscape)

                run_dir = latest_run(output)
                manifest = read_manifest(run_dir)
                failures = read_rows(run_dir / "failures.csv")
                self.assertEqual(exit_code, 0)
                self.assertEqual(devices_seen, ["mps", "cpu"])
                self.assertEqual(manifest["environment"]["resolved_device"], "cpu")
                self.assertIn("model_device_fallback", {row["stage"] for row in failures})
                self.assertEqual(show_environment.call_args.args[3], "cpu")

    def test_the_refreshed_preset_is_cached_for_the_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)

            run_with_mocks(
                build_config(source, output, cache_mode="use", selection="none", preset="wildlife")
            )
            balanced = build_config(
                source, output, cache_mode="use", selection="none", preset="balanced"
            )
            cull.run_pipeline(balanced)
            self.assertEqual(
                read_manifest(latest_run(output))["counts"]["preset_refreshed"], 2
            )

            # The third run is a plain cache hit. preset_refreshed staying at zero
            # is the proof that the refreshed scores were persisted.
            def explode(*_args, **_kwargs):
                raise AssertionError("a persisted preset score must not be recomputed")

            with patch("model_runtime.SubjectScorer", explode):
                cull.run_pipeline(balanced)
            counts = read_manifest(latest_run(output))["counts"]

            self.assertEqual(counts["cached"], 2)
            self.assertEqual(counts.get("preset_refreshed", 0), 0)
            self.assertEqual(counts["evaluated"], 0)

    def test_portrait_still_needs_the_pixels(self) -> None:
        """Face and eye detection cannot come from a cached embedding."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)

            run_with_mocks(
                build_config(source, output, cache_mode="use", selection="none", preset="balanced")
            )
            run_with_mocks(
                build_config(source, output, cache_mode="use", selection="none", preset="portrait")
            )

            counts = read_manifest(latest_run(output))["counts"]

            self.assertEqual(counts["evaluated"], 2)
            self.assertEqual(counts["cached"], 0)

    def test_the_preset_is_not_part_of_the_shared_signature(self) -> None:
        config = build_config(Path("/photos"), Path("/out"), preset="wildlife")
        other = build_config(Path("/photos"), Path("/out"), preset="landscape")
        manifest = {
            "clip": "clip@rev",
            "musiq": "musiq@rev",
            "musiq_sha256": "aa",
            "aesthetic_sha256": "bb",
        }

        self.assertEqual(
            cull._cache_signature(config, "pillow", manifest),
            cull._cache_signature(other, "pillow", manifest),
        )
        self.assertNotIn("preset=", cull._cache_signature(config, "pillow", manifest))


class CacheWriteFailureTests(unittest.TestCase):
    def test_a_failed_cache_write_keeps_the_completed_evaluation(self) -> None:
        """A SQLite fault must cost the cache row, not the computed record."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            make_photos(source)
            config = build_config(source, output, cache_mode="use", selection="all")

            def broken_store(*_args, **_kwargs):
                raise sqlite3.OperationalError("database is locked")

            runtime_patch, device_patch = mocked_runtime()
            with runtime_patch, device_patch, patch.object(EvaluationCache, "store", broken_store):
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
