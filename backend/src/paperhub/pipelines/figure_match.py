"""Find where a cropped figure image sits on its PDF page.

Legacy Marker assets store a figure's image (a crop of the page rendered at
Marker's fixed scale) but not its bbox; when the persisted blocks cannot tell
which region it is, the crop is matched against the page rendered at the same
scale: a coarse search on downscaled copies, then a full-scale refinement.
Pure PIL (the backend stays numpy-free)."""
from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image, ImageChops, ImageStat

# Marker renders figure crops at 192 DPI (measured: 2.66 px per PDF point
# across every Marker asset in the library).
MARKER_DPI = 192
COARSE = 8
# Mean absolute grey-level difference (0-255) a match must stay under.
MAX_MEAN_DIFF = 12.0


def _diff(page: Image.Image, crop: Image.Image, x: int, y: int) -> float:
    region = page.crop((x, y, x + crop.width, y + crop.height))
    return float(sum(ImageStat.Stat(ImageChops.difference(region, crop)).mean))


def locate_crop(
    pdf_path: Path, page_index: int, image_path: Path, dpi: int = MARKER_DPI,
) -> tuple[float, float, float, float] | None:
    """The crop's bbox in PDF points on ``page_index``, or None when it is not
    found there (or is larger than the page at ``dpi``)."""
    with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
        pix = doc[page_index].get_pixmap(dpi=dpi)
        page = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    with Image.open(image_path) as im:
        crop = im.convert("L")
    if crop.width > page.width or crop.height > page.height:
        return None
    small_page = page.reduce(COARSE)
    small_crop = crop.reduce(COARSE)
    best: tuple[float, int, int] | None = None
    for y in range(small_page.height - small_crop.height + 1):
        for x in range(small_page.width - small_crop.width + 1):
            d = _diff(small_page, small_crop, x, y)
            if best is None or d < best[0]:
                best = (d, x, y)
    if best is None:
        return None
    _, cx, cy = best
    fine: tuple[float, int, int] | None = None
    for y in range(max(0, cy * COARSE - COARSE), min(page.height - crop.height,
                                                     cy * COARSE + COARSE) + 1):
        for x in range(max(0, cx * COARSE - COARSE), min(page.width - crop.width,
                                                         cx * COARSE + COARSE) + 1):
            d = _diff(page, crop, x, y)
            if fine is None or d < fine[0]:
                fine = (d, x, y)
    if fine is None or fine[0] > MAX_MEAN_DIFF:
        return None
    _, x, y = fine
    scale = dpi / 72
    return (x / scale, y / scale, (x + crop.width) / scale, (y + crop.height) / scale)
