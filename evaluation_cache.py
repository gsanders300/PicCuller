"""Persistent, non-executable metric cache for resumable evaluation runs."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Self

CACHE_SCHEMA_VERSION = 5
SUPPORTED_SCHEMA_VERSIONS = (0, 1, 2, 3, 4, CACHE_SCHEMA_VERSION)

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
# Text columns absent from schema 1 and 2, added before the rebuild.
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


def relative_key(file_path: Path, source_root: Path) -> str:
    """Return the collection-relative cache key for a source file.

    Keys are relative and POSIX-separated so the cache survives the collection
    moving: a different mount point, drive letter, or volume name no longer
    discards every stored evaluation, and a cache written on one platform reads
    on the other.
    """
    try:
        return file_path.resolve().relative_to(source_root).as_posix()
    except ValueError as error:
        raise ValueError(
            f"{file_path} is outside the collection root {source_root}"
        ) from error


class EvaluationCache:
    """SQLite-backed cache committed after each successfully evaluated image."""

    def __init__(self, database_path: Path, source_root: Path):
        self.database_path = database_path
        self.source_root = source_root.resolve()
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
            self._rebuild_legacy(connection)
            self._create_tables(connection)
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
                relative_path TEXT NOT NULL,
                pipeline_signature TEXT NOT NULL,
                absolute_path TEXT NOT NULL DEFAULT '',
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
                PRIMARY KEY (relative_path, pipeline_signature)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS preset_evaluations (
                relative_path TEXT NOT NULL,
                pipeline_signature TEXT NOT NULL,
                preset TEXT NOT NULL,
                subject_integrity REAL NOT NULL DEFAULT 1.0,
                face_count INTEGER NOT NULL DEFAULT 0,
                eye_count INTEGER NOT NULL DEFAULT 0,
                eye_factor REAL NOT NULL DEFAULT 1.0,
                eye_warning TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (relative_path, pipeline_signature, preset)
            )
            """
        )

    def _rebuild_legacy(self, connection: sqlite3.Connection) -> None:
        """Rebuild any schema 0 to 4 database into the current layout.

        One rebuild covers every older shape rather than a chain of steps:
        schema 1 and 2 are missing base columns, schema 2 and 3 fold the preset
        into the signature, and schema 4 keys rows by absolute path. Rows whose
        stored path falls outside this collection are dropped, because they
        cannot be expressed as a collection-relative key.

        Idempotent: a current database has no `file_path` column, so this returns
        immediately.
        """
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "evaluations" not in tables:
            return
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(evaluations)").fetchall()
        }
        if "file_path" not in columns:
            return

        for name in LEGACY_TEXT_ADDITIONS:
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE evaluations ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                )
                columns.add(name)

        legacy_preset = [name for name in PRESET_FIELDS if name in columns]
        base_rows = connection.execute(
            "SELECT file_path, pipeline_signature, size_bytes, modified_ns, "
            f"{', '.join(BASE_FIELDS)}"
            f"{''.join(f', {name}' for name in legacy_preset)} FROM evaluations"
        ).fetchall()
        preset_rows = (
            connection.execute(
                "SELECT file_path, pipeline_signature, preset, "
                f"{', '.join(PRESET_FIELDS)} FROM preset_evaluations"
            ).fetchall()
            if "preset_evaluations" in tables
            else []
        )

        connection.execute("DROP TABLE evaluations")
        if "preset_evaluations" in tables:
            connection.execute("DROP TABLE preset_evaluations")
        self._create_tables(connection)

        for row in base_rows:
            absolute, signature, size_bytes, modified_ns = row[:4]
            key = self._relative_or_none(absolute)
            if key is None:
                continue
            base_signature, preset = strip_preset(signature)
            self._insert_base(
                connection,
                key,
                base_signature,
                absolute,
                size_bytes,
                modified_ns,
                row[4 : 4 + len(BASE_FIELDS)],
            )
            if preset and legacy_preset:
                self._insert_preset(
                    connection,
                    key,
                    base_signature,
                    preset,
                    row[4 + len(BASE_FIELDS) :],
                )

        for absolute, signature, preset, *values in preset_rows:
            key = self._relative_or_none(absolute)
            if key is not None:
                self._insert_preset(connection, key, signature, preset, values)

    def _relative_or_none(self, absolute: str) -> str | None:
        try:
            return Path(absolute).relative_to(self.source_root).as_posix()
        except ValueError:
            return None

    @staticmethod
    def _insert_base(
        connection: sqlite3.Connection,
        key: str,
        signature: str,
        absolute: str,
        size_bytes: int,
        modified_ns: int,
        values: tuple,
    ) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO evaluations (relative_path, pipeline_signature, "
            f"absolute_path, size_bytes, modified_ns, {', '.join(BASE_FIELDS)}) "
            f"VALUES ({', '.join('?' * (5 + len(BASE_FIELDS)))})",
            (key, signature, absolute, size_bytes, modified_ns, *values),
        )

    @staticmethod
    def _insert_preset(
        connection: sqlite3.Connection,
        key: str,
        signature: str,
        preset: str,
        values: tuple,
    ) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO preset_evaluations (relative_path, pipeline_signature, "
            f"preset, {', '.join(PRESET_FIELDS)}) "
            f"VALUES ({', '.join('?' * (3 + len(PRESET_FIELDS)))})",
            (key, signature, preset, *values),
        )

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
            WHERE relative_path = ?
              AND pipeline_signature = ?
              AND size_bytes = ?
              AND modified_ns = ?
            """,
            (
                relative_key(file_path, self.source_root),
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
            WHERE relative_path = ? AND pipeline_signature = ? AND preset = ?
            """,
            (relative_key(file_path, self.source_root), pipeline_signature, preset),
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
                    relative_path, pipeline_signature, absolute_path, size_bytes, modified_ns,
                    {", ".join(BASE_FIELDS)}
                ) VALUES ({", ".join("?" * (5 + len(BASE_FIELDS)))})
                ON CONFLICT(relative_path, pipeline_signature) DO UPDATE SET
                    absolute_path = excluded.absolute_path,
                    size_bytes = excluded.size_bytes,
                    modified_ns = excluded.modified_ns,
                    {assignments}
                """,
                (
                    relative_key(file_path, self.source_root),
                    pipeline_signature,
                    str(file_path.resolve()),
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
                    relative_path, pipeline_signature, preset, {", ".join(PRESET_FIELDS)}
                ) VALUES ({", ".join("?" * (3 + len(PRESET_FIELDS)))})
                ON CONFLICT(relative_path, pipeline_signature, preset) DO UPDATE SET
                    {assignments}
                """,
                (
                    relative_key(file_path, self.source_root),
                    pipeline_signature,
                    preset,
                    *(getattr(evaluation, name) for name in PRESET_FIELDS),
                ),
            )

    def _connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("Evaluation cache is not open")
        return self.connection
