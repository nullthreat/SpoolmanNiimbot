"""
Render a Spoolman spool into a printable label image.

Produces a black/white image composed at the B1's native 8 px/mm. One layout,
"single": a text column (material badge, brand, filament name, optional
weight) with a QR on the right. Tuned for 50x30 mm (400x240 px), with 38x25 mm
(304x200 px) as a fallback; sizes derive from the label height so both share
the same proportions.

The QR encodes a link back to the spool in Spoolman so you can scan a label and
jump straight to it.
"""

from __future__ import annotations

from functools import lru_cache

import qrcode
from PIL import Image, ImageDraw, ImageFont

# Layouts are composed at this base resolution (Niimbot B1 = 8 px/mm), then
# scaled to the target printer's resolution. See _scale_to / models.py.
BASE_PX_PER_MM = 8


@lru_cache(maxsize=None)
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


def _cap_height(font) -> int:
    """Height of a capital letter above the baseline. Layout is done on cap
    heights / baselines rather than bboxes so spacing is identical no matter
    which glyphs a line happens to contain."""
    return -font.getbbox("H", anchor="ls")[1]


def _descent(font) -> int:
    return font.getmetrics()[1]


def _qr_image(data: str, border: int = 4) -> Image.Image:
    """Base QR at 1px/module with the given quiet zone (in modules)."""
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
    """Render a QR no larger than target_px using INTEGER module scaling, so
    modules stay crisp on a 1-bit printer (no resampling blur).

    The quiet zone may shrink from the standard 4 modules down to 2 when that
    buys a bigger module size: on a 3 mm-wide module budget, 4px modules scan
    far more reliably than 3px ones, and the label's own white margin adds to
    the quiet zone anyway. At equal scale the wider quiet zone wins."""
    n = _qr_image(data, border=0).width
    best_border, best_scale = 4, 0
    for border in (4, 3, 2):
        scale = target_px // (n + 2 * border)
        if scale > best_scale:
            best_border, best_scale = border, scale
    scale = max(1, best_scale)
    base = _qr_image(data, border=best_border)
    return base.resize((base.width * scale, base.height * scale), Image.NEAREST)


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


def _line_pitch(font) -> int:
    """Baseline-to-baseline distance for stacked lines: tight, since these are
    short display lines, not body copy."""
    return round(font.size * 1.08)


def _block_height(font, n_lines: int) -> int:
    return _cap_height(font) + (n_lines - 1) * _line_pitch(font) + _descent(font)


