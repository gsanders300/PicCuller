import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from xmp_rating import RDF_NS, XMP_NS, rating_for_rank, write_xmp_rating

DESCRIPTION = f".//{{{RDF_NS}}}Description"


def ratings(sidecar: Path) -> list[str]:
    """Every rating in the file, in either the attribute or child-element form."""
    root = ET.parse(sidecar).getroot()
    found = []
    for description in root.findall(DESCRIPTION):
        attribute = description.get(f"{{{XMP_NS}}}Rating")
        if attribute is not None:
            found.append(attribute)
        found.extend(
            (child.text or "") for child in description.findall(f"{{{XMP_NS}}}Rating")
        )
    return found


class XmpRatingTests(unittest.TestCase):
    def test_creates_and_updates_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            image = root / "IMG_0001.ARW"
            image.touch()

            sidecar = write_xmp_rating(image, 5)
            write_xmp_rating(image, 4, "Yellow")
            description = ET.parse(sidecar).getroot().find(DESCRIPTION)

        self.assertIsNotNone(description)
        self.assertEqual(description.get(f"{{{XMP_NS}}}Rating"), "4")
        self.assertEqual(description.get(f"{{{XMP_NS}}}Label"), "Yellow")

    def test_rank_rating_bands(self) -> None:
        self.assertEqual(rating_for_rank(1, 100), 5)
        self.assertEqual(rating_for_rank(20, 100), 4)
        self.assertEqual(rating_for_rank(80, 100), 3)


class SidecarMatchingTests(unittest.TestCase):
    def test_another_assets_sidecar_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            (root / "shot.ARW").touch()
            # shot.v2 is a different asset, so its sidecar must not be claimed.
            other = root / "shot.v2.xmp"
            other.write_text(
                '<?xml version="1.0"?>\n'
                '<x:xmpmeta xmlns:x="adobe:ns:meta/" '
                f'xmlns:rdf="{RDF_NS}" xmlns:xmp="{XMP_NS}">'
                '<rdf:RDF><rdf:Description xmp:Rating="1" xmp:Label="Red"/></rdf:RDF>'
                "</x:xmpmeta>\n",
                encoding="utf-8",
            )
            untouched = other.read_bytes()

            sidecar = write_xmp_rating(root / "shot.ARW", 5)

            self.assertEqual(sidecar.name, "shot.xmp")
            self.assertEqual(other.read_bytes(), untouched)
            self.assertEqual(ratings(sidecar), ["5"])

    def test_compound_sidecar_for_the_same_asset_is_updated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            (root / "shot.ARW").touch()
            compound = root / "shot.ARW.xmp"
            compound.write_text(
                '<?xml version="1.0"?>\n'
                '<x:xmpmeta xmlns:x="adobe:ns:meta/" '
                f'xmlns:rdf="{RDF_NS}" xmlns:xmp="{XMP_NS}">'
                '<rdf:RDF><rdf:Description xmp:Rating="2"/></rdf:RDF>'
                "</x:xmpmeta>\n",
                encoding="utf-8",
            )

            sidecar = write_xmp_rating(root / "shot.ARW", 4)

            self.assertEqual(sidecar, compound)
            self.assertEqual(ratings(sidecar), ["4"])

    def test_sidecar_choice_is_deterministic_for_equal_length_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            (root / "shot.ARW").touch()
            for name in ("shot.ARW.xmp", "shot.arw.xmp"):
                target = root / name
                if not target.exists():
                    target.write_text(
                        '<?xml version="1.0"?>\n'
                        '<x:xmpmeta xmlns:x="adobe:ns:meta/" '
                        f'xmlns:rdf="{RDF_NS}" xmlns:xmp="{XMP_NS}">'
                        "<rdf:RDF><rdf:Description/></rdf:RDF>"
                        "</x:xmpmeta>\n",
                        encoding="utf-8",
                    )
            chosen = {write_xmp_rating(root / "shot.ARW", 3).name for _ in range(4)}

        self.assertEqual(len(chosen), 1)


class SingleRatingTests(unittest.TestCase):
    def test_only_one_rating_survives_across_multiple_descriptions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            (root / "shot.ARW").touch()
            (root / "shot.xmp").write_text(
                '<?xml version="1.0"?>\n'
                '<x:xmpmeta xmlns:x="adobe:ns:meta/" '
                f'xmlns:rdf="{RDF_NS}" xmlns:xmp="{XMP_NS}">'
                "<rdf:RDF>"
                '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"/>'
                '<rdf:Description xmp:Rating="2" xmp:Label="Blue"/>'
                "</rdf:RDF></x:xmpmeta>\n",
                encoding="utf-8",
            )

            sidecar = write_xmp_rating(root / "shot.ARW", 5)

            self.assertEqual(ratings(sidecar), ["5"])

    def test_child_element_rating_form_is_replaced_not_duplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            (root / "shot.ARW").touch()
            (root / "shot.xmp").write_text(
                '<?xml version="1.0"?>\n'
                '<x:xmpmeta xmlns:x="adobe:ns:meta/" '
                f'xmlns:rdf="{RDF_NS}" xmlns:xmp="{XMP_NS}">'
                "<rdf:RDF><rdf:Description>"
                "<xmp:Rating>2</xmp:Rating><xmp:Label>Blue</xmp:Label>"
                "</rdf:Description></rdf:RDF></x:xmpmeta>\n",
                encoding="utf-8",
            )

            sidecar = write_xmp_rating(root / "shot.ARW", 5)

            self.assertEqual(ratings(sidecar), ["5"])

    def test_unrelated_metadata_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            (root / "shot.ARW").touch()
            (root / "shot.xmp").write_text(
                '<?xml version="1.0"?>\n'
                '<x:xmpmeta xmlns:x="adobe:ns:meta/" '
                f'xmlns:rdf="{RDF_NS}" xmlns:xmp="{XMP_NS}" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                '<rdf:RDF><rdf:Description dc:creator="Someone" xmp:Rating="1"/>'
                "</rdf:RDF></x:xmpmeta>\n",
                encoding="utf-8",
            )

            sidecar = write_xmp_rating(root / "shot.ARW", 3)
            description = ET.parse(sidecar).getroot().find(DESCRIPTION)

            self.assertEqual(
                description.get("{http://purl.org/dc/elements/1.1/}creator"), "Someone"
            )
            self.assertEqual(ratings(sidecar), ["3"])

    def test_rating_boundaries_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            image = Path(temporary_directory).resolve() / "shot.ARW"
            image.touch()
            for invalid in (0, 6, -1):
                with self.subTest(rating=invalid), self.assertRaises(ValueError):
                    write_xmp_rating(image, invalid)


if __name__ == "__main__":
    unittest.main()
