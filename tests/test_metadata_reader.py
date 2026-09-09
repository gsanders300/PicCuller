import subprocess
import tempfile
import unittest
from datetime import UTC, timedelta
from pathlib import Path
from unittest.mock import patch

from metadata_reader import (
    _exiftool_timeout,
    filesystem_metadata,
    metadata_from_exiftool_values,
    parse_capture_datetime,
    read_metadata_with_exiftool,
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



class ExifToolInvocationTests(unittest.TestCase):
    """The bulk read is the only subprocess and the only unguarded blocking call."""

    def _run(self, capture, **run_kwargs):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            photo = root / "Ünïcode dir"
            photo.mkdir()
            target = photo / "IMG_0001.JPG"
            target.touch()
            with (
                patch("metadata_reader.exiftool_available", return_value=True),
                patch("metadata_reader.subprocess.run", **run_kwargs) as runner,
            ):
                capture.append(runner)
                return read_metadata_with_exiftool([target]), target

    def test_output_is_decoded_as_utf8_not_the_console_locale(self) -> None:
        captured: list = []
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[]", stderr=""
        )
        self._run(captured, return_value=completed)
        kwargs = captured[0].call_args.kwargs

        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    def test_a_timeout_is_always_supplied_and_scales_with_the_collection(self) -> None:
        captured: list = []
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[]", stderr=""
        )
        self._run(captured, return_value=completed)

        self.assertGreater(captured[0].call_args.kwargs["timeout"], 0)
        self.assertLess(_exiftool_timeout(1), _exiftool_timeout(100_000))

    def test_a_stalled_exiftool_raises_a_clear_error(self) -> None:
        captured: list = []
        with self.assertRaises(RuntimeError) as raised:
            self._run(
                captured,
                side_effect=subprocess.TimeoutExpired(cmd="exiftool", timeout=60.0),
            )

        self.assertIn("did not finish", str(raised.exception))
        self.assertIn("60", str(raised.exception))

    def test_non_ascii_paths_round_trip_through_a_real_exiftool(self) -> None:
        import shutil

        if shutil.which("exiftool") is None:
            self.skipTest("ExifTool is not installed")
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            directory = root / "Ünïcode dir"
            directory.mkdir()
            target = directory / "Ölympus_0001.jpg"
            Image.new("RGB", (8, 8), "red").save(target)

            metadata = read_metadata_with_exiftool([target])

            # The SourceFile key must match the path we asked about, or the
            # pipeline silently falls back to embedded metadata for this file.
            self.assertIn(target.resolve(), metadata)


if __name__ == "__main__":
    unittest.main()
