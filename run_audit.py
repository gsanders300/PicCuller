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
from typing import Any

RUN_MANIFEST_SCHEMA_VERSION = 1


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
        self.failures: list[dict[str, str]] = []
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
                        "pandas",
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
        }
        self.run_dir.mkdir(parents=True, exist_ok=False)
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

    def record_failure(self, file_path: Path | None, stage: str, error: BaseException) -> None:
        self.failures.append(
            {
                "file_path": "" if file_path is None else str(file_path),
                "stage": stage,
                "error_type": type(error).__name__,
                "message": str(error),
            }
        )
        self.data["counts"]["failed"] = len(self.failures)
        self._write_failures()
        self._write_manifest()

    def finish(self, status: str = "completed") -> None:
        self._finish_phase_timing()
        self.data["status"] = status
        self.data["phase"] = status
        self.data["completed_at"] = utc_now()
        self._write_failures()
        self._write_manifest()

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

    def _write_failures(self) -> None:
        fieldnames = ("file_path", "stage", "error_type", "message")
        atomic_write_csv(self.failures_path, fieldnames, self.failures)


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
