"""Pinned model loading, verified weights, and adaptive inference."""

from __future__ import annotations

import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pyiqa
import requests
import torch
from platformdirs import user_cache_dir
from torch import nn
from transformers import CLIPModel, CLIPProcessor

from advanced_analysis import SUBJECT_PROMPTS, subject_integrity_scores
from model_config import (
    AESTHETIC_WEIGHTS_NAME,
    AESTHETIC_WEIGHTS_SHA256,
    AESTHETIC_WEIGHTS_URL,
    CLIP_MODEL_ID,
    CLIP_MODEL_REVISION,
    MUSIQ_MODEL_ID,
    MUSIQ_WEIGHTS_ID,
    MUSIQ_WEIGHTS_SHA256,
    MUSIQ_WEIGHTS_URL,
    sha256_file,
)


class AestheticPredictor(nn.Module):
    def __init__(self, input_dim: int = 768):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(input_dim, 1024),
            nn.Dropout(0.2),
            nn.Linear(1024, 128),
            nn.Dropout(0.2),
            nn.Linear(128, 64),
            nn.Dropout(0.1),
            nn.Linear(64, 16),
            nn.Linear(16, 1),
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.layers(features)


@dataclass(slots=True)
class InferenceResult:
    aesthetic_score: float
    embedding: np.ndarray
    subject_integrity: float


class ModelRuntime:
    def __init__(
        self,
        device: torch.device,
        *,
        preset: str,
        aesthetic_weights: Path | None = None,
        mixed_precision: bool = True,
    ) -> None:
        self.device = device
        self.mixed_precision = mixed_precision
        self.clip_model = (
            CLIPModel.from_pretrained(
                CLIP_MODEL_ID,
                revision=CLIP_MODEL_REVISION,
            )
            .to(device)
            .eval()
        )
        self.clip_processor = CLIPProcessor.from_pretrained(
            CLIP_MODEL_ID,
            revision=CLIP_MODEL_REVISION,
            use_fast=False,
        )
        self.aesthetic_head, self.aesthetic_sha256 = load_aesthetic_head(
            device,
            aesthetic_weights,
        )
        musiq_weights = verified_cached_file(
            MUSIQ_WEIGHTS_ID,
            MUSIQ_WEIGHTS_URL,
            MUSIQ_WEIGHTS_SHA256,
        )
        self.musiq_sha256 = sha256_file(musiq_weights)
        self.musiq_metric = pyiqa.create_metric(
            MUSIQ_MODEL_ID,
            device=device,
            pretrained_model_path=str(musiq_weights),
        )
        self.subject_text_features = self._load_subject_features(preset)

    def infer_clip_batch(self, images: list[Any]) -> list[InferenceResult]:
        if not images:
            return []
        try:
            return self._infer_clip_batch(images)
        except RuntimeError as error:
            if len(images) > 1 and _is_memory_error(error):
                _empty_device_cache(self.device)
                midpoint = len(images) // 2
                return self.infer_clip_batch(images[:midpoint]) + self.infer_clip_batch(
                    images[midpoint:]
                )
            if self.device.type != "cpu" and _is_device_fallback_error(error):
                self.move_to_cpu()
                return self._infer_clip_batch(images)
            raise

    def infer_musiq(self, cv_image: np.ndarray) -> float:
        rgb = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB)
        tensor = (
            torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).float().div_(255.0).to(self.device)
        )
        try:
            with torch.inference_mode():
                return float(self.musiq_metric(tensor).item())
        except RuntimeError as error:
            if self.device.type != "cpu" and _is_device_fallback_error(error):
                del tensor
                self.move_to_cpu()
                return self.infer_musiq(cv_image)
            raise

    def move_to_cpu(self) -> None:
        if self.device.type == "cpu":
            return
        previous = self.device
        self.device = torch.device("cpu")
        self.clip_model.to(self.device)
        self.aesthetic_head.to(self.device)
        self.musiq_metric.to(self.device)
        if self.subject_text_features is not None:
            self.subject_text_features = self.subject_text_features.to(self.device)
        _empty_device_cache(previous)

    def _infer_clip_batch(self, images: list[Any]) -> list[InferenceResult]:
        inputs = self.clip_processor(images=images, return_tensors="pt").to(self.device)
        autocast_context = (
            torch.autocast(device_type="cuda", dtype=torch.float16)
            if self.mixed_precision and self.device.type == "cuda"
            else nullcontext()
        )
        with torch.inference_mode(), autocast_context:
            features = self.clip_model.get_image_features(**inputs)
            features = features / features.norm(p=2, dim=-1, keepdim=True)
            aesthetic_scores = self.aesthetic_head(features).flatten()
            if self.subject_text_features is not None:
                subject_scores = [
                    0.5 + 0.5 * score
                    for score in subject_integrity_scores(
                        features,
                        self.subject_text_features[0:1],
                        self.subject_text_features[1:2],
                    )
                ]
            else:
                subject_scores = [1.0] * len(images)

        embeddings = features.detach().float().cpu().numpy()
        scores = aesthetic_scores.detach().float().cpu().tolist()
        return [
            InferenceResult(float(score), embedding, subject_score)
            for score, embedding, subject_score in zip(
                scores,
                embeddings,
                subject_scores,
                strict=True,
            )
        ]

    def _load_subject_features(self, preset: str) -> torch.Tensor | None:
        prompts = SUBJECT_PROMPTS.get(preset)
        if prompts is None:
            return None
        inputs = self.clip_processor(text=list(prompts), return_tensors="pt", padding=True)
        inputs = inputs.to(self.device)
        with torch.inference_mode():
            features = self.clip_model.get_text_features(**inputs)
            return features / features.norm(p=2, dim=-1, keepdim=True)


