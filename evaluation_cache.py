"""Persistent, non-executable metric cache for resumable evaluation runs."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Self

CACHE_SCHEMA_VERSION = 3


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    """Cheap identity used to invalidate a cached file after it changes."""

    size_bytes: int
    modified_ns: int

    @classmethod
    def from_path(cls, path: Path) -> FileFingerprint:
        stat = path.stat()
        return cls(size_bytes=stat.st_size, modified_ns=stat.st_mtime_ns)


@dataclass(frozen=True, slots=True)
class CachedEvaluation:
    """Serialized fields needed to rank and regroup an evaluated image."""

    timestamp_iso: str
    timestamp_source: str
    timezone_source: str
    camera_model: str
    camera_serial: str
    sequence_number: str
    autofocus_info: str
    phash_hex: str
    focus_score: float
    musiq_score: float
    blown_pct: float
    crushed_pct: float
    exposure_penalty: float
    aesthetic_score: float
    subject_integrity: float
    face_count: int
    eye_count: int
    eye_factor: float
    eye_warning: str
    embedding_bytes: bytes
    embedding_length: int


class EvaluationCache:
    """SQLite-backed cache committed after each successfully evaluated image."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> Self:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        current_version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if current_version not in (0, 1, 2, CACHE_SCHEMA_VERSION):
            self.connection.close()
            self.connection = None
            raise RuntimeError(
                f"Unsupported cache schema {current_version}; expected {CACHE_SCHEMA_VERSION}"
            )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS evaluations (
                file_path TEXT NOT NULL,
                pipeline_signature TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                timestamp_iso TEXT NOT NULL,
                timestamp_source TEXT NOT NULL DEFAULT '',
                timezone_source TEXT NOT NULL DEFAULT '',
                camera_model TEXT NOT NULL DEFAULT '',
                camera_serial TEXT NOT NULL DEFAULT '',
                sequence_number TEXT NOT NULL DEFAULT '',
                autofocus_info TEXT NOT NULL DEFAULT '',
                phash_hex TEXT NOT NULL,
                focus_score REAL NOT NULL,
                musiq_score REAL NOT NULL,
                blown_pct REAL NOT NULL,
                crushed_pct REAL NOT NULL,
                exposure_penalty REAL NOT NULL,
                aesthetic_score REAL NOT NULL,
                subject_integrity REAL NOT NULL DEFAULT 1.0,
                face_count INTEGER NOT NULL DEFAULT 0,
                eye_count INTEGER NOT NULL DEFAULT 0,
                eye_factor REAL NOT NULL DEFAULT 1.0,
                eye_warning TEXT NOT NULL DEFAULT '',
                embedding_bytes BLOB NOT NULL,
                embedding_length INTEGER NOT NULL,
                PRIMARY KEY (file_path, pipeline_signature)
            )
            """
        )
        existing_columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(evaluations)").fetchall()
        }
        migrations = {
            "timestamp_source": "TEXT NOT NULL DEFAULT ''",
            "timezone_source": "TEXT NOT NULL DEFAULT ''",
            "camera_model": "TEXT NOT NULL DEFAULT ''",
            "camera_serial": "TEXT NOT NULL DEFAULT ''",
            "sequence_number": "TEXT NOT NULL DEFAULT ''",
            "autofocus_info": "TEXT NOT NULL DEFAULT ''",
            "subject_integrity": "REAL NOT NULL DEFAULT 1.0",
            "face_count": "INTEGER NOT NULL DEFAULT 0",
            "eye_count": "INTEGER NOT NULL DEFAULT 0",
            "eye_factor": "REAL NOT NULL DEFAULT 1.0",
            "eye_warning": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in migrations.items():
            if column not in existing_columns:
                self.connection.execute(f"ALTER TABLE evaluations ADD COLUMN {column} {definition}")
        self.connection.execute(f"PRAGMA user_version={CACHE_SCHEMA_VERSION}")
        self.connection.commit()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def get(
        self,
        file_path: Path,
        fingerprint: FileFingerprint,
        pipeline_signature: str,
    ) -> CachedEvaluation | None:
        connection = self._connection()
        row = connection.execute(
            """
            SELECT
                timestamp_iso,
                timestamp_source,
                timezone_source,
                camera_model,
                camera_serial,
                sequence_number,
                autofocus_info,
                phash_hex,
                focus_score,
                musiq_score,
                blown_pct,
                crushed_pct,
                exposure_penalty,
                aesthetic_score,
                subject_integrity,
                face_count,
                eye_count,
                eye_factor,
                eye_warning,
                embedding_bytes,
                embedding_length
            FROM evaluations
            WHERE file_path = ?
              AND pipeline_signature = ?
              AND size_bytes = ?
              AND modified_ns = ?
            """,
            (
                str(file_path.resolve()),
                pipeline_signature,
                fingerprint.size_bytes,
                fingerprint.modified_ns,
            ),
        ).fetchone()
        return CachedEvaluation(*row) if row is not None else None

    def put(
        self,
        file_path: Path,
        fingerprint: FileFingerprint,
        pipeline_signature: str,
        evaluation: CachedEvaluation,
    ) -> None:
        connection = self._connection()
        with connection:
            connection.execute(
                """
                INSERT INTO evaluations (
                    file_path,
                    pipeline_signature,
                    size_bytes,
                    modified_ns,
                    timestamp_iso,
                    timestamp_source,
                    timezone_source,
                    camera_model,
                    camera_serial,
                    sequence_number,
                    autofocus_info,
                    phash_hex,
                    focus_score,
                    musiq_score,
                    blown_pct,
                    crushed_pct,
                    exposure_penalty,
                    aesthetic_score,
                    subject_integrity,
                    face_count,
                    eye_count,
                    eye_factor,
                    eye_warning,
                    embedding_bytes,
                    embedding_length
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path, pipeline_signature) DO UPDATE SET
                    size_bytes = excluded.size_bytes,
                    modified_ns = excluded.modified_ns,
                    timestamp_iso = excluded.timestamp_iso,
                    timestamp_source = excluded.timestamp_source,
                    timezone_source = excluded.timezone_source,
                    camera_model = excluded.camera_model,
                    camera_serial = excluded.camera_serial,
                    sequence_number = excluded.sequence_number,
                    autofocus_info = excluded.autofocus_info,
                    phash_hex = excluded.phash_hex,
                    focus_score = excluded.focus_score,
                    musiq_score = excluded.musiq_score,
                    blown_pct = excluded.blown_pct,
                    crushed_pct = excluded.crushed_pct,
                    exposure_penalty = excluded.exposure_penalty,
                    aesthetic_score = excluded.aesthetic_score,
                    subject_integrity = excluded.subject_integrity,
                    face_count = excluded.face_count,
                    eye_count = excluded.eye_count,
                    eye_factor = excluded.eye_factor,
                    eye_warning = excluded.eye_warning,
                    embedding_bytes = excluded.embedding_bytes,
                    embedding_length = excluded.embedding_length
                """,
                (
                    str(file_path.resolve()),
                    pipeline_signature,
                    fingerprint.size_bytes,
                    fingerprint.modified_ns,
                    evaluation.timestamp_iso,
                    evaluation.timestamp_source,
                    evaluation.timezone_source,
                    evaluation.camera_model,
                    evaluation.camera_serial,
                    evaluation.sequence_number,
                    evaluation.autofocus_info,
                    evaluation.phash_hex,
                    evaluation.focus_score,
                    evaluation.musiq_score,
                    evaluation.blown_pct,
                    evaluation.crushed_pct,
                    evaluation.exposure_penalty,
                    evaluation.aesthetic_score,
                    evaluation.subject_integrity,
                    evaluation.face_count,
                    evaluation.eye_count,
                    evaluation.eye_factor,
                    evaluation.eye_warning,
                    evaluation.embedding_bytes,
                    evaluation.embedding_length,
                ),
            )

    def _connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("Evaluation cache is not open")
        return self.connection
