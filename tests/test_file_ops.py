from pathlib import Path
import tempfile
import unittest

from file_ops import (
    ExportCollisionError,
    ExportItem,
    build_export_plan,
    copy_export_plan,
    discover_image_files,
    find_associated_files,
)


class FileOperationsTests(unittest.TestCase):
    def test_discovery_is_sorted_and_excludes_generated_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            (root / "session").mkdir()
            (root / "session" / "B.JPG").touch()
            (root / "session" / "a.arw").touch()
            (root / "picks_20260909_120000").mkdir()
            (root / "picks_20260909_120000" / "old.jpg").touch()
            output_root = root / "custom-output"
            output_root.mkdir()
            (output_root / "generated.jpg").touch()

            discovered = discover_image_files(root, output_root)

            self.assertEqual(
                [path.relative_to(root).as_posix() for path in discovered],
                ["session/a.arw", "session/B.JPG"],
            )

    def test_compound_sidecars_are_included_in_asset_family(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            raw = root / "IMG_0001.ARW"
            jpeg = root / "IMG_0001.JPG"
            sidecar = root / "IMG_0001.ARW.xmp"
            unrelated = root / "IMG_0002.xmp"
            for path in (raw, jpeg, sidecar, unrelated):
                path.touch()

            family = find_associated_files(raw)

            self.assertEqual({path.name for path in family}, {raw.name, jpeg.name, sidecar.name})

    def test_export_plan_deduplicates_families_and_preserves_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            first = root / "day-one"
            second = root / "day-two"
            first.mkdir()
            second.mkdir()
            first_raw = first / "IMG_0001.ARW"
            first_jpeg = first / "IMG_0001.JPG"
            second_raw = second / "IMG_0001.ARW"
            for path in (first_raw, first_jpeg, second_raw):
                path.write_bytes(path.name.encode())

            plan = build_export_plan(root, [first_raw, first_jpeg, second_raw])

            self.assertEqual(
                [item.relative_destination.as_posix() for item in plan],
                [
                    "day-one/IMG_0001.ARW",
                    "day-one/IMG_0001.JPG",
                    "day-two/IMG_0001.ARW",
                ],
            )

    def test_export_refuses_to_overwrite_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "source.jpg"
            source.write_bytes(b"source")
            picks = root / "picks"
            picks.mkdir()
            destination = picks / "source.jpg"
            destination.write_bytes(b"existing")

            with self.assertRaises(FileExistsError):
                copy_export_plan(picks, [ExportItem(source, Path("source.jpg"))])
            self.assertEqual(destination.read_bytes(), b"existing")

    def test_plan_rejects_case_insensitive_destination_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            upper = root / "IMG.JPG"
            lower = root / "img.jpg"
            upper.touch()
            lower.touch()

            if upper.samefile(lower):
                self.skipTest("The test filesystem is case-insensitive")

            with self.assertRaises(ExportCollisionError):
                build_export_plan(root, [upper, lower])


if __name__ == "__main__":
    unittest.main()
