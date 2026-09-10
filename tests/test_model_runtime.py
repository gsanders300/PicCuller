import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import torch

import model_runtime


class _FakeInputs(dict):
    def to(self, _device):
        return self


class _FakeTextModel:
    def to(self, _device):
        return self

    def eval(self):
        return self

    def __call__(self, **_inputs):
        return SimpleNamespace(
            text_embeds=torch.tensor(
                ((3.0, 4.0), (0.0, 2.0)),
                dtype=torch.float32,
            )
        )


class _FakeTokenizer:
    def __call__(self, **_kwargs):
        return _FakeInputs()


class _FakeResponse:
    def __init__(self, chunks, content_length: str | None = None) -> None:
        self.chunks = chunks
        self.headers = {} if content_length is None else {"content-length": content_length}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield from self.chunks


class SubjectScorerTests(unittest.TestCase):
    def test_subject_scorer_loads_only_the_clip_text_tower(self) -> None:
        with (
            patch.object(
                model_runtime.CLIPTextModelWithProjection,
                "from_pretrained",
                return_value=_FakeTextModel(),
            ) as load_text_model,
            patch.object(
                model_runtime.CLIPTokenizer,
                "from_pretrained",
                return_value=_FakeTokenizer(),
            ) as load_tokenizer,
            patch.object(model_runtime.CLIPModel, "from_pretrained") as load_full_model,
        ):
            scorer = model_runtime.SubjectScorer(torch.device("cpu"), "wildlife")

        load_text_model.assert_called_once_with(
            model_runtime.CLIP_MODEL_ID,
            revision=model_runtime.CLIP_MODEL_REVISION,
        )
        load_tokenizer.assert_called_once_with(
            model_runtime.CLIP_MODEL_ID,
            revision=model_runtime.CLIP_MODEL_REVISION,
        )
        load_full_model.assert_not_called()
        np.testing.assert_allclose(
            scorer.text_features.numpy(),
            np.asarray(((0.6, 0.8), (0.0, 1.0)), dtype=np.float32),
        )


class ModelDownloadTests(unittest.TestCase):
    def test_verified_download_reports_byte_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "weights.bin"
            payload = b"model-weights"
            expected_hash = hashlib.sha256(payload).hexdigest()
            progress = []

            with patch.object(
                model_runtime.requests,
                "get",
                return_value=_FakeResponse([payload[:5], payload[5:]], str(len(payload))),
            ):
                model_runtime._download_verified(
                    "https://example.invalid/weights.bin",
                    destination,
                    expected_hash,
                    lambda path, downloaded, total: progress.append(
                        (path, downloaded, total)
                    ),
                )

            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(progress[0], (destination, 0, len(payload)))
            self.assertEqual(progress[-1], (destination, len(payload), len(payload)))

    def test_failed_download_removes_its_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / "weights.bin"

            def fail_during_stream(chunk_size: int):
                del chunk_size
                yield b"partial"
                raise model_runtime.requests.ConnectionError("connection lost")

            response = _FakeResponse([])
            response.iter_content = fail_during_stream
            with (
                patch.object(model_runtime.requests, "get", return_value=response),
                self.assertRaises(model_runtime.requests.ConnectionError),
            ):
                model_runtime._download_verified(
                    "https://example.invalid/weights.bin",
                    destination,
                    "unused",
                )

            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob(".*.part")), [])


if __name__ == "__main__":
    unittest.main()
