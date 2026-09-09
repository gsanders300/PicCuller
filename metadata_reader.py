"""Capture metadata extraction with optional bulk ExifTool support."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from PIL import Image


DATE_PATTERN = re.compile(
    r"^\s*(?P<year>\d{4})[:-](?P<month>\d{2})[:-](?P<day>\d{2})"
    r"[ T](?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<fraction>\d+))?"
    r"(?P<offset>Z|[+-]\d{2}:?\d{2})?\s*$"
)


@dataclass(frozen=True, slots=True)
class CaptureMetadata:
    capture_time: datetime
    timestamp_source: str
    timezone_source: str
    camera_model: str = ""
    camera_serial: str = ""
    sequence_number: str = ""
    autofocus_info: str = ""


def local_timezone() -> tzinfo:
    return datetime.now().astimezone().tzinfo or UTC


def resolve_assumed_timezone(name: str | None) -> tzinfo | None:
    if name is None:
        return None
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown IANA timezone: {name}") from error


def parse_capture_datetime(
    value: object,
    *,
    subsecond: object = None,
    offset: object = None,
    assumed_timezone: tzinfo | None = None,
) -> tuple[datetime, str]:
    """Parse common EXIF/ExifTool timestamps into an aware datetime."""
    match = DATE_PATTERN.match(str(value))
    if match is None:
        raise ValueError(f"Unsupported capture timestamp: {value!r}")

    fraction = match.group("fraction")
    if not fraction and subsecond is not None:
        fraction = re.sub(r"\D", "", str(subsecond))
    microsecond = int(((fraction or "") + "000000")[:6])

    offset_text = match.group("offset") or (str(offset).strip() if offset else "")
    if offset_text:
        if offset_text == "Z":
            parsed_timezone = UTC
        else:
            sign = -1 if offset_text.startswith("-") else 1
            digits = offset_text[1:].replace(":", "")
            if len(digits) != 4 or not digits.isdigit():
                raise ValueError(f"Unsupported timezone offset: {offset_text!r}")
            minutes = int(digits[:2]) * 60 + int(digits[2:])
            parsed_timezone = timezone(sign * timedelta(minutes=minutes))
        timezone_source = "embedded_offset"
    else:
        parsed_timezone = assumed_timezone or local_timezone()
        timezone_source = "system_local_assumption"

    return (
        datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second")),
            microsecond,
            tzinfo=parsed_timezone,
        ),
        timezone_source,
    )


def filesystem_metadata(file_path: Path) -> CaptureMetadata:
    capture_time = datetime.fromtimestamp(file_path.stat().st_mtime).astimezone()
    return CaptureMetadata(
        capture_time=capture_time,
        timestamp_source="filesystem_mtime",
        timezone_source="filesystem",
    )


def metadata_from_pillow(
    image: Image.Image,
    file_path: Path,
    assumed_timezone: tzinfo | None = None,
) -> CaptureMetadata:
    """Read standard and nested EXIF IFD values from an already-open image."""
    values: dict[str, Any] = {}
    try:
        exif = image.getexif()
        values.update(_named_exif_values(exif))
        from PIL import ExifTags

        exif_ifd_id = getattr(getattr(ExifTags, "IFD", None), "Exif", 34665)
        try:
            values.update(_named_exif_values(exif.get_ifd(exif_ifd_id)))
        except (AttributeError, KeyError, TypeError, ValueError):
            pass
    except (AttributeError, OSError, TypeError, ValueError):
        return filesystem_metadata(file_path)

    date_candidates = (
        ("DateTimeOriginal", "SubsecTimeOriginal", "OffsetTimeOriginal"),
        ("DateTimeDigitized", "SubsecTimeDigitized", "OffsetTimeDigitized"),
        ("DateTime", "SubsecTime", "OffsetTime"),
    )
    for date_key, subsecond_key, offset_key in date_candidates:
        if values.get(date_key):
            try:
                capture_time, timezone_source = parse_capture_datetime(
                    values[date_key],
                    subsecond=values.get(subsecond_key),
                    offset=values.get(offset_key),
                    assumed_timezone=assumed_timezone,
                )
                return CaptureMetadata(
                    capture_time=capture_time,
                    timestamp_source=f"pillow:{date_key}",
                    timezone_source=timezone_source,
                    camera_model=_text(values.get("Model")),
                    camera_serial=_text(
                        values.get("BodySerialNumber") or values.get("CameraSerialNumber")
                    ),
                    sequence_number=_text(values.get("ImageNumber")),
                )
            except ValueError:
                continue

    fallback = filesystem_metadata(file_path)
    return CaptureMetadata(
        capture_time=fallback.capture_time,
        timestamp_source=fallback.timestamp_source,
        timezone_source=fallback.timezone_source,
        camera_model=_text(values.get("Model")),
        camera_serial=_text(values.get("BodySerialNumber") or values.get("CameraSerialNumber")),
        sequence_number=_text(values.get("ImageNumber")),
    )


def exiftool_available() -> bool:
    return shutil.which("exiftool") is not None


def select_metadata_backend(preference: str) -> str:
    if preference == "auto":
        return "exiftool" if exiftool_available() else "pillow"
    if preference == "exiftool" and not exiftool_available():
        raise RuntimeError(
            "ExifTool was requested but is not installed or is not available on PATH"
        )
    if preference not in {"exiftool", "pillow"}:
        raise ValueError(f"Unsupported metadata backend: {preference}")
    return preference


def read_metadata_with_exiftool(
    file_paths: Iterable[Path],
    assumed_timezone: tzinfo | None = None,
) -> dict[Path, CaptureMetadata]:
    """Read metadata for many files in one ExifTool process."""
    paths = [path.resolve() for path in file_paths]
    if not paths:
        return {}
    if not exiftool_available():
        raise RuntimeError("ExifTool is not installed or is not available on PATH")

    command = [
        "exiftool",
        "-json",
        "-charset",
        "filename=UTF8",
        "-DateTimeOriginal",
        "-SubSecTimeOriginal",
        "-OffsetTimeOriginal",
        "-CreateDate",
        "-SubSecCreateDate",
        "-OffsetTimeDigitized",
        "-ModifyDate",
        "-SubSecTime",
        "-OffsetTime",
        "-Model",
        "-CameraModelName",
        "-SerialNumber",
        "-InternalSerialNumber",
        "-ImageNumber",
        "-SequenceNumber",
        "-AFPoint",
        "-AFPointsUsed",
        "-@",
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            input="".join(f"{path}\n" for path in paths),
            capture_output=True,
            text=True,
            # ExifTool is told to emit UTF-8 filenames, so decode as UTF-8 rather
            # than the console locale. A Windows code page would otherwise mangle
            # a non-ASCII SourceFile and lose metadata for the whole batch.
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_exiftool_timeout(len(paths)),
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"ExifTool did not finish within {error.timeout:.0f} seconds for {len(paths)} files"
        ) from error
    if result.returncode != 0:
        message = result.stderr.strip() or "unknown ExifTool error"
        raise RuntimeError(f"ExifTool metadata extraction failed: {message}")

    decoded = json.loads(result.stdout)
    metadata_by_path: dict[Path, CaptureMetadata] = {}
    for item in decoded:
        source = Path(item["SourceFile"]).resolve()
        metadata_by_path[source] = metadata_from_exiftool_values(
            source,
            item,
            assumed_timezone,
        )
    return metadata_by_path


def _exiftool_timeout(file_count: int) -> float:
    """Scale the ExifTool deadline with the collection size.

    A stalled network or removable mount must not hang the run behind an
    indefinite spinner, but a large collection legitimately takes minutes.
    """
    return 60.0 + 0.05 * file_count


def metadata_from_exiftool_values(
    file_path: Path,
    values: dict[str, Any],
    assumed_timezone: tzinfo | None = None,
) -> CaptureMetadata:
    date_candidates = (
        ("DateTimeOriginal", "SubSecTimeOriginal", "OffsetTimeOriginal"),
        ("CreateDate", "SubSecCreateDate", "OffsetTimeDigitized"),
        ("ModifyDate", "SubSecTime", "OffsetTime"),
    )
    for date_key, subsecond_key, offset_key in date_candidates:
        if values.get(date_key):
            try:
                capture_time, timezone_source = parse_capture_datetime(
                    values[date_key],
                    subsecond=values.get(subsecond_key),
                    offset=values.get(offset_key),
                    assumed_timezone=assumed_timezone,
                )
                return _exiftool_metadata(
                    capture_time,
                    f"exiftool:{date_key}",
                    timezone_source,
                    values,
                )
            except ValueError:
                continue

    fallback = filesystem_metadata(file_path)
    return _exiftool_metadata(
        fallback.capture_time,
        fallback.timestamp_source,
        fallback.timezone_source,
        values,
    )


def _exiftool_metadata(
    capture_time: datetime,
    timestamp_source: str,
    timezone_source: str,
    values: dict[str, Any],
) -> CaptureMetadata:
    return CaptureMetadata(
        capture_time=capture_time,
        timestamp_source=timestamp_source,
        timezone_source=timezone_source,
        camera_model=_text(values.get("Model") or values.get("CameraModelName")),
        camera_serial=_text(values.get("SerialNumber") or values.get("InternalSerialNumber")),
        sequence_number=_text(values.get("SequenceNumber") or values.get("ImageNumber")),
        autofocus_info=_text(values.get("AFPointsUsed") or values.get("AFPoint")),
    )


def _named_exif_values(exif: Any) -> dict[str, Any]:
    from PIL import ExifTags

    return {ExifTags.TAGS.get(tag_id, str(tag_id)): value for tag_id, value in exif.items()}


def _text(value: object) -> str:
    return "" if value is None else str(value)
