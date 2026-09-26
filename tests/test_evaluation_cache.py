import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import imagehash
import numpy as np

from evaluation_cache import (
    EvaluationCache,
    FileFingerprint,
    strip_preset,
)

# A real normalized little-endian float32 vector, so the embedding round trip
# through the stored bytes is actually exercised.
SAMPLE_EMBEDDING = np.asarray([0.5, -0.5, 0.5, -0.5, 0.25, 0.75, -0.25, 0.125], dtype="<f4")

# The sample record's base evaluation as a raw row, for building legacy databases.
SAMPLE_BASE_ROW = (
    "2026-09-09T12:34:56.123456+02:00", "exiftool:DateTimeOriginal", "embedded_offset",
    "Camera X", "123", "42", "Center", "0123456789abcdef",
    123.4, 72.5, 1.2, 3.4, 0.95, 6.8,
    SAMPLE_EMBEDDING.tobytes(), int(SAMPLE_EMBEDDING.size),
)


def sample_record(path: Path) -> dict:
    return {
        "file_name": path.name,
        "file_path": str(path.resolve()),
        "timestamp": datetime.fromisoformat("2026-09-09T12:34:56.123456+02:00"),
        "timestamp_source": "exiftool:DateTimeOriginal",
        "timezone_source": "embedded_offset",
        "camera_model": "Camera X",
        "camera_serial": "123",
        "sequence_number": "42",
        "autofocus_info": "Center",
        "phash": imagehash.hex_to_hash("0123456789abcdef"),
        "focus_score": 123.4,
        "musiq_score": 72.5,
        "blown_pct": 1.2,
        "crushed_pct": 3.4,
        "exposure_penalty": 0.95,
        "aesthetic_score": 6.8,
        "embedding": SAMPLE_EMBEDDING.copy(),
        "subject_integrity": 0.9,
        "face_count": 1,
        "eye_count": 2,
        "eye_factor": 1.0,
        "eye_warning": "",
        "cache_hit": False,
    }


def comparable(record: dict) -> dict:
    """The stored content of a record, in a form that supports equality."""
    values = {
        key: value
        for key, value in record.items()
        if key not in ("file_name", "file_path", "cache_hit")
    }
    values["phash"] = str(values["phash"])
    values["embedding"] = values["embedding"].tolist()
    return values


