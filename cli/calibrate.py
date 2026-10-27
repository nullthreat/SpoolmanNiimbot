#!/usr/bin/env python3
"""
Print a calibration label to check whether the loaded roll matches an assumed
physical size. The pattern is a full-bleed border with corner ticks and a
center crosshair, plus printed dimensions and a 10 mm ruler scale.

Compare the printed border to the physical label edge:
  - Border aligns with the label edges         -> size is correct.
  - Print runs off the edge / shifts off-label  -> label is SMALLER than assumed.
  - Blank margin around the print               -> label is LARGER than assumed.

Usage:
  python calibrate.py --port COM3                 # assume 38 x 25 mm
  python calibrate.py --port COM3 -W 50 -H 30     # test a different size
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw

from core.printer import InfoEnum, PrinterClient, SerialTransport, find_serial_port

PX_PER_MM = 8
B1_MAX_WIDTH_PX = 384


def make_calibration(width_mm: float, height_mm: float) -> Image.Image:
    w = round(width_mm * PX_PER_MM)
    h = round(height_mm * PX_PER_MM)
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)

    # Full-bleed border (1px inset so it isn't clipped).
    d.rectangle([0, 0, w - 1, h - 1], outline=0, width=2)

    # Corner ticks 5 mm long.
    t = 5 * PX_PER_MM
    for (x0, y0, x1, y1) in [
        (0, 0, t, 0), (0, 0, 0, t),                       # top-left
        (w - 1 - t, 0, w - 1, 0), (w - 1, 0, w - 1, t),   # top-right
        (0, h - 1, t, h - 1), (0, h - 1 - t, 0, h - 1),   # bottom-left
        (w - 1 - t, h - 1, w - 1, h - 1), (w - 1, h - 1 - t, w - 1, h - 1),  # bottom-right
    ]:
        d.line([x0, y0, x1, y1], fill=0, width=3)

    # Center crosshair.
    cx, cy = w // 2, h // 2
    d.line([cx - 15, cy, cx + 15, cy], fill=0, width=2)
    d.line([cx, cy - 15, cx, cy + 15], fill=0, width=2)

    # Printed dimensions.
    d.text((8, 8), f"{width_mm:g} x {height_mm:g} mm", fill=0)
    d.text((8, 22), f"{w} x {h} px @ 8/mm", fill=0)

    # 10 mm ruler along the bottom, ticks every 1 mm, labeled every 5 mm.
    base_y = h - 10
    for mm in range(0, 11):
        x = 8 + mm * PX_PER_MM
        if x >= w - 2:
            break
        tick = 6 if mm % 5 == 0 else 3
        d.line([x, base_y, x, base_y - tick], fill=0, width=1)
    d.text((8, base_y - 20), "10mm", fill=0)

    return img.convert("1")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="Serial port (e.g. COM3). Auto-detected if omitted.")
    ap.add_argument("-W", "--width-mm", type=float, default=38.0)
    ap.add_argument("-H", "--height-mm", type=float, default=25.0)
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270])
    ap.add_argument("--density", type=int, default=3, choices=range(1, 6))
    ap.add_argument("--top-mm", type=float, default=0.5, help="Shift down by N mm (alignment correction)")
    ap.add_argument("--left-mm", type=float, default=1.0, help="Shift right by N mm (alignment correction)")
    ap.add_argument("--preview", action="store_true", help="Write PNG, do not print")
    args = ap.parse_args()

    img = make_calibration(args.width_mm, args.height_mm)
    if args.rotate:
        img = img.rotate(-args.rotate, expand=True)
    dx, dy = round(args.left_mm * PX_PER_MM), round(args.top_mm * PX_PER_MM)
    if dx or dy:
        bg = Image.new(img.mode, img.size, 255)
        bg.paste(img, (dx, dy))
        img = bg
    if img.width > B1_MAX_WIDTH_PX:
        print(f"Error: {img.width}px wide exceeds B1 limit {B1_MAX_WIDTH_PX}px.", file=sys.stderr)
        return 1

    if args.preview:
        out = f"calibration_{args.width_mm:g}x{args.height_mm:g}.png"
        img.save(out)
        print(f"Wrote {out} ({img.width}x{img.height}px)")
        return 0

    try:
        port = find_serial_port(args.port)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    transport = SerialTransport(port)
    try:
        printer = PrinterClient(transport)
        try:
            print(f"Connected. Serial: {printer.get_info(InfoEnum.DEVICESERIAL)} Battery: {printer.get_info(InfoEnum.BATTERY)}")
        except Exception as e:  # noqa: BLE001
            print(f"(info unavailable: {e})")
        print(f"Printing calibration for {args.width_mm:g} x {args.height_mm:g} mm ({img.width}x{img.height}px)")
        printer.print_image(img, density=args.density)
        print("Done. Compare the printed border to the physical label edges.")
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
