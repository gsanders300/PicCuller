"""Versioned model identities without importing the ML runtime."""

from __future__ import annotations

import hashlib
from pathlib import Path

CLIP_MODEL_ID = "openai/clip-vit-large-patch14"
CLIP_MODEL_REVISION = "32bd64288804d66eefd0ccbe215aa642df71cc41"
MUSIQ_MODEL_ID = "musiq"
MUSIQ_WEIGHTS_ID = "musiq_koniq_ckpt-e95806b9.pth"
MUSIQ_MODEL_REVISION = "0df2df423c65f6a64209309695f3845727431027"
MUSIQ_WEIGHTS_URL = (
    "https://huggingface.co/chaofengc/IQA-PyTorch-Weights/resolve/"
    f"{MUSIQ_MODEL_REVISION}/{MUSIQ_WEIGHTS_ID}"
)
MUSIQ_WEIGHTS_SHA256 = "e95806b9eae5f3814c410f574ba8e552362bd5bc63d758ed5b97860f5d6185aa"
AESTHETIC_MODEL_REVISION = "6934dd81792f086e613a121dbce43082cb8be85e"
AESTHETIC_WEIGHTS_NAME = "sac+logos+ava1-l14-linearMSE.pth"
AESTHETIC_WEIGHTS_URL = (
    "https://raw.githubusercontent.com/christophschuhmann/"
    "improved-aesthetic-predictor/"
    f"{AESTHETIC_MODEL_REVISION}/sac%2Blogos%2Bava1-l14-linearMSE.pth"
)
AESTHETIC_WEIGHTS_SHA256 = "21dd590f3ccdc646f0d53120778b296013b096a035a2718c9cb0d511bff0f1e0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
