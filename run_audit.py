"""Atomic run manifests and failure reporting."""

from __future__ import annotations

import csv
import json
import os
import platform
import tempfile
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, TextIO

RUN_MANIFEST_SCHEMA_VERSION = 1
FAILURE_FIELDS = ("file_path", "stage", "error_type", "message")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def installed_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "not-installed"


class RunAudit:
    """Maintain a durable description of run state and recoverable failures."""

    def __init__(
        self,
        run_dir: Path,
        *,
        input_root: Path,
        output_root: Path,
        configuration: dict[str, Any],
        models: dict[str, str],
        discovered_count: int,
    ) -> None:
        self.run_dir = run_dir
        self.manifest_path = run_dir / "run.json"
        self.failures_path = run_dir / "failures.csv"
        self.failure_count = 0
        self._failures_handle: TextIO | None = None
        self._failures_writer: csv.DictWriter | None = None
        self._phase_started = time.perf_counter()
        self.data: dict[str, Any] = {
            "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
            "status": "starting",
            "phase": "initializing",
            "started_at": utc_now(),
            "updated_at": utc_now(),
            "completed_at": None,
            "input_root": str(input_root),
            "output_root": str(output_root),
            "run_directory": str(run_dir),
            "configuration": configuration,
            "models": models,
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "packages": {
                    package: installed_version(package)
                    for package in (
                        "imagehash",
                        "numpy",
                        "opencv-python",
                        "Pillow",
                        "pyiqa",
                        "rawpy",
                        "torch",
                        "torchvision",
                        "transformers",
                    )
                },
            },
            "counts": {
                "discovered": discovered_count,
                "cached": 0,
                "evaluated": 0,
                "failed": 0,
                "winners": 0,
                "selected": 0,
                "exported_files": 0,
            },
            "outputs": {},
            "timings_seconds": {},
            # Aggregate worker time per pipeline stage, with the number of images
            # each stage handled. Phase timings are wall clock and overlap; these
            # are what a throughput comparison actually needs.
            "stage_seconds": {},
            "stage_counts": {},
        }
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self._open_failures()
        self._write_manifest()

    def set_phase(self, phase: str) -> None:
        self._finish_phase_timing()
        self.data["phase"] = phase
        self.data["status"] = "running"
        self._write_manifest()

    def increment(self, name: str, amount: int = 1) -> None:
        self.data["counts"][name] = int(self.data["counts"].get(name, 0)) + amount
        self._write_manifest()

    def set_count(self, name: str, value: int) -> None:
        self.data["counts"][name] = value
        self._write_manifest()

    def add_output(self, name: str, path: Path) -> None:
        self.data["outputs"][name] = str(path)
        self._write_manifest()

    def accumulate_stage(self, name: str, seconds: float, count: int = 1) -> None:
        """Add aggregate time and image count for one pipeline stage.

        This deliberately does not rewrite the manifest: it is called once per
        image, and the next phase or count update flushes it.
        """
        seconds_by_stage = self.data["stage_seconds"]
        seconds_by_stage[name] = round(float(seconds_by_stage.get(name, 0.0)) + seconds, 6)
        counts_by_stage = self.data["stage_counts"]
        counts_by_stage[name] = int(counts_by_stage.get(name, 0)) + count

    def record_failure(self, file_path: Path | None, stage: str, error: BaseException) -> None:
        self.failure_count += 1
        self.data["counts"]["failed"] = self.failure_count
        self._append_failure(
            {
                "file_path": "" if file_path is None else str(file_path),
                "stage": stage,
                "error_type": type(error).__name__,
                "message": str(error),
            }
        )
        self._write_manifest()

    def finish(self, status: str = "completed") -> None:
        self._finish_phase_timing()
        self.data["status"] = status
        self.data["phase"] = status
        self.data["completed_at"] = utc_now()
        self._close_failures()
        self._write_manifest()

    def _open_failures(self) -> None:
        """Open failures.csv once and write its header.

        Failures are appended and flushed one row at a time. Rewriting the whole
        file per failure is quadratic, and a run against an unsupported RAW format
        fails every image, so the count is unbounded.
        """
        self.failures_path.parent.mkdir(parents=True, exist_ok=True)
        self._failures_handle = self.failures_path.open("w", newline="", encoding="utf-8")
        self._failures_writer = csv.DictWriter(
            self._failures_handle,
            fieldnames=FAILURE_FIELDS,
            extrasaction="ignore",
        )
        self._failures_writer.writeheader()
        self._failures_handle.flush()

    def _append_failure(self, row: Mapping[str, Any]) -> None:
        if self._failures_handle is None or self._failures_writer is None:
            # After finish(), fall back to a full atomic rewrite is not possible
            # without retaining rows, so append directly instead.
            with self.failures_path.open("a", newline="", encoding="utf-8") as handle:
                csv.DictWriter(
                    handle, fieldnames=FAILURE_FIELDS, extrasaction="ignore"
                ).writerow({key: _safe_csv_value(value) for key, value in row.items()})
            return
        self._failures_writer.writerow(
            {key: _safe_csv_value(value) for key, value in row.items()}
        )
        self._failures_handle.flush()

    def _close_failures(self) -> None:
        if self._failures_handle is not None:
            self._failures_handle.flush()
            self._failures_handle.close()
            self._failures_handle = None
            self._failures_writer = None

    def _finish_phase_timing(self) -> None:
        phase = str(self.data.get("phase", "unknown"))
        elapsed = time.perf_counter() - self._phase_started
        previous = float(self.data["timings_seconds"].get(phase, 0.0))
        self.data["timings_seconds"][phase] = round(previous + elapsed, 6)
        self._phase_started = time.perf_counter()

    def _write_manifest(self) -> None:
        self.data["updated_at"] = utc_now()
        atomic_write_text(
            self.manifest_path,
            json.dumps(self.data, indent=2, sort_keys=True) + "\n",
        )


def atomic_write_text(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        temporary_path.write_text(content, encoding="utf-8")
        temporary_path.replace(destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_write_csv(
    destination: Path,
    fieldnames: Sequence[str],
    rows: Iterable[Mapping[str, Any]],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with temporary_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(
                {key: _safe_csv_value(value) for key, value in row.items()} for row in rows
            )
        temporary_path.replace(destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _safe_csv_value(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value
