import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from evaluation_cache import CachedEvaluation, EvaluationCache, FileFingerprint

# A real normalized little-endian float32 vector, so the round trip through
# record_from_cache's np.frombuffer is actually exercised.
SAMPLE_EMBEDDING = np.asarray([0.5, -0.5, 0.5, -0.5, 0.25, 0.75, -0.25, 0.125], dtype="<f4")


def sample_evaluation() -> CachedEvaluation:
    return CachedEvaluation(
        timestamp_iso="2026-09-09T12:34:56.123456+02:00",
        timestamp_source="exiftool:DateTimeOriginal",
        timezone_source="embedded_offset",
        camera_model="Camera X",
        camera_serial="123",
        sequence_number="42",
        autofocus_info="Center",
        phash_hex="0123456789abcdef",
        focus_score=123.4,
        musiq_score=72.5,
        blown_pct=1.2,
        crushed_pct=3.4,
        exposure_penalty=0.95,
        aesthetic_score=6.8,
        subject_integrity=0.9,
        face_count=1,
        eye_count=2,
        eye_factor=1.0,
        eye_warning="",
        embedding_bytes=SAMPLE_EMBEDDING.tobytes(),
        embedding_length=int(SAMPLE_EMBEDDING.size),
    )


class EvaluationCacheTests(unittest.TestCase):
    def test_round_trip_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photo.jpg"
            source.write_bytes(b"photo")
            database = root / "cache.sqlite3"
            fingerprint = FileFingerprint.from_path(source)

            with EvaluationCache(database) as cache:
                cache.put(source, fingerprint, "pipeline-v1", sample_evaluation())

            with EvaluationCache(database) as cache:
                cached = cache.get(source, fingerprint, "pipeline-v1")

            self.assertEqual(cached, sample_evaluation())

    def test_changed_file_invalidates_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photo.jpg"
            source.write_bytes(b"first")
            database = root / "cache.sqlite3"
            original_fingerprint = FileFingerprint.from_path(source)

            with EvaluationCache(database) as cache:
                cache.put(source, original_fingerprint, "pipeline-v1", sample_evaluation())
                source.write_bytes(b"a different size")
                changed_fingerprint = FileFingerprint.from_path(source)
                cached = cache.get(source, changed_fingerprint, "pipeline-v1")

            self.assertIsNone(cached)

    def test_pipeline_signature_is_part_of_cache_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "photo.jpg"
            source.touch()
            fingerprint = FileFingerprint.from_path(source)

            with EvaluationCache(root / "cache.sqlite3") as cache:
                cache.put(source, fingerprint, "pipeline-v1", sample_evaluation())
                cached = cache.get(source, fingerprint, "pipeline-v2")

            self.assertIsNone(cached)

    def test_version_one_schema_is_migrated_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory).resolve() / "cache.sqlite3"
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

            with EvaluationCache(database) as cache:
                columns = {
                    row[1]
                    for row in cache.connection.execute("PRAGMA table_info(evaluations)").fetchall()
                }

            self.assertIn("timestamp_source", columns)
            self.assertIn("subject_integrity", columns)


if __name__ == "__main__":
    unittest.main()
