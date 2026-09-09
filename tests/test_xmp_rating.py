import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from xmp_rating import XMP_NS, rating_for_rank, write_xmp_rating


class XmpRatingTests(unittest.TestCase):
    def test_creates_and_updates_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            image = root / "IMG_0001.ARW"
            image.touch()

            sidecar = write_xmp_rating(image, 5)
            write_xmp_rating(image, 4, "Yellow")
            description = (
                ET.parse(sidecar)
                .getroot()
                .find(".//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description")
            )

        self.assertIsNotNone(description)
        self.assertEqual(description.get(f"{{{XMP_NS}}}Rating"), "4")
        self.assertEqual(description.get(f"{{{XMP_NS}}}Label"), "Yellow")

    def test_rank_rating_bands(self) -> None:
        self.assertEqual(rating_for_rank(1, 100), 5)
        self.assertEqual(rating_for_rank(20, 100), 4)
        self.assertEqual(rating_for_rank(80, 100), 3)


if __name__ == "__main__":
    unittest.main()
