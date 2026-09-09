"""Deterministic burst grouping and score composition."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime
from typing import Any


ABSOLUTE_FOCUS_FLOOR = 0.5


def assign_session_focus_factors(records: list[dict[str, Any]]) -> None:
    """Add rank-based focus factors so standalone blur affects final ranking.

    Absolute Laplacian values are highly content-dependent, so the factor uses an
    empirical percentile within the current session and is capped to a moderate
    0.5-1.0 range. Within-burst focus comparison remains the stronger signal.
    """
    if not records:
        return

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
            ABSOLUTE_FOCUS_FLOOR
            + (1.0 - ABSOLUTE_FOCUS_FLOOR) * percentile
        )


def calculate_composite_score(
    record: dict[str, Any],
    relative_focus_ratio: float = 1.0,
) -> float:
    """Combine bounded technical gates with the aesthetic score."""
    aesthetic = max(0.0, float(record["aesthetic_score"]))
    musiq_factor = _clamp(float(record["musiq_score"]) / 100.0)
    exposure_factor = _clamp(float(record["exposure_penalty"]))
    absolute_focus_factor = _clamp(float(record.get("absolute_focus_factor", 1.0)))
    relative_focus_factor = _clamp(float(relative_focus_ratio)) ** 1.5
    return (
        aesthetic
        * absolute_focus_factor
        * relative_focus_factor
        * musiq_factor
        * exposure_factor
    )


def group_bursts(
    records: list[dict[str, Any]],
    time_window_seconds: float = 2.0,
    phash_threshold: int = 8,
    sim_threshold: float = 0.88,
) -> list[dict[str, Any]]:
    """Cluster photos using timestamps, perceptual hash, and CLIP similarity."""
    records.sort(
        key=lambda record: (
            _timestamp_sort_key(record["timestamp"]),
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

        if 0.0 <= elapsed_seconds <= time_window_seconds:
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
            focus_ratio = (
                float(record["focus_score"]) / max_focus if max_focus > 0 else 1.0
            )
            record["relative_focus_factor"] = focus_ratio**1.5
            record["composite_score"] = calculate_composite_score(record, focus_ratio)
            labeled_records.append(record)

    return labeled_records


def _timestamp_sort_key(timestamp: datetime) -> datetime:
    return timestamp


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(value, maximum))
