import tempfile
import unittest
from datetime import UTC, timedelta
from pathlib import Path

from metadata_reader import (
    filesystem_metadata,
    metadata_from_exiftool_values,
    parse_capture_datetime,
    resolve_assumed_timezone,
)


class MetadataReaderTests(unittest.TestCase):
    def test_parses_subseconds_and_embedded_offset(self) -> None:
        capture_time, source = parse_capture_datetime(
            "2026:09:09 12:34:56",
            subsecond="1234",
            offset="-04:00",
        )

        self.assertEqual(capture_time.microsecond, 123400)
        self.assertEqual(capture_time.utcoffset(), -timedelta(hours=4))
        self.assertEqual(source, "embedded_offset")

    def test_parses_inline_fraction_and_compact_offset(self) -> None:
        capture_time, source = parse_capture_datetime("2026:09:09 12:34:56.9876543+0530")

        self.assertEqual(capture_time.microsecond, 987654)
        self.assertEqual(capture_time.utcoffset(), timedelta(hours=5, minutes=30))
        self.assertEqual(source, "embedded_offset")

    def test_missing_offset_uses_supplied_timezone(self) -> None:
        capture_time, source = parse_capture_datetime(
            "2026:09:09 12:34:56",
            assumed_timezone=UTC,
        )

        self.assertEqual(capture_time.tzinfo, UTC)
        self.assertEqual(source, "system_local_assumption")

    def test_exiftool_values_include_camera_and_af_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "photo.nef"
            source.touch()
            metadata = metadata_from_exiftool_values(
                source,
                {
                    "DateTimeOriginal": "2026:09:09 12:34:56.5",
                    "OffsetTimeOriginal": "-04:00",
                    "Model": "Camera X",
                    "SerialNumber": "123",
                    "SequenceNumber": 42,
                    "AFPointsUsed": "Center",
                },
            )

        self.assertEqual(metadata.timestamp_source, "exiftool:DateTimeOriginal")
        self.assertEqual(metadata.camera_model, "Camera X")
        self.assertEqual(metadata.camera_serial, "123")
        self.assertEqual(metadata.sequence_number, "42")
        self.assertEqual(metadata.autofocus_info, "Center")

    def test_filesystem_fallback_is_timezone_aware(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "photo.jpg"
            source.touch()
            metadata = filesystem_metadata(source)

        self.assertIsNotNone(metadata.capture_time.tzinfo)
        self.assertEqual(metadata.timestamp_source, "filesystem_mtime")

    def test_resolves_named_timezone(self) -> None:
        zone = resolve_assumed_timezone("America/New_York")
        capture_time, _ = parse_capture_datetime(
            "2026:01:09 12:34:56",
            assumed_timezone=zone,
        )

        self.assertEqual(capture_time.utcoffset(), -timedelta(hours=5))


if __name__ == "__main__":
    unittest.main()
