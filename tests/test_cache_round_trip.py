"""End-to-end coverage of the cache hit path.

`record_to_cache` -> `EvaluationCache.put` -> `get` -> `record_from_cache` is what
makes a run resumable, and it is the one path a wrong value passes through
invisibly. These tests need no network and no model download.
"""

import tempfile
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import imagehash
import numpy as np

import cull
from evaluation_cache import EvaluationCache, FileFingerprint


def source_record(path: Path, timestamp: datetime) -> dict:
    embedding = np.asarray(
        [0.125, -0.25, 0.5, -0.75, 1.0, 0.0, -0.5, 0.375],
        dtype=np.float32,
    )
    return {
        "file_name": path.name,
        "file_path": str(path.resolve()),
        "timestamp": timestamp,
        "timestamp_source": "exiftool:DateTimeOriginal",
        "timezone_source": "embedded_offset",
        "camera_model": "Camera X",
        "camera_serial": "SN-0001",
        "sequence_number": "42",
        "autofocus_info": "Center",
        "phash": imagehash.hex_to_hash("f0e1d2c3b4a59687"),
        "focus_score": 1234.5678,
        "musiq_score": 72.512345,
        "blown_pct": 1.25,
        "crushed_pct": 3.5,
        "exposure_penalty": 0.9375,
        "aesthetic_score": 6.828125,
        "embedding": embedding,
        "subject_integrity": 0.875,
        "face_count": 2,
        "eye_count": 4,
        "eye_factor": 1.0,
        "eye_warning": "",
        "cache_hit": False,
    }


class CacheRoundTripTests(unittest.TestCase):
    def test_record_survives_a_full_write_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            photo = root / "photo.jpg"
            photo.write_bytes(b"photo bytes")
            timestamp = datetime(2026, 7, 4, 9, 30, 15, 250000, tzinfo=timezone(timedelta(hours=-4)))
            original = source_record(photo, timestamp)
            fingerprint = FileFingerprint.from_path(photo)

            with EvaluationCache(root / "cache.sqlite3") as cache:
                cache.put(photo, fingerprint, "signature-1", cull.record_to_cache(original))
                cache.put_preset(
                    photo, "signature-1", "wildlife", cull.record_to_preset_cache(original)
                )
                cached = cache.get(photo, fingerprint, "signature-1")
                preset_cached = cache.get_preset(photo, "signature-1", "wildlife")

            self.assertIsNotNone(cached)
            self.assertIsNotNone(preset_cached)
            restored = cull.record_from_cache(photo, cached, preset_cached)

            # The binary embedding must come back bit-identical.
            np.testing.assert_array_equal(restored["embedding"], original["embedding"])
            self.assertEqual(restored["embedding"].dtype, np.dtype("<f4"))
            self.assertEqual(restored["embedding"].size, original["embedding"].size)

            # The perceptual hash must remain comparable after the hex round trip.
            self.assertEqual(str(restored["phash"]), str(original["phash"]))
            self.assertEqual(restored["phash"] - original["phash"], 0)

            # Timestamps must stay aware and keep their original offset.
            self.assertIsNotNone(restored["timestamp"].tzinfo)
            self.assertEqual(restored["timestamp"], original["timestamp"])
            self.assertEqual(restored["timestamp"].utcoffset(), timedelta(hours=-4))
            self.assertEqual(
                restored["timestamp"].astimezone(UTC),
                original["timestamp"].astimezone(UTC),
            )

            self.assertTrue(restored["cache_hit"])
            for field in (
                "timestamp_source",
                "timezone_source",
                "camera_model",
                "camera_serial",
                "sequence_number",
                "autofocus_info",
                "focus_score",
                "musiq_score",
                "blown_pct",
                "crushed_pct",
                "exposure_penalty",
                "aesthetic_score",
                "subject_integrity",
                "face_count",
                "eye_count",
                "eye_factor",
                "eye_warning",
            ):
                with self.subTest(field=field):
                    self.assertEqual(restored[field], original[field])

    def test_full_precision_metrics_are_not_rounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            photo = root / "photo.jpg"
            photo.write_bytes(b"photo")
            original = source_record(photo, datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
            original["focus_score"] = 1234.56789012345
            original["aesthetic_score"] = 6.123456789012345
            fingerprint = FileFingerprint.from_path(photo)

            with EvaluationCache(root / "cache.sqlite3") as cache:
                cache.put(photo, fingerprint, "signature-1", cull.record_to_cache(original))
                restored = cull.record_from_cache(
                    photo, cache.get(photo, fingerprint, "signature-1")
                )

            self.assertEqual(restored["focus_score"], original["focus_score"])
            self.assertEqual(restored["aesthetic_score"], original["aesthetic_score"])

    def test_a_truncated_embedding_blob_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            photo = root / "photo.jpg"
            photo.write_bytes(b"photo")
            original = source_record(photo, datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
            fingerprint = FileFingerprint.from_path(photo)

            with EvaluationCache(root / "cache.sqlite3") as cache:
                cache.put(photo, fingerprint, "signature-1", cull.record_to_cache(original))
                cache.connection.execute(
                    "UPDATE evaluations SET embedding_length = embedding_length + 1"
                )
                cache.connection.commit()
                cached = cache.get(photo, fingerprint, "signature-1")

            with self.assertRaises(ValueError):
                cull.record_from_cache(photo, cached)

    def test_restored_record_can_be_written_back_unchanged(self) -> None:
        """A cached record must be re-cacheable, so a resumed run stays stable."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            photo = root / "photo.jpg"
            photo.write_bytes(b"photo")
            original = source_record(photo, datetime(2026, 3, 9, 11, 22, 33, tzinfo=UTC))
            fingerprint = FileFingerprint.from_path(photo)

            with EvaluationCache(root / "cache.sqlite3") as cache:
                first = cull.record_to_cache(original)
                first_preset = cull.record_to_preset_cache(original)
                cache.put(photo, fingerprint, "signature-1", first)
                cache.put_preset(photo, "signature-1", "portrait", first_preset)
                restored = cull.record_from_cache(
                    photo,
                    cache.get(photo, fingerprint, "signature-1"),
                    cache.get_preset(photo, "signature-1", "portrait"),
                )
                second = cull.record_to_cache(restored)
                second_preset = cull.record_to_preset_cache(restored)

            self.assertEqual(first, second)
            self.assertEqual(first_preset, second_preset)


if __name__ == "__main__":
    unittest.main()
