"""Optional heuristic analysis that augments, but does not replace, core scores."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

SUBJECT_PROMPTS = {
    "wildlife": (
        "a wildlife photograph with the complete animal clearly in frame",
        "a wildlife photograph where the animal is cut off or leaving the frame",
    ),
    "portrait": (
        "a well composed portrait with a clear face and open eyes",
        "a portrait with a hidden face, closed eyes, or awkward crop",
    ),
    "landscape": (
        "a compelling well composed landscape photograph",
        "a poorly composed accidental landscape snapshot",
    ),
}


@dataclass(frozen=True, slots=True)
class PortraitAnalysis:
    face_count: int
    eye_count: int
    eye_factor: float
    warning: str


def analyze_portrait(cv_image: Any) -> PortraitAnalysis:
    """Use bundled OpenCV cascades as a lightweight, optional eye warning."""
    import cv2

    face_cascade, eye_cascade = _portrait_classifiers()
    gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(40, 40),
    )
    eye_count = 0
    for x, y, width, height in faces:
        upper_face = gray[y : y + int(height * 0.65), x : x + width]
        eyes = eye_cascade.detectMultiScale(
            upper_face,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(12, 12),
        )
        eye_count += len(eyes)

    face_count = len(faces)
    if face_count == 0:
        return PortraitAnalysis(0, 0, 0.85, "No face detected")
    if eye_count < face_count * 2:
        return PortraitAnalysis(face_count, eye_count, 0.7, "Possible closed/obscured eyes")
    return PortraitAnalysis(face_count, eye_count, 1.0, "")


def subject_integrity_scores(
    image_embeddings: Any,
    positive_embedding: Any,
    negative_embedding: Any,
) -> list[float]:
    """Return two-prompt probabilities from normalized CLIP embeddings."""
    import torch

    positive = image_embeddings @ positive_embedding.T
    negative = image_embeddings @ negative_embedding.T
    logits = torch.stack((positive, negative), dim=-1) * 10.0
    probabilities = logits.softmax(dim=-1)[..., 0]
    return [float(value) for value in probabilities.detach().cpu().flatten()]


@lru_cache(maxsize=1)
def _portrait_classifiers() -> tuple[Any, Any]:
    import cv2

    base = cv2.data.haarcascades
    face = cv2.CascadeClassifier(base + "haarcascade_frontalface_default.xml")
    eyes = cv2.CascadeClassifier(base + "haarcascade_eye_tree_eyeglasses.xml")
    if face.empty() or eyes.empty():
        raise RuntimeError("OpenCV portrait classifiers could not be loaded")
    return face, eyes
