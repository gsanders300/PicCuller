"""Deterministic burst grouping and score composition."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

ABSOLUTE_FOCUS_FLOOR = 0.5
# A penalty smaller than this is noise next to the uncalibrated weights, so the
# reason text leaves it out. evaluation.csv still carries every multiplier.
REASON_PENALTY_THRESHOLD = 0.95
REASON_STANDING_PERCENT = 25


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
    # Halved from 1.0 after one shoot's keep/reject feedback (73 keeps, 13 rejects):
    # whole-run sharpness was the weakest predictor and pushed liked photos with
    # little fine detail down. Keep/reject agreement rose from 74% to 79%, and the
    # top 20 still held 19 keeps. Still provisional until more shoots confirm it.
    "balanced": ScoringProfile(name="balanced", absolute_focus_weight=0.5),
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


def score_multipliers(
    record: dict[str, Any],
    relative_focus_ratio: float = 1.0,
    profile: ScoringProfile | None = None,
) -> dict[str, float]:
    """Return the preset-weighted factors that scale the aesthetic base.

    The composite is the aesthetic score times these values, in this order, so
    the report can show exactly how much each factor moved a photograph.
    """
    profile = profile or SCORING_PROFILES["balanced"]
    return {
        "absolute_focus_multiplier": _clamp(float(record.get("absolute_focus_factor", 1.0)))
        ** profile.absolute_focus_weight,
        "relative_focus_multiplier": _clamp(float(relative_focus_ratio))
        ** profile.relative_focus_exponent,
        "musiq_multiplier": _clamp(float(record["musiq_score"]) / 100.0) ** profile.musiq_weight,
        "exposure_multiplier": _clamp(float(record["exposure_penalty"]))
        ** profile.exposure_weight,
        "eye_multiplier": _clamp(float(record.get("eye_factor", 1.0))) ** profile.eye_weight,
        "subject_multiplier": _clamp(float(record.get("subject_integrity", 1.0)))
        ** profile.subject_weight,
    }


SCORE_MULTIPLIER_FIELDS = (
    "absolute_focus_multiplier",
    "relative_focus_multiplier",
    "musiq_multiplier",
    "exposure_multiplier",
    "eye_multiplier",
    "subject_multiplier",
)


def calculate_composite_score(
    record: dict[str, Any],
    relative_focus_ratio: float = 1.0,
    profile: ScoringProfile | None = None,
) -> float:
    """Combine bounded technical gates with the aesthetic score."""
    composite = max(0.0, float(record["aesthetic_score"]))
    for multiplier in score_multipliers(record, relative_focus_ratio, profile).values():
        composite *= multiplier
    return composite


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
            record.update(score_multipliers(record, focus_ratio, active_profile))
            record["composite_score"] = calculate_composite_score(
                record,
                focus_ratio,
                active_profile,
            )
            labeled_records.append(record)

    return labeled_records


def describe_score_reasons(records: list[dict[str, Any]], *, grouped: bool = True) -> None:
    """Add a plain-language `score_reason` to each ranked record.

    The reason states the burst decision, the photograph's standing among all
    candidates, and each penalty large enough to matter. Records must already
    carry burst ranks and score multipliers.
    """
    by_burst: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        by_burst.setdefault(int(record["burst_id"]), []).append(record)
    for group in by_burst.values():
        group.sort(key=lambda record: int(record["burst_rank"]))
    sorted_values = {
        field: sorted(float(record[field]) for record in records)
        for _, field in _STANDING_FIELDS
    }

    for record in records:
        clauses = []
        if grouped:
            clauses.append(_burst_clause(record, by_burst[int(record["burst_id"])]))
        clauses.extend(_standing_clauses(record, sorted_values))
        clauses.extend(_penalty_clauses(record))
        record["score_reason"] = "; ".join(clauses) or "no standout strength or penalty"


_STANDING_FIELDS = (
    ("aesthetic", "aesthetic_score"),
    ("sharpness", "focus_score"),
    ("technical quality", "musiq_score"),
)


def _burst_clause(record: dict[str, Any], group: list[dict[str, Any]]) -> str:
    size = len(group)
    if size == 1:
        return "single shot"
    rank = int(record["burst_rank"])
    if rank == 1:
        runner_up = group[1]
        gap = _score_gap(record, runner_up)
        if gap is None:
            return (
                f"best of {size} in burst; ties {runner_up['file_name']} on score "
                "and wins on path order"
            )
        amount, factor = gap
        return (
            f"best of {size} in burst; next best {runner_up['file_name']} "
            f"scored {amount} lower{factor}"
        )
    winner = group[0]
    position = f"{_ordinal(rank)} of {size} in burst"
    gap = _score_gap(winner, record)
    if gap is None:
        return f"{position}; ties {winner['file_name']} on score and loses on path order"
    amount, factor = gap
    return f"{position}; scored {amount} lower than {winner['file_name']}{factor}"


def _score_gap(better: dict[str, Any], worse: dict[str, Any]) -> tuple[str, str] | None:
    """Return how far `worse` trails `better`, and the factor that differs most."""
    high = float(better["composite_score"])
    low = float(worse["composite_score"])
    if low >= high:
        return None
    percent = 100.0 * (1.0 - low / high)
    if low <= 0.0:
        amount = "100%"
    elif percent >= 99.5:
        amount = "more than 99%"
    elif percent >= 1.0:
        amount = f"{percent:.0f}%"
    else:
        amount = "less than 1%"
    better_factors = _comparison_factors(better)
    worse_factors = _comparison_factors(worse)
    main_factor, largest_ratio = "", 1.0
    for name, value in better_factors.items():
        other = worse_factors[name]
        ratio = value / other if other > 0 else (float("inf") if value > 0 else 1.0)
        if ratio > largest_ratio:
            main_factor, largest_ratio = name, ratio
    return amount, f", mainly on {main_factor}" if main_factor else ""


def _comparison_factors(record: dict[str, Any]) -> dict[str, float]:
    """Group the composite's terms into the factors a photographer would name."""
    return {
        "aesthetic": max(0.0, float(record["aesthetic_score"])),
        "sharpness": float(record["absolute_focus_multiplier"])
        * float(record["relative_focus_multiplier"]),
        "technical quality": float(record["musiq_multiplier"]),
        "exposure": float(record["exposure_multiplier"]),
        "eye check": float(record["eye_multiplier"]),
        "subject check": float(record["subject_multiplier"]),
    }


