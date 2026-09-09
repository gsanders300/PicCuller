import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

import cull


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


class PipelineIntegrationTests(unittest.TestCase):
    def test_noninteractive_run_creates_audited_review_and_safe_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photos"
            output = root / "output"
            (source / "day-one").mkdir(parents=True)
            (source / "day-two").mkdir(parents=True)
            Image.new("RGB", (64, 48), "red").save(source / "day-one" / "same.jpg")
            Image.new("RGB", (64, 48), "blue").save(source / "day-two" / "same.jpg")

            config = cull.PipelineConfig(
                input_folder=source,
                output_folder=output,
                time_window=2.0,
                max_burst_duration=10.0,
                phash_threshold=8,
                sim_threshold=0.88,
                no_group=True,
                cache_mode="none",
                metadata_backend="pillow",
                assumed_timezone="UTC",
                device="cpu",
                batch_size=2,
                workers=2,
                mixed_precision=False,
                preset="balanced",
                primary="all",
                selection="all",
                diversity=0.0,
                feedback_file=None,
                contact_sheet_count=2,
                write_xmp=True,
                aesthetic_head=None,
                plain=True,
                debug=True,
            )

            with (
                patch("model_runtime.ModelRuntime", FakeModelRuntime),
                patch(
                    "model_runtime.resolve_device",
                    return_value=SimpleNamespace(type="cpu"),
                ),
            ):
                exit_code = cull.run_pipeline(config)

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


if __name__ == "__main__":
    unittest.main()
