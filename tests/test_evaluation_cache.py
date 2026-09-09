from pathlib import Path
import tempfile
import unittest

from evaluation_cache import CachedEvaluation, EvaluationCache, FileFingerprint


def sample_evaluation() -> CachedEvaluation:
    return CachedEvaluation(
        timestamp_iso="2026-09-09T12:34:56.123456",
        phash_hex="0123456789abcdef",
        focus_score=123.4,
        musiq_score=72.5,
        blown_pct=1.2,
        crushed_pct=3.4,
        exposure_penalty=0.95,
        aesthetic_score=6.8,
        embedding_bytes=b"embedding",
        embedding_length=9,
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


if __name__ == "__main__":
    unittest.main()
