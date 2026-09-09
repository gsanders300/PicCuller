import tempfile
import unittest
from pathlib import Path

from file_ops import (
    ExportCollisionError,
    ExportItem,
    build_export_plan,
    copy_export_plan,
    discover_image_files,
    find_associated_files,
    select_primary_images,
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
            target = root / "target.bin"
            target.touch()
            try:
                (root / "linked.jpg").symlink_to(target)
            except OSError:
                # Creating symlinks may require elevated privileges on Windows.
                pass

            discovered = discover_image_files(root, output_root)

            self.assertEqual(
                [path.relative_to(root).as_posix() for path in discovered],
                ["session/a.arw", "session/B.JPG"],
            )

    def test_appledouble_companions_are_not_discovered(self) -> None:
        """macOS writes ._NAME.ARW beside originals on exFAT and FAT media."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            (root / "IMG_0001.ARW").touch()
            (root / "._IMG_0001.ARW").touch()
            (root / "._IMG_0002.JPG").touch()

            discovered = discover_image_files(root)

            self.assertEqual([path.name for path in discovered], ["IMG_0001.ARW"])

    def test_system_directories_are_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            (root / "keep.jpg").touch()
            for name in (
                ".Trashes",
                ".Spotlight-V100",
                "@eaDir",
                "$RECYCLE.BIN",
                "System Volume Information",
                ".fseventsd",
            ):
                directory = root / name
                directory.mkdir()
                (directory / "deleted.jpg").touch()

            discovered = discover_image_files(root)

            self.assertEqual([path.name for path in discovered], ["keep.jpg"])

    def test_an_unreadable_directory_is_reported_not_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            (root / "readable").mkdir()
            (root / "readable" / "a.jpg").touch()
            blocked = root / "blocked"
            blocked.mkdir()
            (blocked / "b.jpg").touch()
            blocked.chmod(0o000)
            try:
                try:
                    list(blocked.iterdir())
                except PermissionError:
                    enforced = True
                else:
                    enforced = False

                if not enforced:
                    self.skipTest("This user can read a directory with mode 000")

                errors: list[OSError] = []
                discovered = discover_image_files(root, None, errors.append)

                self.assertEqual([path.name for path in discovered], ["a.jpg"])
                self.assertEqual(len(errors), 1)
                self.assertIn("blocked", str(errors[0].filename))
            finally:
                blocked.chmod(0o700)

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

    def test_primary_selection_prefers_raw_but_falls_back_to_jpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            raw = root / "IMG_0001.ARW"
            paired_jpeg = root / "IMG_0001.JPG"
            jpeg_only = root / "IMG_0002.JPG"
            for path in (raw, paired_jpeg, jpeg_only):
                path.touch()

            selected = select_primary_images([paired_jpeg, jpeg_only, raw], "raw")

            self.assertEqual({path.name for path in selected}, {raw.name, jpeg_only.name})

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