class EvaluationCacheTests(unittest.TestCase):
    def test_round_trip_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photo.jpg"
            source.write_bytes(b"photo")
            database = root / "cache.sqlite3"
            fingerprint = FileFingerprint.from_path(source)

            with EvaluationCache(database, root) as cache:
                cache.store(source, fingerprint, "pipeline-v1", "balanced", sample_record(source))

            with EvaluationCache(database, root) as cache:
                hit = cache.lookup(source, fingerprint, "pipeline-v1", "balanced")

            self.assertEqual(comparable(hit.record), comparable(sample_record(source)))

    def test_changed_file_invalidates_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photo.jpg"
            source.write_bytes(b"first")
            database = root / "cache.sqlite3"
            original_fingerprint = FileFingerprint.from_path(source)

            with EvaluationCache(database, root) as cache:
                cache.store(
                    source, original_fingerprint, "pipeline-v1", "balanced", sample_record(source)
                )
                source.write_bytes(b"a different size")
                changed_fingerprint = FileFingerprint.from_path(source)
                cached = cache.lookup(source, changed_fingerprint, "pipeline-v1", "balanced")

            self.assertIsNone(cached)

    def test_pipeline_signature_is_part_of_cache_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photo.jpg"
            source.touch()
            fingerprint = FileFingerprint.from_path(source)

            with EvaluationCache(root / "cache.sqlite3", root) as cache:
                cache.store(source, fingerprint, "pipeline-v1", "balanced", sample_record(source))
                cached = cache.lookup(source, fingerprint, "pipeline-v2", "balanced")

            self.assertIsNone(cached)

    def test_version_one_schema_is_migrated_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            database = root / "cache.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                """
                CREATE TABLE evaluations (
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
            connection.execute("PRAGMA user_version=1")
            connection.commit()
            connection.close()

            with EvaluationCache(database, root) as cache:
                columns = {
                    row[1]
                    for row in cache.connection.execute("PRAGMA table_info(evaluations)").fetchall()
                }
                preset_columns = {
                    row[1]
                    for row in cache.connection.execute(
                        "PRAGMA table_info(preset_evaluations)"
                    ).fetchall()
                }

            # Schema 1 lacked the provenance columns entirely.
            self.assertIn("timestamp_source", columns)
            # Preset-specific metrics now live in their own table.
            self.assertIn("subject_integrity", preset_columns)
            self.assertNotIn("subject_integrity", columns)


class PresetSplitTests(unittest.TestCase):
    """Preset-independent metrics must be stored once, not once per preset."""

    def test_one_base_row_serves_every_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            photo = root / "photo.jpg"
            photo.write_bytes(b"photo")
            fingerprint = FileFingerprint.from_path(photo)

            with EvaluationCache(root / "cache.sqlite3", root) as cache:
                cache.store(photo, fingerprint, "sig", "balanced", sample_record(photo))
                for preset in ("wildlife", "landscape"):
                    cache.store_preset(photo, "sig", preset, sample_record(photo))
                base_rows = cache.connection.execute(
                    "SELECT COUNT(*) FROM evaluations"
                ).fetchone()[0]
                preset_rows = cache.connection.execute(
                    "SELECT COUNT(*) FROM preset_evaluations"
                ).fetchone()[0]

            self.assertEqual(base_rows, 1)
            self.assertEqual(preset_rows, 3)

    def test_a_missing_preset_row_does_not_hide_the_base_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            photo = root / "photo.jpg"
            photo.write_bytes(b"photo")
            fingerprint = FileFingerprint.from_path(photo)

            with EvaluationCache(root / "cache.sqlite3", root) as cache:
                cache.store(photo, fingerprint, "sig", "wildlife", sample_record(photo))

                self.assertFalse(cache.lookup(photo, fingerprint, "sig", "wildlife").preset_missing)
                landscape = cache.lookup(photo, fingerprint, "sig", "landscape")
                self.assertIsNotNone(landscape)
                self.assertTrue(landscape.preset_missing)

    def test_preset_rows_are_updated_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            photo = root / "photo.jpg"
            photo.write_bytes(b"photo")
            fingerprint = FileFingerprint.from_path(photo)

            with EvaluationCache(root / "cache.sqlite3", root) as cache:
                cache.store(photo, fingerprint, "sig", "wildlife", sample_record(photo))
                changed = sample_record(photo)
                changed["subject_integrity"] = 0.25
                cache.store_preset(photo, "sig", "wildlife", changed)
                stored = cache.lookup(photo, fingerprint, "sig", "wildlife").record
                count = cache.connection.execute(
                    "SELECT COUNT(*) FROM preset_evaluations"
                ).fetchone()[0]

            self.assertEqual(count, 1)
            self.assertEqual(stored["subject_integrity"], 0.25)


class SignatureStrippingTests(unittest.TestCase):
    def test_the_preset_is_removed_and_returned(self) -> None:
        base, preset = strip_preset("algorithm=5;max_dim=1024;preset=wildlife")

        self.assertEqual(base, "algorithm=5;max_dim=1024")
        self.assertEqual(preset, "wildlife")

    def test_a_signature_without_a_preset_is_unchanged(self) -> None:
        base, preset = strip_preset("algorithm=5;max_dim=1024")

        self.assertEqual(base, "algorithm=5;max_dim=1024")
        self.assertEqual(preset, "")


class SchemaThreeMigrationTests(unittest.TestCase):
    """Schema 3 folded the preset into the signature; the split must preserve it."""

    def _legacy_database(self, path: Path, presets: tuple[str, ...], root: Path) -> None:
        connection = sqlite3.connect(path)
        connection.execute(
            """
            CREATE TABLE evaluations (
                file_path TEXT NOT NULL, pipeline_signature TEXT NOT NULL,
                size_bytes INTEGER NOT NULL, modified_ns INTEGER NOT NULL,
                timestamp_iso TEXT NOT NULL, timestamp_source TEXT NOT NULL DEFAULT '',
                timezone_source TEXT NOT NULL DEFAULT '', camera_model TEXT NOT NULL DEFAULT '',
                camera_serial TEXT NOT NULL DEFAULT '', sequence_number TEXT NOT NULL DEFAULT '',
                autofocus_info TEXT NOT NULL DEFAULT '', phash_hex TEXT NOT NULL,
                focus_score REAL NOT NULL, musiq_score REAL NOT NULL, blown_pct REAL NOT NULL,
                crushed_pct REAL NOT NULL, exposure_penalty REAL NOT NULL,
                aesthetic_score REAL NOT NULL, subject_integrity REAL NOT NULL DEFAULT 1.0,
                face_count INTEGER NOT NULL DEFAULT 0, eye_count INTEGER NOT NULL DEFAULT 0,
                eye_factor REAL NOT NULL DEFAULT 1.0, eye_warning TEXT NOT NULL DEFAULT '',
                embedding_bytes BLOB NOT NULL, embedding_length INTEGER NOT NULL,
                PRIMARY KEY (file_path, pipeline_signature)
            )
            """
        )
        for index, preset in enumerate(presets):
            connection.execute(
                "INSERT INTO evaluations VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(root / "day-one" / "a.jpg"),
                    f"algorithm=4;max_dim=1024;preset={preset}", 10, 20,
                    "2026-01-01T00:00:00+00:00", "exiftool:DateTimeOriginal", "embedded_offset",
                    "Camera", "SN", "1", "Center", "0123456789abcdef",
                    100.0, 70.0, 1.0, 2.0, 0.9, 6.5,
                    0.5 + index * 0.1, index, index * 2, 1.0, "",
                    SAMPLE_EMBEDDING.tobytes(), int(SAMPLE_EMBEDDING.size),
                ),
            )
        connection.execute("PRAGMA user_version=3")
        connection.commit()
        connection.close()

    def test_duplicate_base_rows_collapse_and_preset_rows_survive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            database = root / "cache.sqlite3"
            self._legacy_database(database, ("balanced", "wildlife", "landscape"), root)

            with EvaluationCache(database, root) as cache:
                base_rows = cache.connection.execute(
                    "SELECT COUNT(*) FROM evaluations"
                ).fetchone()[0]
                preset_rows = dict(
                    cache.connection.execute(
                        "SELECT preset, subject_integrity FROM preset_evaluations"
                    ).fetchall()
                )
                signatures = {
                    row[0]
                    for row in cache.connection.execute(
                        "SELECT DISTINCT pipeline_signature FROM evaluations"
                    ).fetchall()
                }

            # Three preset copies of one photo collapse to a single base row.
            self.assertEqual(base_rows, 1)
            self.assertEqual(signatures, {"algorithm=4;max_dim=1024"})
            # Each preset keeps its own subject integrity.
            self.assertEqual(set(preset_rows), {"balanced", "wildlife", "landscape"})
            for preset, expected in (("balanced", 0.5), ("wildlife", 0.6), ("landscape", 0.7)):
                self.assertAlmostEqual(preset_rows[preset], expected)

    def test_migration_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            database = root / "cache.sqlite3"
            self._legacy_database(database, ("balanced", "wildlife"), root)

            with EvaluationCache(database, root) as cache:
                first = cache.connection.execute("SELECT COUNT(*) FROM preset_evaluations").fetchone()
            with EvaluationCache(database, root) as cache:
                second = cache.connection.execute(
                    "SELECT COUNT(*) FROM preset_evaluations"
                ).fetchone()
                leftover = cache.connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'evaluations_legacy'"
                ).fetchall()

            self.assertEqual(first, second)
            self.assertEqual(leftover, [])

    def test_a_future_schema_is_refused_without_touching_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            database = root / "cache.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("PRAGMA user_version=99")
            connection.commit()
            connection.close()
            before = database.read_bytes()

            with self.assertRaises(RuntimeError) as raised, EvaluationCache(database, root):
                pass

            self.assertIn("Unsupported cache schema 99", str(raised.exception))
            self.assertEqual(database.read_bytes(), before)


