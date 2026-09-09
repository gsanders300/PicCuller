"""Persistent, non-executable metric cache for resumable evaluation runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3


CACHE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    """Cheap identity used to invalidate a cached file after it changes."""

    size_bytes: int
    modified_ns: int

    @classmethod
    def from_path(cls, path: Path) -> "FileFingerprint":
        stat = path.stat()
        return cls(size_bytes=stat.st_size, modified_ns=stat.st_mtime_ns)


@dataclass(frozen=True, slots=True)
class CachedEvaluation:
    """Serialized fields needed to rank and regroup an evaluated image."""

    timestamp_iso: str
    phash_hex: str
    focus_score: float
    musiq_score: float
    blown_pct: float
    crushed_pct: float
    exposure_penalty: float
    aesthetic_score: float
    embedding_bytes: bytes
    embedding_length: int


class EvaluationCache:
    """SQLite-backed cache committed after each successfully evaluated image."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> "EvaluationCache":
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        current_version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if current_version not in (0, CACHE_SCHEMA_VERSION):
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
                phash_hex TEXT NOT NULL,
                focus_score REAL NOT NULL,
                musiq_score REAL NOT NULL,
                blown_pct REAL NOT NULL,
                crushed_pct REAL NOT NULL,
                exposure_penalty REAL NOT NULL,
                aesthetic_score REAL NOT NULL,
                embedding_bytes BLOB NOT NULL,
                embedding_length INTEGER NOT NULL,
                PRIMARY KEY (file_path, pipeline_signature)
            )
            """
        )
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
                phash_hex,
                focus_score,
                musiq_score,
                blown_pct,
                crushed_pct,
                exposure_penalty,
                aesthetic_score,
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
                    phash_hex,
                    focus_score,
                    musiq_score,
                    blown_pct,
                    crushed_pct,
                    exposure_penalty,
                    aesthetic_score,
                    embedding_bytes,
                    embedding_length
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path, pipeline_signature) DO UPDATE SET
                    size_bytes = excluded.size_bytes,
                    modified_ns = excluded.modified_ns,
                    timestamp_iso = excluded.timestamp_iso,
                    phash_hex = excluded.phash_hex,
                    focus_score = excluded.focus_score,
                    musiq_score = excluded.musiq_score,
                    blown_pct = excluded.blown_pct,
                    crushed_pct = excluded.crushed_pct,
                    exposure_penalty = excluded.exposure_penalty,
                    aesthetic_score = excluded.aesthetic_score,
                    embedding_bytes = excluded.embedding_bytes,
                    embedding_length = excluded.embedding_length
                """,
                (
                    str(file_path.resolve()),
                    pipeline_signature,
                    fingerprint.size_bytes,
                    fingerprint.modified_ns,
                    evaluation.timestamp_iso,
                    evaluation.phash_hex,
                    evaluation.focus_score,
                    evaluation.musiq_score,
                    evaluation.blown_pct,
                    evaluation.crushed_pct,
                    evaluation.exposure_penalty,
                    evaluation.aesthetic_score,
                    evaluation.embedding_bytes,
                    evaluation.embedding_length,
                ),
            )

    def _connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("Evaluation cache is not open")
        return self.connection