def _fit_block(d, text, max_width, max_height, size_hi, size_lo, max_lines=3, bold=True):
    """Largest font in [size_lo, size_hi] at which text wraps into <= max_lines
    lines that fit (max_width x max_height). The size_hi cap keeps a short
    name like "Red" from ballooning, so a shelf of labels reads as one set.
    As a final guarantee, ellipsizes so text NEVER overflows its column."""
    for size in range(size_hi, size_lo - 1, -1):
        f = _load_font(size, bold)
        lines = _wrap(d, text, f, max_width, max_lines=max_lines)
        if (max(d.textlength(l, font=f) for l in lines) <= max_width
                and _block_height(f, len(lines)) <= max_height):
            return f, lines
    f = _load_font(size_lo, bold)
    n = max(1, min(max_lines, (max_height - _cap_height(f) - _descent(f)) // _line_pitch(f) + 1))
    lines = _wrap(d, text, f, max_width, max_lines=n)
    return f, [_ellipsize(d, l, f, max_width) for l in lines]


def _fit_line(d, text, max_width, size_hi, size_lo, bold=False):
    """Largest font in [size_lo, size_hi] that fits one line; below size_lo the
    text is ellipsized instead of shrinking into illegibility."""
    for size in range(size_hi, size_lo - 1, -1):
        f = _load_font(size, bold)
        if d.textlength(text, font=f) <= max_width:
            return f, text
    f = _load_font(size_lo, bold)
    return f, _ellipsize(d, text, f, max_width)


def _draw_lines(d, x, top, lines, font):
    """Draw stacked lines whose first cap-top sits at `top`. Returns the y of
    the last baseline."""
    base = top + _cap_height(font)
    for i, line in enumerate(lines):
        d.text((x, base + i * _line_pitch(font)), line, fill=0, font=font, anchor="ls")
    return base + (len(lines) - 1) * _line_pitch(font)


def _badge(d, x, top, text, font, max_width, draw=True):
    """Material as a reversed-out (white on black) tag — the first thing the
    eye lands on when pulling a spool off the shelf. Returns (width, height,
    text baseline offset from top); draw=False only measures."""
    cap = _cap_height(font)
    pad_y = max(3, round(cap * 0.32))
    pad_x = pad_y + 2
    text = _ellipsize(d, text, font, max_width - 2 * pad_x)
    bw = round(d.textlength(text, font=font)) + 2 * pad_x
    bh = cap + 2 * pad_y
    if draw:
        d.rounded_rectangle([x, top, x + bw - 1, top + bh - 1], radius=max(2, bh // 5), fill=0)
        d.text((x + pad_x, top + pad_y + cap), text, fill=255, font=font, anchor="ls")
    return bw, bh, pad_y + cap


def _spool_url(base_url: str, spool_id) -> str:
    base = (base_url or "").rstrip("/")
    return f"{base}/spool/show/{spool_id}" if base and spool_id is not None else str(spool_id or "")


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


def shift(img: Image.Image, down_mm: float, right_mm: float, px_per_mm: float) -> Image.Image:
    """Move content on a same-size white canvas (negative = up / left; the
    opposite edges are cropped) to correct a printer's print origin. Applied
    at print time only, so previews stay centered."""
    dx, dy = round(right_mm * px_per_mm), round(down_mm * px_per_mm)
    if dx == 0 and dy == 0:
        return img
    out = Image.new(img.mode, img.size, 255 if img.mode == "L" else 1)
    out.paste(img, (dx, dy))
    return out


def render_single(
    spool: dict,
    spoolman_url: str = "",
    width_mm: float = 50.0,
    height_mm: float = 30.0,
    include_qr: bool = True,
    include_weight: bool = False,
    px_per_mm: float = BASE_PX_PER_MM,
) -> Image.Image:
    """Two-column label. Left column: a header row (material as a reversed
    badge, remaining weight right-aligned beside it), the brand, then the
    filament name as the bold hero. Right column: the QR. The text group is
    centered vertically against the QR.

    Tuned for 50x30; all sizes derive from the label height so 38x25 shares
    the same proportions. Composed at the base 8 px/mm resolution, then scaled
    to the printer's px_per_mm at the end.
    """
    w = round(width_mm * BASE_PX_PER_MM)
    h = round(height_mm * BASE_PX_PER_MM)
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)

    vendor = spool.get("vendor") or ""
    material = spool.get("material") or ""
    name = spool.get("name") or ""
    has_qr = include_qr and spool.get("id") is not None
    weight = (f"{round(spool['remaining_weight'])} g"
              if include_weight and spool.get("remaining_weight") is not None else "")

    M = 8                  # outer safe margin
    gap = round(h * 0.04)  # vertical rhythm between elements

    # --- right column: QR, vertically centered ---
    text_right = w - M
    if has_qr:
        qr = _qr_scaled(_spool_url(spoolman_url, spool["id"]), min(h - 2 * M, round(w * 0.44)))
        # The QR's quiet zone overlaps the right margin a little (the label edge
        # is white anyway); on the left it plus a small gutter separates text.
        qr_x = w - M - qr.width + qr.width // 30
        img.paste(qr.convert("L"), (qr_x, (h - qr.height) // 2))
        text_right = qr_x - 4
    text_w = text_right - M

    # --- measure the stack: header row, brand, name ---
    f_badge = _load_font(round(h * 0.13), True)
    f_weight = _load_font(round(h * 0.1))
    f_brand, brand = _fit_line(d, vendor, text_w, round(h * 0.115), round(h * 0.08)) if vendor else (None, "")

    header_h = badge_w = 0
    if material:
        badge_w, header_h, _ = _badge(d, M, 0, material, f_badge, text_w, draw=False)
    # Weight rides on the header row when it fits; otherwise it's pinned to the
    # bottom-left and the stack centers in the space above it.
    weight_inline = bool(weight) and (
        not material or badge_w + 12 + d.textlength(weight, font=f_weight) <= text_w)
    if weight_inline and not material:
        header_h = _cap_height(f_weight) + _descent(f_weight)

    bottom = h - M
    if weight and not weight_inline:
        bottom -= _cap_height(f_weight) + gap

    stack_h = header_h + (gap if header_h and (brand or name) else 0)
    if brand:
        stack_h += _cap_height(f_brand) + _descent(f_brand) + (gap if name else 0)
    name_lines, f_name = [], None
    if name:
        f_name, name_lines = _fit_block(d, name, text_w, bottom - M - stack_h,
                                        round(h * 0.19), round(h * 0.085))
        stack_h += _block_height(f_name, len(name_lines))

    # --- draw, centered vertically in the space above any bottom weight ---
    y = M + max(0, (bottom - M - stack_h) // 2)
    if header_h:
        if material:
            _, _, base = _badge(d, M, y, material, f_badge, text_w)
            base += y
        else:
            base = y + _cap_height(f_weight)
        if weight_inline and material:
            d.text((text_right, base), weight, fill=0, font=f_weight, anchor="rs")
        elif weight_inline:
            d.text((M, base), weight, fill=0, font=f_weight, anchor="ls")
        y += header_h + gap
    if brand:
        _draw_lines(d, M, y, [brand], f_brand)
        y += _cap_height(f_brand) + _descent(f_brand) + gap
    if name_lines:
        _draw_lines(d, M, y, name_lines, f_name)
    if weight and not weight_inline:
        d.text((M, h - M), weight, fill=0, font=f_weight, anchor="ls")

    return _scale_to(img, width_mm, height_mm, px_per_mm)


def _wrap_widths(d, text, font, widths):
    """Greedy word-wrap where line i must fit widths[i]. Returns the lines, or
    None if the text doesn't fit in len(widths) lines."""
    words = text.split()
    lines, cur = [], ""
    for wd in words:
        trial = (cur + " " + wd).strip()
        if d.textlength(trial, font=font) <= widths[len(lines)]:
            cur = trial
        elif not cur or len(lines) + 1 >= len(widths):
            return None
        else:
            lines.append(cur)
            cur = wd
            if d.textlength(cur, font=font) > widths[len(lines)]:
                return None
    if cur:
        lines.append(cur)
    return lines


def _fit_flow(d, text, x_top, region_top, region_h, narrow_w, wide_w, split_y,
              size_hi, size_lo, max_lines=3):
    """Like _fit_block, but text flows around an obstacle in the top-right:
    lines whose box starts above split_y get narrow_w, lines below get wide_w.
    The block is centered vertically in the region, or bottom-aligned when that
    lets it fit at a larger size. Returns (font, lines, top).
    """
    for size in range(size_hi, size_lo - 1, -1):
        f = _load_font(size, True)
        cap, pitch = _cap_height(f), _line_pitch(f)
        for n in range(1, max_lines + 1):
            bh = _block_height(f, n)
            if bh > region_h:
                break
            # Prefer centered; else drop the block to the bottom of the region
            # so its last lines can run full width under the obstacle.
            for top in (region_top + (region_h - bh) // 2, region_top + region_h - bh):
                widths = [narrow_w if top + i * pitch < split_y else wide_w for i in range(n)]
                lines = _wrap_widths(d, text, f, widths)
                if lines:
                    return f, lines, top
    f, lines = _fit_block(d, text, narrow_w, region_h, size_lo, size_lo, max_lines)
    return f, lines, region_top + max(0, (region_h - _block_height(f, len(lines))) // 2)


def _vertical_text(text, font, fill=255, bg=0) -> Image.Image:
    """Text rotated to read bottom-to-top (like a book spine), on a tight
    canvas sized to the cap height so it centers optically."""
    cap = _cap_height(font)
    tw = round(font.getlength(text))
    tmp = Image.new("L", (tw + 2, cap + 2), bg)
    ImageDraw.Draw(tmp).text((1, cap + 1), text, fill=fill, font=font, anchor="ls")
    return tmp.rotate(90, expand=True)


def render_b(
    spool: dict,
    spoolman_url: str = "",
    width_mm: float = 50.0,
    height_mm: float = 30.0,
    include_qr: bool = True,
    include_weight: bool = False,
    px_per_mm: float = BASE_PX_PER_MM,
) -> Image.Image:
    """Style B: a framed label. The frame's left edge widens into a black
    spine carrying the material in large vertical type. The filament name is
    the hero, filling the box beside a QR in the top-right corner; brand and
    weight share a full-width footer under a rule."""
    w = round(width_mm * BASE_PX_PER_MM)
    h = round(height_mm * BASE_PX_PER_MM)
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)

    vendor = spool.get("vendor") or ""
    material = spool.get("material") or ""
    name = spool.get("name") or ""
    has_qr = include_qr and spool.get("id") is not None
    weight = (f"{round(spool['remaining_weight'])} g"
              if include_weight and spool.get("remaining_weight") is not None else "")

    E = 5                       # frame inset from the label edge
    LW = 3                      # frame line weight
    R = round(h * 0.07)         # frame corner radius
    spine = round(h * 0.2) if material else LW
    P = round(h * 0.04)         # padding inside the frame
    gap = round(h * 0.035)

    # --- frame + spine: a filled black rounded rect with the interior knocked
    # out; the knockout starts at the spine's right edge. ---
    d.rounded_rectangle([E, E, w - 1 - E, h - 1 - E], radius=R, fill=0)
    ix0, iy0, ix1, iy1 = E + spine, E + LW, w - 1 - E - LW, h - 1 - E - LW
    d.rounded_rectangle([ix0, iy0, ix1, iy1], radius=max(1, R - LW), fill=255,
                        corners=(not material, True, True, not material))

    if material:
        span = (iy1 - iy0) - 2 * P
        f, text = _fit_line(d, material, span, round(spine * 0.72), round(spine * 0.4), bold=True)
        vt = _vertical_text(text, f)
        img.paste(vt, (E + (spine - vt.width) // 2, (h - vt.height) // 2))

    cx0, cy0, cx1, cy1 = ix0 + P, iy0 + P, ix1 - P, iy1 - P

    # --- footer: brand left, weight right, one baseline, rule above ---
    rule_y = cy1 + gap
    if vendor or weight:
        f_w = _load_font(round(h * 0.1))
        ww = round(d.textlength(weight, font=f_w)) + 2 * gap if weight else 0
        if weight:
            d.text((cx1, cy1), weight, fill=0, font=f_w, anchor="rs")
        cap = _cap_height(f_w)
        if vendor:
            f_b, brand = _fit_line(d, vendor, cx1 - cx0 - ww, round(h * 0.11), round(h * 0.075))
            d.text((cx0, cy1), brand, fill=0, font=f_b, anchor="ls")
            cap = max(cap, _cap_height(f_b))
        rule_y = cy1 - cap - gap - 2
        d.line([cx0, rule_y, cx1, rule_y], fill=0, width=2)

    # --- QR in the top-right corner, above the rule; its quiet zone doubles
    # as padding against the frame ---
    hero_right, qr_bottom = cx1, cy0
    if has_qr:
        target = min(rule_y - iy0, round((ix1 - ix0) * 0.45))
        qr = _qr_scaled(_spool_url(spoolman_url, spool["id"]), target)
        qx = ix1 + 1 - qr.width
        img.paste(qr.convert("L"), (qx, iy0))
        quiet = qr.width // 15                   # quiet zone is >= 2 modules
        hero_right = qx + quiet - gap
        qr_bottom = iy0 + qr.height - quiet + gap

    # --- hero: the name, centered, wrapping around the QR ---
    if name:
        region_h = rule_y - gap - cy0
        f, lines, top = _fit_flow(d, name, cx0, cy0, region_h, hero_right - cx0, cx1 - cx0,
                                  qr_bottom, round(h * 0.25), round(h * 0.085), max_lines=3)
        _draw_lines(d, cx0, top, lines, f)

    return _scale_to(img, width_mm, height_mm, px_per_mm)
