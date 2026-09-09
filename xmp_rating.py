"""Non-destructive XMP ratings for exported selections."""

from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

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
        description = root.find(f".//{{{RDF_NS}}}Description")
        if description is None:
            rdf = root.find(f".//{{{RDF_NS}}}RDF")
            if rdf is None:
                rdf = ET.SubElement(root, f"{{{RDF_NS}}}RDF")
            description = ET.SubElement(rdf, f"{{{RDF_NS}}}Description")
    else:
        root = ET.Element(f"{{{META_NS}}}xmpmeta")
        rdf = ET.SubElement(root, f"{{{RDF_NS}}}RDF")
        description = ET.SubElement(rdf, f"{{{RDF_NS}}}Description")
        tree = ET.ElementTree(root)

    description.set(f"{{{XMP_NS}}}Rating", str(rating))
    description.set(f"{{{XMP_NS}}}Label", label)
    _atomic_write_tree(tree, sidecar)
    return sidecar


def _find_sidecar(image_path: Path) -> Path | None:
    base = image_path.stem.casefold()
    candidates = [
        path
        for path in image_path.parent.iterdir()
        if path.is_file() and path.suffix.casefold() == ".xmp" and _sidecar_base(path) == base
    ]
    return min(candidates, key=lambda path: len(path.name), default=None)


def _sidecar_base(sidecar: Path) -> str:
    stem = Path(sidecar.stem)
    if stem.suffix:
        stem = Path(stem.stem)
    return stem.name.casefold()


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
