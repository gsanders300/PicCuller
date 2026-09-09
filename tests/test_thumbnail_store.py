"""The shared review-thumbnail store.

Source files are read-only and unchanged between runs, so a repeat pass over the
same shoot used to re-decode up to `--contact-sheet` images purely to regenerate
byte-identical previews. On a fully cached run that was the only decode left.
"""

import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import cull
from portfolio import link_or_copy


def write_photo(directory: Path, name: str, size: tuple[int, int] = (800, 600)) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    Image.new("RGB", size, "teal").save(path, quality=90)
    return path


class ThumbnailKeyTests(unittest.TestCase):
    def test_the_key_is_stable_for_an_unchanged_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            photo = write_photo(root / "day-one", "a.jpg")

            first = cull._thumbnail_key(photo, root)
            second = cull._thumbnail_key(photo, root)

            self.assertEqual(first, second)

    def test_the_key_survives_the_collection_moving(self) -> None:
        """It is collection-relative, matching the evaluation cache."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            original = root / "CardA" / "shoot"
            photo = write_photo(original / "day-one", "a.jpg")
            before = cull._thumbnail_key(photo, original)

            moved = root / "Archive" / "shoot"
            moved.parent.mkdir(parents=True)
            original.rename(moved)
            moved_photo = moved / "day-one" / "a.jpg"

            self.assertEqual(cull._thumbnail_key(moved_photo, moved), before)

    def test_an_edited_file_gets_a_different_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            photo = write_photo(root, "a.jpg")
            before = cull._thumbnail_key(photo, root)

            Image.new("RGB", (900, 700), "orange").save(photo, quality=90)

            self.assertNotEqual(cull._thumbnail_key(photo, root), before)

    def test_different_files_get_different_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            first = write_photo(root, "a.jpg")
            second = write_photo(root, "b.jpg", size=(640, 480))

            self.assertNotEqual(
                cull._thumbnail_key(first, root), cull._thumbnail_key(second, root)
            )


class CachedThumbnailTests(unittest.TestCase):
    def test_a_second_request_does_not_decode_again(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            photo = write_photo(root, "a.jpg")
            store = root / ".photo-cull" / "thumbnails"

            first = cull._cached_thumbnail(store, photo, root)
            stamp = first.stat().st_mtime_ns
            second = cull._cached_thumbnail(store, photo, root)

            self.assertEqual(first, second)
            self.assertTrue(first.is_file())
            # Untouched, so the file was reused rather than rewritten.
            self.assertEqual(second.stat().st_mtime_ns, stamp)

    def test_the_thumbnail_respects_the_review_dimension(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            photo = write_photo(root, "a.jpg", size=(2400, 1600))
            store = root / "thumbnails"

            with Image.open(cull._cached_thumbnail(store, photo, root)) as thumbnail:
                self.assertLessEqual(max(thumbnail.size), cull.MAX_THUMBNAIL_DIMENSION)
                self.assertEqual(thumbnail.size, (480, 320))

    def test_no_partial_file_survives_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            broken = root / "broken.jpg"
            broken.write_bytes(b"not an image")
            store = root / "thumbnails"

            with self.assertRaises(OSError):
                cull._cached_thumbnail(store, broken, root)

            leftovers = list(store.glob("*")) if store.is_dir() else []
            self.assertEqual(leftovers, [])


class LinkOrCopyTests(unittest.TestCase):
    def test_a_hard_link_avoids_duplicating_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "shared.jpg"
            Image.new("RGB", (64, 48), "red").save(source)
            destination = root / "run" / "0001.jpg"
            destination.parent.mkdir()

            link_or_copy(source, destination)

            self.assertTrue(destination.is_file())
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            if os.stat(destination).st_nlink > 1:
                self.assertEqual(os.stat(destination).st_ino, os.stat(source).st_ino)

    def test_an_existing_destination_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "new.jpg"
            Image.new("RGB", (32, 32), "blue").save(source)
            destination = root / "old.jpg"
            Image.new("RGB", (16, 16), "black").save(destination)

            link_or_copy(source, destination)

            self.assertEqual(destination.read_bytes(), source.read_bytes())

    def test_a_copy_is_used_when_linking_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "shared.jpg"
            Image.new("RGB", (48, 48), "green").save(source)
            destination = root / "copy.jpg"
            real_link = os.link

            def refuse(*_args, **_kwargs):
                raise OSError("cross-device link")

            os.link = refuse
            try:
                link_or_copy(source, destination)
            finally:
                os.link = real_link

            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertNotEqual(os.stat(destination).st_ino, os.stat(source).st_ino)


class PruneTests(unittest.TestCase):
    def test_the_store_is_bounded_to_the_newest_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = Path(temporary_directory).resolve() / "thumbnails"
            store.mkdir()
            for index in range(10):
                entry = store / f"{index:02d}.jpg"
                entry.write_bytes(b"x")
                os.utime(entry, (1_700_000_000 + index, 1_700_000_000 + index))

            removed = cull._prune_thumbnail_store(store, keep=4)
            surviving = sorted(path.name for path in store.glob("*.jpg"))

            self.assertEqual(removed, 6)
            self.assertEqual(surviving, ["06.jpg", "07.jpg", "08.jpg", "09.jpg"])

    def test_a_store_under_the_limit_is_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = Path(temporary_directory).resolve() / "thumbnails"
            store.mkdir()
            (store / "a.jpg").write_bytes(b"x")

            self.assertEqual(cull._prune_thumbnail_store(store, keep=10), 0)
            self.assertTrue((store / "a.jpg").is_file())

    def test_a_missing_store_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            absent = Path(temporary_directory).resolve() / "nothing"

            self.assertEqual(cull._prune_thumbnail_store(absent), 0)


if __name__ == "__main__":
    unittest.main()
