#!/usr/bin/env python3
"""
Calibration print for a two-up split cable-flag label (e.g. Niimbot 40x15/2).

The physical label is width_mm x height_mm (default 40x15), die-cut-split into
two equal cells across the width. This prints:
  - an outer border at the full 40x15 size,
  - a vertical split line down the middle,
  - a border around each 20x15 cell,
  - "L" / "R" markers and a 10mm ruler.

Compare to the physical label:
  - Outer border matches the sticker edge      -> overall size correct.
  - Split line lands on the physical die-cut    -> cell division correct.

Usage:
  python calibrate_split.py --port COM3                  # 40x15, 2 cells
  python calibrate_split.py --port COM3 -W 50 -H 15      # test other sizes
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw

from core.printer import InfoEnum, PrinterClient, SerialTransport, find_serial_port

PX_PER_MM = 8
B1_MAX_WIDTH_PX = 384


def make_split_calibration(width_mm: float, height_mm: float, cells: int, axis: str) -> Image.Image:
    """axis='v' stacks cells top/bottom (horizontal split lines).
    axis='h' places cells left/right (vertical split lines)."""
    w = round(width_mm * PX_PER_MM)
    h = round(height_mm * PX_PER_MM)
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)

    # Outer border.
    d.rectangle([0, 0, w - 1, h - 1], outline=0, width=2)

    for i in range(cells):
        if axis == "v":  # cells stacked top -> bottom
            y0 = round(i * h / cells)
            y1 = round((i + 1) * h / cells) - 1
            x0, x1 = 0, w - 1
            if i < cells - 1:  # split line at cell bottom
                d.line([0, y1, w - 1, y1], fill=0, width=2)
        else:  # cells left -> right
            x0 = round(i * w / cells)
            x1 = round((i + 1) * w / cells) - 1
            y0, y1 = 0, h - 1
            if i < cells - 1:  # split line at cell right
                d.line([x1, 0, x1, h - 1], fill=0, width=2)

        # Inset cell border.
        d.rectangle([x0 + 2, y0 + 2, x1 - 2, y1 - 2], outline=0, width=1)
        # Cell marker.
        label = ("T", "B")[i] if (cells == 2 and axis == "v") else \
                ("L", "R")[i] if (cells == 2) else str(i + 1)
        d.text((x0 + 6, y0 + 4), label, fill=0)
        # Center crosshair.
        ccx, ccy = round((x0 + x1) / 2), round((y0 + y1) / 2)
        d.line([ccx - 8, ccy, ccx + 8, ccy], fill=0, width=1)
        d.line([ccx, ccy - 8, ccx, ccy + 8], fill=0, width=1)

    return img.convert("1")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="Serial port (e.g. COM3). Auto-detected if omitted.")
    ap.add_argument("-W", "--width-mm", type=float, default=40.0)
    ap.add_argument("-H", "--height-mm", type=float, default=15.0)
    ap.add_argument("--cells", type=int, default=2, help="Number of cells (default 2)")
    ap.add_argument("--axis", choices=["v", "h"], default="v", help="Split axis: v=top/bottom stack (default), h=left/right")
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270])
    ap.add_argument("--density", type=int, default=5, choices=range(1, 6))
    ap.add_argument("--top-mm", type=float, default=0.0, help="Alignment shift down (mm); not needed on this RFID-calibrated stock")
    ap.add_argument("--left-mm", type=float, default=0.0, help="Alignment shift right (mm); not needed on this RFID-calibrated stock")
    ap.add_argument("--preview", action="store_true", help="Write PNG, do not print")
    args = ap.parse_args()

    img = make_split_calibration(args.width_mm, args.height_mm, args.cells, args.axis)
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
        out = f"calib_split_{args.width_mm:g}x{args.height_mm:g}_{args.cells}up.png"
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
        print(f"Printing {args.cells}-up calibration for {args.width_mm:g} x {args.height_mm:g} mm ({img.width}x{img.height}px)")
        printer.print_image(img, density=args.density)
        print("Done. Check outer border vs sticker edge, split line vs die-cut.")
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
