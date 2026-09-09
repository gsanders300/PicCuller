"""Non-destructive XMP ratings for exported selections."""

from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from file_ops import logical_asset_stem

XMP_NS = "http://ns.adobe.com/xap/1.0/"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
META_NS = "adobe:ns:meta/"

ET.register_namespace("x", META_NS)
ET.register_namespace("rdf", RDF_NS)
ET.register_namespace("xmp", XMP_NS)


def rating_for_rank(rank: int, total: int) -> int:
    fraction = (rank - 1) / max(total, 1)
    if fraction < 0.1:
        return 5
    if fraction < 0.4:
        return 4
    return 3


def write_xmp_rating(image_path: Path, rating: int, label: str = "Green") -> Path:
    """Create or update an XMP sidecar next to an exported image."""
    if not 1 <= rating <= 5:
        raise ValueError("XMP rating must be between 1 and 5")
    sidecar = _find_sidecar(image_path) or image_path.with_suffix(".xmp")

    if sidecar.exists():
        tree = ET.parse(sidecar)
        root = tree.getroot()
        descriptions = root.findall(f".//{{{RDF_NS}}}Description")
        if descriptions:
            description = _rating_description(descriptions)
        else:
            rdf = root.find(f".//{{{RDF_NS}}}RDF")
            if rdf is None:
                rdf = ET.SubElement(root, f"{{{RDF_NS}}}RDF")
            description = ET.SubElement(rdf, f"{{{RDF_NS}}}Description")
            descriptions = [description]
        # A real sidecar can carry the rating as an attribute on one Description
        # and as a child element on another. Clear every other holder so the file
        # ends with exactly one rating and one label.
        for other in descriptions:
            _clear_rating(other, keep=other is description)
    else:
        root = ET.Element(f"{{{META_NS}}}xmpmeta")
        rdf = ET.SubElement(root, f"{{{RDF_NS}}}RDF")
        description = ET.SubElement(rdf, f"{{{RDF_NS}}}Description")
        tree = ET.ElementTree(root)

    description.set(f"{{{XMP_NS}}}Rating", str(rating))
    description.set(f"{{{XMP_NS}}}Label", label)
    _atomic_write_tree(tree, sidecar)
    return sidecar


def _rating_description(descriptions: list[ET.Element]) -> ET.Element:
    """Prefer the Description that already carries a rating, else the first."""
    for description in descriptions:
        if description.get(f"{{{XMP_NS}}}Rating") is not None:
            return description
    for description in descriptions:
        if description.find(f"{{{XMP_NS}}}Rating") is not None:
            return description
    return descriptions[0]


def _clear_rating(description: ET.Element, *, keep: bool) -> None:
    """Remove attribute and child-element rating and label forms."""
    if not keep:
        description.attrib.pop(f"{{{XMP_NS}}}Rating", None)
        description.attrib.pop(f"{{{XMP_NS}}}Label", None)
    for name in (f"{{{XMP_NS}}}Rating", f"{{{XMP_NS}}}Label"):
        for child in description.findall(name):
            description.remove(child)


def _find_sidecar(image_path: Path) -> Path | None:
    """Find an existing sidecar for exactly this asset family.

    Matching uses the same asset-family rule as discovery and export, so
    `shot.ARW` never claims `shot.v2.xmp`, which belongs to a different asset.
    """
    base = logical_asset_stem(image_path)
    candidates = [
        path
        for path in image_path.parent.iterdir()
        if path.is_file()
        and path.suffix.casefold() == ".xmp"
        and logical_asset_stem(path) == base
    ]
    return min(
        candidates,
        key=lambda path: (len(path.name), path.name.casefold()),
        default=None,
    )


def _atomic_write_tree(tree: ET.ElementTree, destination: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        tree.write(temporary_path, encoding="utf-8", xml_declaration=True)
        temporary_path.replace(destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