class PortableKeyTests(unittest.TestCase):
    """The cache must survive the collection moving to a different path."""

    def _populate(self, collection: Path) -> None:
        (collection / "day-one").mkdir(parents=True)
        photo = collection / "day-one" / "IMG_0001.ARW"
        photo.write_bytes(b"raw bytes")
        with EvaluationCache(collection / ".photo-cull" / "cache.sqlite3", collection) as cache:
            cache.store(
                photo, FileFingerprint.from_path(photo), "sig", "wildlife", sample_record(photo)
            )

    def test_moving_the_collection_keeps_every_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            original = root / "Volumes" / "CardA" / "shoot"
            self._populate(original)

            # The same collection, mounted somewhere else entirely.
            moved = root / "Volumes" / "BackupDrive" / "archive" / "shoot"
            moved.parent.mkdir(parents=True)
            original.rename(moved)

            photo = moved / "day-one" / "IMG_0001.ARW"
            with EvaluationCache(moved / ".photo-cull" / "cache.sqlite3", moved) as cache:
                hit = cache.lookup(photo, FileFingerprint.from_path(photo), "sig", "wildlife")

            self.assertIsNotNone(hit, "a moved collection must not lose its evaluations")
            self.assertFalse(hit.preset_missing)
            self.assertEqual(comparable(hit.record), comparable(sample_record(photo)))

    def test_keys_are_posix_so_a_cache_crosses_platforms(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            self._populate(root / "shoot")
            collection = root / "shoot"

            with EvaluationCache(collection / ".photo-cull" / "cache.sqlite3", collection) as cache:
                keys = [
                    row[0]
                    for row in cache.connection.execute(
                        "SELECT relative_path FROM evaluations"
                    ).fetchall()
                ]

            self.assertEqual(keys, ["day-one/IMG_0001.ARW"])
            self.assertNotIn("\\", keys[0])

    def test_the_absolute_path_is_kept_for_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            self._populate(root / "shoot")
            collection = root / "shoot"

            with EvaluationCache(collection / ".photo-cull" / "cache.sqlite3", collection) as cache:
                stored = cache.connection.execute(
                    "SELECT absolute_path FROM evaluations"
                ).fetchone()[0]

            self.assertEqual(stored, str(collection / "day-one" / "IMG_0001.ARW"))

    def test_a_file_outside_the_collection_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            collection = root / "shoot"
            collection.mkdir()
            stray = root / "elsewhere.jpg"
            stray.write_bytes(b"x")

            with (
                EvaluationCache(collection / "cache.sqlite3", collection) as cache,
                self.assertRaises(ValueError) as raised,
            ):
                cache.lookup(stray, FileFingerprint.from_path(stray), "sig", "balanced")

            self.assertIn("outside the collection root", str(raised.exception))

    def test_a_changed_file_still_misses_after_a_move(self) -> None:
        """Portability must not weaken the size and mtime invalidation."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            collection = root / "shoot"
            self._populate(collection)
            photo = collection / "day-one" / "IMG_0001.ARW"
            photo.write_bytes(b"different content entirely")

            with EvaluationCache(collection / ".photo-cull" / "cache.sqlite3", collection) as cache:
                cached = cache.lookup(photo, FileFingerprint.from_path(photo), "sig", "wildlife")

            self.assertIsNone(cached)


class AbsolutePathMigrationTests(unittest.TestCase):
    def test_schema_four_rows_are_rekeyed_to_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            collection = root / "shoot"
            (collection / "day-one").mkdir(parents=True)
            photo = collection / "day-one" / "IMG_0001.ARW"
            photo.write_bytes(b"raw bytes")
            database = collection / "cache.sqlite3"

            connection = sqlite3.connect(database)
            connection.execute(
                """
                CREATE TABLE evaluations (
                    file_path TEXT NOT NULL, pipeline_signature TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL, modified_ns INTEGER NOT NULL,
                    timestamp_iso TEXT NOT NULL, timestamp_source TEXT NOT NULL DEFAULT '',
                    timezone_source TEXT NOT NULL DEFAULT '', camera_model TEXT NOT NULL DEFAULT '',
                    camera_serial TEXT NOT NULL DEFAULT '',
                    sequence_number TEXT NOT NULL DEFAULT '',
                    autofocus_info TEXT NOT NULL DEFAULT '', phash_hex TEXT NOT NULL,
                    focus_score REAL NOT NULL, musiq_score REAL NOT NULL, blown_pct REAL NOT NULL,
                    crushed_pct REAL NOT NULL, exposure_penalty REAL NOT NULL,
                    aesthetic_score REAL NOT NULL, embedding_bytes BLOB NOT NULL,
                    embedding_length INTEGER NOT NULL,
                    PRIMARY KEY (file_path, pipeline_signature)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE preset_evaluations (
                    file_path TEXT NOT NULL, pipeline_signature TEXT NOT NULL,
                    preset TEXT NOT NULL, subject_integrity REAL NOT NULL DEFAULT 1.0,
                    face_count INTEGER NOT NULL DEFAULT 0, eye_count INTEGER NOT NULL DEFAULT 0,
                    eye_factor REAL NOT NULL DEFAULT 1.0, eye_warning TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (file_path, pipeline_signature, preset)
                )
                """
            )
            fingerprint = FileFingerprint.from_path(photo)
            connection.execute(
                "INSERT INTO evaluations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(photo), "sig", fingerprint.size_bytes, fingerprint.modified_ns,
                    *SAMPLE_BASE_ROW,
                ),
            )
            connection.execute(
                "INSERT INTO preset_evaluations VALUES (?,?,?,?,?,?,?,?)",
                (str(photo), "sig", "wildlife", 0.9, 1, 2, 1.0, ""),
            )
            # A row belonging to some other collection sharing this output root.
            connection.execute(
                "INSERT INTO evaluations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "/somewhere/else/other.jpg", "sig", 1, 2,
                    *SAMPLE_BASE_ROW,
                ),
            )
            connection.execute("PRAGMA user_version=4")
            connection.commit()
            connection.close()

            with EvaluationCache(database, collection) as cache:
                hit = cache.lookup(photo, fingerprint, "sig", "wildlife")
                keys = [
                    row[0]
                    for row in cache.connection.execute(
                        "SELECT relative_path FROM evaluations"
                    ).fetchall()
                ]

            self.assertFalse(hit.preset_missing)
            self.assertEqual(comparable(hit.record), comparable(sample_record(photo)))
            # The unrelated row cannot be expressed relatively, so it is dropped.
            self.assertEqual(keys, ["day-one/IMG_0001.ARW"])


if __name__ == "__main__":
    unittest.main()
