"""
Render a Spoolman spool into a printable label image.

Produces a black/white image at the B1's native 8 px/mm. Two layouts:
  - "single"  38x25 mm (304x200 px): QR on the left, text on the right,
               filament name across the bottom.
  - "cell"    40x15 mm (320x120 px): compact one-line-per-field, optional small
               QR, for the two-up split stock.

The QR encodes a link back to the spool in Spoolman so you can scan a label and
jump straight to it.
"""

from __future__ import annotations

import qrcode
from PIL import Image, ImageDraw, ImageFont

# Layouts are composed at this base resolution (Niimbot B1 = 8 px/mm), then
# scaled to the target printer's resolution. See _scale_to / models.py.
BASE_PX_PER_MM = 8


def _load_font(size: int, bold: bool = False):
    """Try common system fonts; fall back to PIL default."""
    candidates = (
        ["arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold else ["arial.ttf", "DejaVuSans.ttf"]
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _qr_image(data: str, border: int = 4) -> Image.Image:
    """Base QR at 1px/module, with a 4-module quiet zone (the QR standard)."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("1")


def _qr_scaled(data: str, target_px: int) -> Image.Image:
    """Render a QR as close as possible to target_px using INTEGER module
    scaling, so modules stay crisp on a 1-bit printer (no resampling blur)."""
    base = _qr_image(data, border=4)
    modules = base.width  # includes the 4-module quiet zone each side
    scale = max(1, target_px // modules)
    return base.resize((modules * scale, modules * scale), Image.NEAREST)


def _ellipsize(d, text, font, max_width):
    """Hard-truncate text with an ellipsis so it never exceeds max_width.
    Guarantees the returned string fits, even for a single long word."""
    if d.textlength(text, font=font) <= max_width:
        return text
    ell = "…"
    s = text
    while s and d.textlength(s + ell, font=font) > max_width:
        s = s[:-1]
    return (s + ell) if s else ell


def _fit_name(d, name, max_width, max_height):
    """Pick the largest bold font + line breaking so the filament name fits
    inside (max_width x max_height). Tries 1, then 2, then 3 lines across a
    size range. As a final guarantee, ellipsizes any line that still overflows
    so text NEVER crosses into the QR column."""
    for max_lines, size_hi, size_lo in ((1, 72, 36), (2, 50, 26), (3, 34, 20)):
        for size in range(size_hi, size_lo - 1, -2):
            f = _load_font(size, bold=True)
            line_h = d.textbbox((0, 0), "Xg", font=f)[3]
            if max_lines == 1:
                lines = [name]
            else:
                lines = _wrap(d, name, f, max_width, max_lines=max_lines)
            if len(lines) > max_lines:
                continue
            widest = max(d.textlength(l, font=f) for l in lines)
            block_h = line_h * len(lines) + (len(lines) - 1) * 2
            if widest <= max_width and block_h <= max_height:
                return f, lines
    # Final safety net: smallest size, up to 3 lines, ellipsize each line.
    f = _load_font(20, bold=True)
    lines = _wrap(d, name, f, max_width, max_lines=3)
    lines = [_ellipsize(d, l, f, max_width) for l in lines]
    return f, lines


def _wrap(d, text, font, max_width, max_lines=2):
    """Greedy word-wrap into at most max_lines lines that each fit max_width."""
    words = text.split()
    lines, cur = [], ""
    for wd in words:
        trial = (cur + " " + wd).strip()
        if d.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
            if len(lines) == max_lines - 1:
                break
    if cur:
        lines.append(cur)
    # Append any leftover words to the last line (will be size-checked by caller).
    used = sum(len(l.split()) for l in lines)
    if used < len(words):
        lines[-1] = (lines[-1] + " " + " ".join(words[used:])).strip()
    return lines[:max_lines]


def _spool_url(base_url: str, spool_id) -> str:
    base = (base_url or "").rstrip("/")
    return f"{base}/spool/show/{spool_id}" if base and spool_id is not None else str(spool_id or "")


def _fit_text(draw, text, font_path_size, max_width, bold=False):
    """Return the largest font (<= start size) that fits text in max_width."""
    size = font_path_size
    while size > 6:
        font = _load_font(size, bold)
        w = draw.textlength(text, font=font)
        if w <= max_width:
            return font
        size -= 1
    return _load_font(6, bold)


def _scale_to(img: Image.Image, width_mm: float, height_mm: float, px_per_mm: float) -> Image.Image:
    """Scale a base (8 px/mm) 1-bit layout to a target printer resolution.
    Keeps the tuned layout intact; only resamples at the end. Re-thresholds so
    the result stays pure black/white and crisp."""
    if abs(px_per_mm - BASE_PX_PER_MM) < 1e-6:
        return img.convert("1")
    tw = round(width_mm * px_per_mm)
    th = round(height_mm * px_per_mm)
    scaled = img.convert("L").resize((tw, th), Image.LANCZOS)
    return scaled.point(lambda p: 0 if p < 128 else 255, mode="L").convert("1")


def render_single(
    spool: dict,
    spoolman_url: str = "",
    width_mm: float = 38.0,
    height_mm: float = 25.0,
    include_qr: bool = True,
    include_weight: bool = False,
    px_per_mm: float = BASE_PX_PER_MM,
) -> Image.Image:
    """Two-column label: text on the left (type, brand, then the name as the
    hero), a vertical divider, and a QR anchored on the right.

    The layout is composed at the base 8 px/mm resolution, then scaled to the
    printer's px_per_mm (e.g. the B1 Pro's ~11.81) at the end so the tuned
    spacing/fonts stay consistent across printers.
    """
    w = round(width_mm * BASE_PX_PER_MM)
    h = round(height_mm * BASE_PX_PER_MM)
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)

    vendor = spool.get("vendor") or ""
    material = spool.get("material") or ""
    name = spool.get("name") or ""
    has_qr = include_qr and spool.get("id") is not None

    M = 8  # outer safe margin

    # --- right column: QR anchor ---
    if has_qr:
        url = _spool_url(spoolman_url, spool["id"])
        # Size the QR to the label: as tall as fits (minus margins), capped so
        # it never eats more than ~45% of the width. On a 304px-wide 38x25 this
        # lands ~120px; on a 400px-wide 50x30 it grows to ~190px automatically.
        qr_target = min(h - 2 * M, round(w * 0.45))
        qr = _qr_scaled(url, qr_target)
        gutter = 8
        qr_col_x = w - M - qr.width            # left edge of the QR
        qr_y = (h - qr.height) // 2
        img.paste(qr.convert("L"), (qr_col_x, max(M, qr_y)))
        # vertical divider sits in the gutter just left of the QR
        div_x = qr_col_x - gutter
        d.line([div_x, 20, div_x, h - 21], fill=0, width=2)
        text_right = div_x - gutter
    else:
        text_right = w - M

    text_w = text_right - M
    y = M

    # --- TYPE (material): a modest header line, kept at the previous size. ---
    if material:
        f_type = _fit_text(d, material, 30, text_w)
        type_h = d.textbbox((0, 0), material, font=f_type)[3]
        d.text((M, y), material, fill=0, font=f_type)
        y += type_h + 4

    # --- BRAND (vendor): larger, auto-sized to fill the column width. ---
    if vendor:
        f_brand = _fit_text(d, vendor, 40, text_w, bold=False)
        brand_h = d.textbbox((0, 0), vendor, font=f_brand)[3]
        d.text((M, y), vendor, fill=0, font=f_brand)
        y += brand_h + 8

    # --- weight pinned to bottom-left (optional) ---
    bottom_limit = h - M
    if include_weight and spool.get("remaining_weight") is not None:
        grams = f"{round(spool['remaining_weight'])} g"
        f_w = _fit_text(d, grams, 22, text_w)
        gh = d.textbbox((0, 0), grams, font=f_w)[3]
        d.text((M, h - M - gh), grams, fill=0, font=f_w)
        bottom_limit = h - M - gh - 8

    # --- NAME: the hero. Fill the block between y and bottom_limit. ---
    if name:
        avail_h = bottom_limit - y
        font, chosen_lines = _fit_name(d, name, text_w, avail_h)
        line_h = d.textbbox((0, 0), "Xg", font=font)[3]
        block_h = line_h * len(chosen_lines) + (len(chosen_lines) - 1) * 2
        ny = y + max(0, (avail_h - block_h) // 2)
        for i, line in enumerate(chosen_lines):
            d.text((M, ny + i * (line_h + 2)), line, fill=0, font=font)

    return _scale_to(img, width_mm, height_mm, px_per_mm)


def render_cell(
    spool: dict,
    spoolman_url: str = "",
    width_mm: float = 40.0,
    height_mm: float = 15.0,
    include_qr: bool = True,
    px_per_mm: float = BASE_PX_PER_MM,
) -> Image.Image:
    """40x15 compact label for the two-up split stock: small QR anchored left,
    'brand · type' header and filament name stacked to its right."""
    w = round(width_mm * BASE_PX_PER_MM)
    h = round(height_mm * BASE_PX_PER_MM)
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    pad = 4
    content_left = pad

    if include_qr and spool.get("id") is not None:
        url = _spool_url(spoolman_url, spool["id"])
        qr = _qr_scaled(url, h - 2 * pad)
        img.paste(qr.convert("L"), (pad, pad))
        content_left = pad + qr.width + 6

    text_w = w - content_left - pad
    vendor = spool.get("vendor") or ""
    material = spool.get("material") or ""
    name = spool.get("name") or ""

    header = " · ".join(x for x in [vendor, material] if x)
    f1 = _fit_text(d, header or " ", 15, text_w)
    h1 = d.textbbox((0, 0), header or "X", font=f1)[3]
    d.text((content_left, pad), header, fill=0, font=f1)

    if name:
        avail_h = h - pad - (pad + h1 + 2)
        f2, lines = _fit_name(d, name, text_w, avail_h)
        line_h = d.textbbox((0, 0), "Xg", font=f2)[3]
        ny = pad + h1 + 2
        for i, ln in enumerate(lines):
            d.text((content_left, ny + i * (line_h + 1)), ln, fill=0, font=f2)
    return _scale_to(img, width_mm, height_mm, px_per_mm)
