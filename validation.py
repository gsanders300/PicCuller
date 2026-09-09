"""Evaluate ranking quality against human keep/reject feedback."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any

from portfolio import load_feedback
from run_audit import atomic_write_text


def evaluate_feedback(
    evaluation_file: Path,
    feedback_file: Path,
) -> dict[str, Any]:
    with evaluation_file.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("Evaluation CSV contains no records")

    input_root = _common_input_root(rows)
    feedback = load_feedback(feedback_file, input_root)
    labeled = [row for row in rows if row.get("file_path") in feedback]
    keeps = [row for row in labeled if feedback[row["file_path"]] == "keep"]
    rejects = [row for row in labeled if feedback[row["file_path"]] == "reject"]
    top_k = len(keeps)
    ranked = sorted(
        rows,
        key=lambda row: (
            -float(row["composite_score"]),
            row["file_path"].casefold(),
        ),
    )
    top_paths = {row["file_path"] for row in ranked[:top_k]}
    precision_at_keep_count = (
        sum(row["file_path"] in top_paths for row in keeps) / top_k if top_k else None
    )
    pairwise_total = len(keeps) * len(rejects)
    pairwise_correct = sum(
        float(keep["composite_score"]) > float(reject["composite_score"])
        for keep in keeps
        for reject in rejects
    )
    return {
        "evaluation_file": str(evaluation_file.resolve()),
        "feedback_file": str(feedback_file.resolve()),
        "total_images": len(rows),
        "labeled_images": len(labeled),
        "keeps": len(keeps),
        "rejects": len(rejects),
        "precision_at_keep_count": precision_at_keep_count,
        "pairwise_accuracy": (pairwise_correct / pairwise_total if pairwise_total else None),
        "mean_keep_global_rank": _mean_rank(keeps),
        "mean_reject_global_rank": _mean_rank(rejects),
    }


def _common_input_root(rows: list[dict[str, str]]) -> Path:
    """Find the directory every evaluated file sits beneath.

    Walking upward with `Path.parent` cannot terminate when two rows sit on
    different roots, because a root is its own parent: on Windows,
    `Path("C:/").parent == Path("C:/")`.
    """
    paths = [Path(row["file_path"]).resolve() for row in rows]
    try:
        common = Path(os.path.commonpath(paths))
    except ValueError as error:
        raise ValueError(
            "Evaluation rows do not share a common directory, so they cannot describe "
            "one collection. Check that the CSV came from a single run."
        ) from error
    return common if common.is_dir() else common.parent


def _mean_rank(rows: list[dict[str, str]]) -> float | None:
    if not rows:
        return None
    return sum(float(row["global_quality_rank"]) for row in rows) / len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="photo-cull-validate",
        description="Compare a Photo Cull evaluation with human keep/reject feedback.",
    )
    parser.add_argument("evaluation", type=Path)
    parser.add_argument("feedback", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = evaluate_feedback(args.evaluation, args.feedback)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    output = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        atomic_write_text(args.output, output)
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
