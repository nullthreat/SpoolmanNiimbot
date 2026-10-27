"""
Image collection + normalization for Spoolman PNG labels.

Spoolman exports one PNG per label at 38x25 mm. The Niimbot B1 prints at
8 px/mm (~203 dpi), so 38x25 mm == 304x200 px. The B1's max print width is
384 px, so a 304 px wide label fits comfortably.

This module gathers labels from a single PNG, a folder of PNGs, or a zip
archive (Spoolman delivers 5+ labels as a zip), then normalizes each image to
the correct pixel dimensions and to pure black/white.
"""

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

PX_PER_MM = 8  # Niimbot B1 native resolution


@dataclass
class Label:
    name: str
    image: Image.Image


def _is_png(filename: str) -> bool:
    return filename.lower().endswith(".png")


def collect_labels(source: str) -> list[Label]:
    """Load labels from a PNG file, a directory of PNGs, or a zip of PNGs.
    Returns them sorted by name for deterministic ordering."""
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Source not found: {source}")

    labels: list[Label] = []

    if path.is_dir():
        for p in sorted(path.iterdir()):
            if p.is_file() and _is_png(p.name):
                labels.append(Label(p.name, Image.open(p).copy()))
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for info in sorted(zf.infolist(), key=lambda i: i.filename):
                if info.is_dir() or not _is_png(info.filename):
                    continue
                data = zf.read(info)
                labels.append(Label(info.filename, Image.open(io.BytesIO(data)).copy()))
    elif _is_png(path.name):
        labels.append(Label(path.name, Image.open(path).copy()))
    else:
        raise ValueError(
            f"Unsupported source '{source}'. Expected a .png, a .zip of PNGs, "
            "or a folder containing PNGs."
        )

    if not labels:
        raise ValueError(f"No PNG labels found in '{source}'.")
    return labels


def normalize(
    image: Image.Image,
    width_mm: float = 38.0,
    height_mm: float = 25.0,
    threshold: int = 128,
    fit: bool = True,
) -> Image.Image:
    """Return a 1-bit image sized for the B1.

    The target canvas is width_mm x height_mm at 8 px/mm. If the source image
    already matches, it is used as-is. Otherwise it is scaled to fit (preserving
    aspect ratio) and centered on a white canvas, so a slightly off-size export
    still prints cleanly.
    """
    target_w = round(width_mm * PX_PER_MM)
    target_h = round(height_mm * PX_PER_MM)

    # Flatten transparency onto white so alpha doesn't turn black on invert.
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        bg = Image.new("RGBA", image.size, (255, 255, 255, 255))
        bg.paste(image.convert("RGBA"), mask=image.convert("RGBA"))
        image = bg.convert("RGB")

    gray = image.convert("L")

    if gray.size == (target_w, target_h):
        resized = gray
    elif fit:
        src = gray.copy()
        src.thumbnail((target_w, target_h), Image.LANCZOS)
        canvas = Image.new("L", (target_w, target_h), 255)
        canvas.paste(src, ((target_w - src.width) // 2, (target_h - src.height) // 2))
        resized = canvas
    else:
        resized = gray.resize((target_w, target_h), Image.LANCZOS)

    # Pure black/white. point() keeps edges crisp for text/QR; no dithering.
    bw = resized.point(lambda p: 0 if p < threshold else 255, mode="L").convert("1")
    return bw