def _standing_clauses(
    record: dict[str, Any],
    sorted_values: dict[str, list[float]],
) -> list[str]:
    """Name the strongest and weakest measurement when either is in a tail quarter."""
    tops: list[tuple[int, str]] = []
    bottoms: list[tuple[int, str]] = []
    for label, field in _STANDING_FIELDS:
        values = sorted_values[field]
        value = float(record[field])
        count = len(values)
        # Integer ceiling division keeps the percentage exact at each boundary.
        tops.append((-(-100 * (count - bisect_left(values, value)) // count), label))
        bottoms.append((-(-100 * bisect_right(values, value) // count), label))

    clauses = []
    top_percent, top_label = min(tops, key=lambda item: item[0])
    if top_percent <= REASON_STANDING_PERCENT:
        clauses.append(f"{top_label} in top {top_percent}% of all photos")
    else:
        top_label = ""
    remaining = [item for item in bottoms if item[1] != top_label]
    bottom_percent, bottom_label = min(remaining, key=lambda item: item[0])
    if bottom_percent <= REASON_STANDING_PERCENT:
        clauses.append(f"{bottom_label} in bottom {bottom_percent}% of all photos")
    return clauses


def _penalty_clauses(record: dict[str, Any]) -> list[str]:
    clauses = []
    exposure = float(record["exposure_multiplier"])
    if exposure <= REASON_PENALTY_THRESHOLD:
        clauses.append(
            f"clipping {_loss(exposure)}: {float(record['blown_pct']):.1f}% highlights blown, "
            f"{float(record['crushed_pct']):.1f}% shadows crushed"
        )
    eye = float(record["eye_multiplier"])
    warning = str(record.get("eye_warning") or "")
    warning = warning[:1].lower() + warning[1:]
    if eye <= REASON_PENALTY_THRESHOLD:
        clauses.append(f"{warning or 'eye check'} {_loss(eye)}")
    elif warning:
        clauses.append(warning)
    subject = float(record["subject_multiplier"])
    if subject <= REASON_PENALTY_THRESHOLD:
        clauses.append(f"weak subject check {_loss(subject)}")
    return clauses


def _loss(multiplier: float) -> str:
    return f"(-{round(100.0 * (1.0 - multiplier))}%)"


def _ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


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
