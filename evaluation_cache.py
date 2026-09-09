"""Persistent, non-executable metric cache for resumable evaluation runs."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Self

CACHE_SCHEMA_VERSION = 4
SUPPORTED_SCHEMA_VERSIONS = (0, 1, 2, 3, CACHE_SCHEMA_VERSION)

BASE_FIELDS = (
    "timestamp_iso",
    "timestamp_source",
    "timezone_source",
    "camera_model",
    "camera_serial",
    "sequence_number",
    "autofocus_info",
    "phash_hex",
    "focus_score",
    "musiq_score",
    "blown_pct",
    "crushed_pct",
    "exposure_penalty",
    "aesthetic_score",
    "embedding_bytes",
    "embedding_length",
)
PRESET_FIELDS = (
    "subject_integrity",
    "face_count",
    "eye_count",
    "eye_factor",
    "eye_warning",
)
# Text columns absent from schema 1 and 2, added before the split-table rebuild.
LEGACY_TEXT_ADDITIONS = (
    "timestamp_source",
    "timezone_source",
    "camera_model",
    "camera_serial",
    "sequence_number",
    "autofocus_info",
)


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
    """Metrics that do not depend on the scoring preset."""

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
    embedding_bytes: bytes
    embedding_length: int


@dataclass(frozen=True, slots=True)
class CachedPresetEvaluation:
    """Metrics whose meaning depends on the scoring preset.

    Subject integrity comes from preset-specific CLIP prompts. The face and eye
    values exist only for the portrait preset, which is the one preset that needs
    the decoded pixels rather than the cached embedding.
    """

    subject_integrity: float = 1.0
    face_count: int = 0
    eye_count: int = 0
    eye_factor: float = 1.0
    eye_warning: str = ""


def strip_preset(signature: str) -> tuple[str, str]:
    """Split a legacy signature into its preset-free form and its preset.

    Schema 3 and earlier folded `preset=` into one signature, so every preset
    stored a duplicate copy of every preset-independent metric.
    """
    parts = signature.split(";")
    kept = [part for part in parts if not part.startswith("preset=")]
    preset = next(
        (part.removeprefix("preset=") for part in parts if part.startswith("preset=")),
        "",
    )
    return ";".join(kept), preset


class EvaluationCache:
    """SQLite-backed cache committed after each successfully evaluated image."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> Self:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        try:
            # Read the version before any pragma that writes: journal_mode is
            # persistent, so setting it first would modify a database this build
            # is about to refuse.
            current_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if current_version not in SUPPORTED_SCHEMA_VERSIONS:
                raise RuntimeError(
                    f"Unsupported cache schema {current_version}; expected {CACHE_SCHEMA_VERSION}"
                )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            self._create_tables(connection)
            if current_version < CACHE_SCHEMA_VERSION:
                self._migrate_to_v4(connection)
            connection.execute(f"PRAGMA user_version={CACHE_SCHEMA_VERSION}")
            connection.commit()
        except BaseException:
            # Never leave a half-opened connection behind on a failed migration.
            connection.close()
            raise
        self.connection = connection
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    @staticmethod
    def _create_tables(connection: sqlite3.Connection) -> None:
        connection.execute(
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
                embedding_bytes BLOB NOT NULL,
                embedding_length INTEGER NOT NULL,
                PRIMARY KEY (file_path, pipeline_signature)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS preset_evaluations (
                file_path TEXT NOT NULL,
                pipeline_signature TEXT NOT NULL,
                preset TEXT NOT NULL,
                subject_integrity REAL NOT NULL DEFAULT 1.0,
                face_count INTEGER NOT NULL DEFAULT 0,
                eye_count INTEGER NOT NULL DEFAULT 0,
                eye_factor REAL NOT NULL DEFAULT 1.0,
                eye_warning TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (file_path, pipeline_signature, preset)
            )
            """
        )

    @staticmethod
    def _migrate_to_v4(connection: sqlite3.Connection) -> None:
        """Bring a schema 0 to 3 database up to the split-table layout.

        Schema 1 and 2 are missing base columns, and schema 2 and 3 fold the
        preset into one signature so every preset stored a duplicate copy of
        every preset-independent metric. Idempotent: a second pass finds a table
        that already has every base column and no preset column, and returns.
        """
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(evaluations)").fetchall()
        }
        for name in LEGACY_TEXT_ADDITIONS:
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE evaluations ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                )
                columns.add(name)

        legacy = [name for name in PRESET_FIELDS if name in columns]
        rows = connection.execute(
            "SELECT file_path, pipeline_signature, size_bytes, modified_ns, "
            f"{', '.join(BASE_FIELDS)}{''.join(f', {name}' for name in legacy)} FROM evaluations"
        ).fetchall()
        if not legacy and not any(strip_preset(row[1])[1] for row in rows):
            return

        connection.execute("ALTER TABLE evaluations RENAME TO evaluations_legacy")
        EvaluationCache._create_tables(connection)

        base_placeholders = ", ".join("?" * (4 + len(BASE_FIELDS)))
        for row in rows:
            file_path, signature, size_bytes, modified_ns = row[:4]
            base_values = row[4 : 4 + len(BASE_FIELDS)]
            legacy_values = row[4 + len(BASE_FIELDS) :]
            base_signature, preset = strip_preset(signature)
            connection.execute(
                "INSERT OR REPLACE INTO evaluations "
                "(file_path, pipeline_signature, size_bytes, modified_ns, "
                f"{', '.join(BASE_FIELDS)}) VALUES ({base_placeholders})",
                (file_path, base_signature, size_bytes, modified_ns, *base_values),
            )
            if preset and legacy:
                connection.execute(
                    "INSERT OR REPLACE INTO preset_evaluations "
                    f"(file_path, pipeline_signature, preset, {', '.join(legacy)}) "
                    f"VALUES ({', '.join('?' * (3 + len(legacy)))})",
                    (file_path, base_signature, preset, *legacy_values),
                )
        connection.execute("DROP TABLE evaluations_legacy")

    def get(
        self,
        file_path: Path,
        fingerprint: FileFingerprint,
        pipeline_signature: str,
    ) -> CachedEvaluation | None:
        connection = self._connection()
        row = connection.execute(
            f"""
            SELECT {", ".join(BASE_FIELDS)}
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

    def get_preset(
        self,
        file_path: Path,
        pipeline_signature: str,
        preset: str,
    ) -> CachedPresetEvaluation | None:
        connection = self._connection()
        row = connection.execute(
            f"""
            SELECT {", ".join(PRESET_FIELDS)}
            FROM preset_evaluations
            WHERE file_path = ? AND pipeline_signature = ? AND preset = ?
            """,
            (str(file_path.resolve()), pipeline_signature, preset),
        ).fetchone()
        return CachedPresetEvaluation(*row) if row is not None else None

    def put(
        self,
        file_path: Path,
        fingerprint: FileFingerprint,
        pipeline_signature: str,
        evaluation: CachedEvaluation,
    ) -> None:
        assignments = ", ".join(f"{name} = excluded.{name}" for name in BASE_FIELDS)
        connection = self._connection()
        with connection:
            connection.execute(
                f"""
                INSERT INTO evaluations (
                    file_path, pipeline_signature, size_bytes, modified_ns,
                    {", ".join(BASE_FIELDS)}
                ) VALUES ({", ".join("?" * (4 + len(BASE_FIELDS)))})
                ON CONFLICT(file_path, pipeline_signature) DO UPDATE SET
                    size_bytes = excluded.size_bytes,
                    modified_ns = excluded.modified_ns,
                    {assignments}
                """,
                (
                    str(file_path.resolve()),
                    pipeline_signature,
                    fingerprint.size_bytes,
                    fingerprint.modified_ns,
                    *(getattr(evaluation, name) for name in BASE_FIELDS),
                ),
            )

    def put_preset(
        self,
        file_path: Path,
        pipeline_signature: str,
        preset: str,
        evaluation: CachedPresetEvaluation,
    ) -> None:
        assignments = ", ".join(f"{name} = excluded.{name}" for name in PRESET_FIELDS)
        connection = self._connection()
        with connection:
            connection.execute(
                f"""
                INSERT INTO preset_evaluations (
                    file_path, pipeline_signature, preset, {", ".join(PRESET_FIELDS)}
                ) VALUES ({", ".join("?" * (3 + len(PRESET_FIELDS)))})
                ON CONFLICT(file_path, pipeline_signature, preset) DO UPDATE SET
                    {assignments}
                """,
                (
                    str(file_path.resolve()),
                    pipeline_signature,
                    preset,
                    *(getattr(evaluation, name) for name in PRESET_FIELDS),
                ),
            )

    def _connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("Evaluation cache is not open")
        return self.connection
