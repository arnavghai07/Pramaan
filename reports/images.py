"""
reports/images.py — PRAMAAN
============================
Evidence photographs, sized for a document.

WHY THE IMAGES ARE NOT EMBEDDED AS-IS
--------------------------------------
An evidence photograph is a full-resolution phone capture — several
megapixels, a few megabytes. A page is 17 cm wide at roughly 150 dpi, so
about 1000 pixels are ever actually rendered; embedding the original puts
20x the data into the file for no visible gain and produces a 30 MB report
that an inspector cannot email. Each image is therefore re-encoded once,
at a width the page can actually show.

The resize preserves aspect ratio, and only ever shrinks. Upscaling a
small overlay would make it blurry without adding information, and — more
to the point for PRAMAAN — an evidence image that has been stretched is
exactly the kind of distorted picture CLAUDE.md rule 5 spends a paragraph
warning about. Nothing here changes the geometry of what was measured;
the measurement was made on the raw frame long before this file sees it.

A file that cannot be read or decoded returns None. The renderers then
print the same "not captured" wording they use for an image that was never
taken, rather than emitting a broken image placeholder.
"""
import sys
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, UnidentifiedImageError

#: Widest the page ever shows an image, in pixels. 1400 px across a ~17 cm
#: text column is about 200 dpi — sharp in print, and small enough that
#: three photographs do not dominate the file size.
MAX_WIDTH_PX = 1400
MAX_HEIGHT_PX = 1800
JPEG_QUALITY = 82


class SizedImage:
    """A decoded, page-sized JPEG held in memory, with its pixel dimensions."""

    def __init__(self, data: bytes, width: int, height: int):
        self.data = data
        self.width = width
        self.height = height

    def stream(self) -> BytesIO:
        """
        A fresh stream each time. ReportLab and python-docx both read the
        buffer to its end, so handing the same one to two calls would give
        the second an empty file.
        """
        return BytesIO(self.data)

    def size_for(self, max_width: float, max_height: float) -> tuple[float, float]:
        """
        Fit the image inside a box in whatever unit the caller works in
        (points for ReportLab, EMU-backed Inches for python-docx), keeping
        the aspect ratio and never enlarging beyond the box.
        """
        scale = min(max_width / self.width, max_height / self.height)
        return self.width * scale, self.height * scale


def load_evidence(path: Optional[Path]) -> Optional[SizedImage]:
    """
    Read one evidence file and re-encode it for embedding, or None if the
    inspection has no such image or the file cannot be decoded.
    """
    if path is None:
        return None
    try:
        with Image.open(path) as img:
            # Overlays are PNGs and may carry an alpha channel or a palette;
            # JPEG has neither, so they are flattened onto white first. Doing
            # this explicitly avoids a save-time error on a file that is
            # otherwise perfectly good evidence.
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                flattened = Image.new("RGB", img.size, (255, 255, 255))
                flattened.paste(img, mask=img.split()[-1])
                img = flattened
            elif img.mode != "RGB":
                img = img.convert("RGB")

            img.thumbnail((MAX_WIDTH_PX, MAX_HEIGHT_PX), Image.LANCZOS)
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return SizedImage(buffer.getvalue(), img.width, img.height)
    except (OSError, UnidentifiedImageError, ValueError) as e:
        # An unreadable evidence file must not cost the officer the report.
        print(f"[reports] could not embed evidence image {path}: {e}",
              file=sys.stderr)
        return None
