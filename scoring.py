"""Deterministic burst grouping and score composition."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

ABSOLUTE_FOCUS_FLOOR = 0.5


@dataclass(frozen=True, slots=True)
class ScoringProfile:
    name: str
    absolute_focus_floor: float = ABSOLUTE_FOCUS_FLOOR
    absolute_focus_weight: float = 1.0
    relative_focus_exponent: float = 1.5
    musiq_weight: float = 1.0
    exposure_weight: float = 1.0
    eye_weight: float = 0.0
    subject_weight: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


SCORING_PROFILES = {
    "balanced": ScoringProfile(name="balanced"),
    "wildlife": ScoringProfile(
        name="wildlife",
        absolute_focus_floor=0.55,
        absolute_focus_weight=0.8,
        relative_focus_exponent=1.8,
        exposure_weight=0.8,
        subject_weight=0.2,
    ),
    "portrait": ScoringProfile(
        name="portrait",
        absolute_focus_floor=0.6,
        absolute_focus_weight=0.7,
        relative_focus_exponent=1.6,
        exposure_weight=0.8,
        eye_weight=0.35,
        subject_weight=0.15,
    ),
    "landscape": ScoringProfile(
        name="landscape",
        absolute_focus_floor=0.45,
        absolute_focus_weight=1.2,
        relative_focus_exponent=1.2,
        musiq_weight=1.1,
        exposure_weight=1.2,
        # advanced_analysis.SUBJECT_PROMPTS defines landscape prompts, so the
        # score was computed and reported at every run and then multiplied by a
        # zero weight. Uncalibrated, like every other weight here.
        subject_weight=0.15,
    ),
}


def get_scoring_profile(name: str) -> ScoringProfile:
    try:
        return SCORING_PROFILES[name]
    except KeyError as error:
        choices = ", ".join(sorted(SCORING_PROFILES))
        raise ValueError(f"Unknown scoring preset {name!r}; choose from {choices}") from error


def assign_session_focus_factors(
    records: list[dict[str, Any]],
    profile: ScoringProfile | None = None,
) -> None:
    """Add rank-based focus factors so standalone blur affects final ranking.

    Absolute Laplacian values are highly content-dependent, so the factor uses an
    empirical percentile within the current session and is capped to a moderate
    0.5-1.0 range. Within-burst focus comparison remains the stronger signal.
    """
    if not records:
        return

    profile = profile or SCORING_PROFILES["balanced"]
    scores = [max(0.0, float(record["focus_score"])) for record in records]
    sorted_scores = sorted(scores)
    count = len(sorted_scores)

    for record, score in zip(records, scores, strict=True):
        left = bisect_left(sorted_scores, score)
        right = bisect_right(sorted_scores, score)
        average_one_based_rank = ((left + 1) + right) / 2.0
        percentile = average_one_based_rank / count
        record["focus_percentile"] = percentile
        record["absolute_focus_factor"] = (
            profile.absolute_focus_floor + (1.0 - profile.absolute_focus_floor) * percentile
        )


def calculate_composite_score(
    record: dict[str, Any],
    relative_focus_ratio: float = 1.0,
    profile: ScoringProfile | None = None,
) -> float:
    """Combine bounded technical gates with the aesthetic score."""
    profile = profile or SCORING_PROFILES["balanced"]
    aesthetic = max(0.0, float(record["aesthetic_score"]))
    musiq_factor = _clamp(float(record["musiq_score"]) / 100.0)
    exposure_factor = _clamp(float(record["exposure_penalty"]))
    absolute_focus_factor = _clamp(float(record.get("absolute_focus_factor", 1.0)))
    eye_factor = _clamp(float(record.get("eye_factor", 1.0)))
    subject_factor = _clamp(float(record.get("subject_integrity", 1.0)))
    relative_focus_factor = _clamp(float(relative_focus_ratio)) ** profile.relative_focus_exponent
    return (
        aesthetic
        * absolute_focus_factor**profile.absolute_focus_weight
        * relative_focus_factor
        * musiq_factor**profile.musiq_weight
        * exposure_factor**profile.exposure_weight
        * eye_factor**profile.eye_weight
        * subject_factor**profile.subject_weight
    )


def group_bursts(
    records: list[dict[str, Any]],
    time_window_seconds: float = 2.0,
    phash_threshold: int = 8,
    sim_threshold: float = 0.88,
    max_burst_duration_seconds: float = 10.0,
    profile: ScoringProfile | None = None,
) -> list[dict[str, Any]]:
    """Cluster photos using timestamps, perceptual hash, and CLIP similarity."""
    records.sort(
        key=lambda record: (
            _timestamp_sort_key(record["timestamp"]),
            _sequence_sort_key(record.get("sequence_number")),
            str(record["file_path"]).casefold(),
        )
    )
    clusters: list[list[dict[str, Any]]] = []
    current_cluster: list[dict[str, Any]] = []

    for item in records:
        if not current_cluster:
            current_cluster.append(item)
            continue

        previous = current_cluster[-1]
        elapsed_seconds = (item["timestamp"] - previous["timestamp"]).total_seconds()

        burst_duration = (item["timestamp"] - current_cluster[0]["timestamp"]).total_seconds()
        same_camera = _same_camera(item, previous)

        if (
            same_camera
            and 0.0 <= elapsed_seconds <= time_window_seconds
            and 0.0 <= burst_duration <= max_burst_duration_seconds
        ):
            if item["phash"] - previous["phash"] <= phash_threshold:
                current_cluster.append(item)
                continue

            # Embeddings are normalized during inference, so a dot product is
            # equivalent to cosine similarity without scikit-learn overhead.
            similarity = sum(
                float(current_value) * float(previous_value)
                for current_value, previous_value in zip(
                    item["embedding"],
                    previous["embedding"],
                    strict=True,
                )
            )
            if similarity >= sim_threshold:
                current_cluster.append(item)
                continue

        clusters.append(current_cluster)
        current_cluster = [item]

    if current_cluster:
        clusters.append(current_cluster)

    labeled_records: list[dict[str, Any]] = []
    for cluster_id, cluster in enumerate(clusters, start=1):
        max_focus = max(float(record["focus_score"]) for record in cluster)
        for record in cluster:
            record["burst_id"] = cluster_id
            record["burst_size"] = len(cluster)
            focus_ratio = float(record["focus_score"]) / max_focus if max_focus > 0 else 1.0
            active_profile = profile or SCORING_PROFILES["balanced"]
            record["relative_focus_factor"] = focus_ratio**active_profile.relative_focus_exponent
            record["composite_score"] = calculate_composite_score(
                record,
                focus_ratio,
                active_profile,
            )
            labeled_records.append(record)

    return labeled_records


def _timestamp_sort_key(timestamp: datetime) -> datetime:
    return timestamp


def _same_camera(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_serial = str(first.get("camera_serial") or "")
    second_serial = str(second.get("camera_serial") or "")
    if first_serial and second_serial:
        return first_serial == second_serial
    first_model = str(first.get("camera_model") or "")
    second_model = str(second.get("camera_model") or "")
    return not first_model or not second_model or first_model == second_model


def _sequence_sort_key(value: object) -> tuple[int, int | str]:
    text = str(value or "")
    try:
        return 0, int(text)
    except ValueError:
        return 1, text.casefold()


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(value, maximum))