def resolve_device(preference: str = "auto") -> torch.device:
    if preference == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is not available")
        return torch.device("cuda")
    if preference == "mps":
        if not getattr(torch.backends, "mps", None) or not torch.backends.mps.is_available():
            raise RuntimeError("MPS was requested but is not available")
        return torch.device("mps")
    if preference == "cpu":
        return torch.device("cpu")
    if preference != "auto":
        raise ValueError(f"Unsupported device: {preference}")
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def load_aesthetic_head(
    device: torch.device,
    custom_weights: Path | None = None,
) -> tuple[nn.Module, str]:
    if custom_weights is not None:
        weights_path = custom_weights.expanduser().resolve(strict=True)
        expected_hash = None
    else:
        weights_path = verified_cached_file(
            AESTHETIC_WEIGHTS_NAME,
            AESTHETIC_WEIGHTS_URL,
            AESTHETIC_WEIGHTS_SHA256,
        )
        expected_hash = AESTHETIC_WEIGHTS_SHA256

    actual_hash = sha256_file(weights_path)
    if expected_hash is not None and actual_hash != expected_hash:
        raise RuntimeError(
            f"Aesthetic model checksum mismatch at {weights_path}; remove the file and retry"
        )

    model = AestheticPredictor(input_dim=768)
    state = torch.load(weights_path, map_location=device, weights_only=True)
    model.load_state_dict(state)
    model.to(device).eval()
    return model, actual_hash


def verified_cached_file(name: str, url: str, expected_hash: str) -> Path:
    cache_directory = Path(user_cache_dir("photo-cull", "PhotoCull")) / "models"
    cache_directory.mkdir(parents=True, exist_ok=True)
    destination = cache_directory / name
    if destination.exists():
        if sha256_file(destination) != expected_hash:
            raise RuntimeError(
                f"Model checksum mismatch at {destination}; remove the file and retry"
            )
    else:
        _download_verified(url, destination, expected_hash)
    return destination


def _download_verified(url: str, destination: Path, expected_hash: str) -> None:
    with requests.get(url, stream=True, timeout=(15, 120)) as response:
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".part",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    try:
        if sha256_file(temporary_path) != expected_hash:
            raise RuntimeError("Downloaded aesthetic model failed checksum verification")
        temporary_path.replace(destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _is_memory_error(error: RuntimeError) -> bool:
    message = str(error).casefold()
    return "out of memory" in message or "allocate" in message


def _is_device_fallback_error(error: RuntimeError) -> bool:
    message = str(error).casefold()
    return _is_memory_error(error) or "not implemented for" in message or "mps" in message


def is_device_fallback_error(error: RuntimeError) -> bool:
    return _is_device_fallback_error(error)


def _empty_device_cache(device: torch.device) -> None:
    if device.type == "cuda":
        torch.cuda.empty_cache()
    elif device.type == "mps" and hasattr(torch.mps, "empty_cache"):
        torch.mps.empty_cache()
