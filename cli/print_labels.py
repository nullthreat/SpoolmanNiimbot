#!/usr/bin/env python3
"""
Print Spoolman PNG labels to a Niimbot B1 over USB.

Spoolman exports one 38x25 mm PNG per label (or a zip of PNGs for 5+ labels).
This tool takes a PNG, a folder of PNGs, or a zip, normalizes each to the B1's
native 304x200 px / 1-bit format, and prints them.

Examples
--------
  # List available serial ports
  python print_labels.py --list-ports

  # Dry run: write normalized previews next to the source, print nothing
  python print_labels.py labels.zip --preview

  # Print a single label (auto-detect COM port)
  python print_labels.py spool_42.png

  # Print a whole zip to a specific port at max density
  python print_labels.py labels.zip --port COM5 --density 5

The label is rotated 90 degrees by default so a 38mm-wide design feeds correctly
through the B1 (which prints across its head width). Use --rotate to change.
"""

import argparse
import sys
import time
from pathlib import Path

# Allow running this script directly (python cli/print_labels.py) by putting
# the repo root on the path so `core` is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from serial.tools.list_ports import comports as list_comports

from core import labels as lbl
from core.printer import (
    InfoEnum,
    PrinterClient,
    SerialTransport,
    find_serial_port,
)

B1_MAX_WIDTH_PX = 384


def cmd_list_ports() -> int:
    ports = list(list_comports())
    if not ports:
        print("No serial ports found. Plug in the B1 over USB and power it on.")
        return 1
    print("Available serial ports:")
    for p in ports:
        print(f"  {p.device} : {p.description} [{p.hwid}]")
    return 0


# Known Niimbot RFID label-type codes -> human description.
# type 1 is the common continuous/die-cut gap label; others are gapless/black-mark/transparent.
_LABEL_TYPE_NAMES = {
    1: "With Gaps (standard die-cut)",
    2: "Black Mark",
    3: "Continuous (gapless)",
    4: "Transparent",
    5: "Pre-cut / special",
}


def cmd_info(port: str | None) -> int:
    try:
        port = find_serial_port(port)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    transport = SerialTransport(port)
    try:
        printer = PrinterClient(transport)
        print(f"Port: {port}")
        try:
            print(f"  Device serial : {printer.get_info(InfoEnum.DEVICESERIAL)}")
            print(f"  Battery level : {printer.get_info(InfoEnum.BATTERY)}")
        except Exception as e:  # noqa: BLE001 - best effort
            print(f"  (printer info unavailable: {e})")

        print("Loaded label roll (RFID tag):")
        try:
            rfid = printer.get_rfid()
        except Exception as e:  # noqa: BLE001
            rfid = None
            print(f"  (RFID read failed: {e})")
        if rfid is None:
            print("  No RFID tag detected.")
            print("  Either no roll is loaded, the cover is open, or it's a")
            print("  third-party (non-RFID) roll. Physical size can't be read;")
            print("  measure the label and set --width-mm / --height-mm to match.")
        else:
            type_code = rfid["type"]
            type_name = _LABEL_TYPE_NAMES.get(type_code, "unknown")
            print(f"  Barcode (SKU)      : {rfid['barcode']}")
            print(f"  Serial             : {rfid['serial']}")
            print(f"  UUID               : {rfid['uuid']}")
            print(f"  Label type         : {type_code} ({type_name})")
            print(f"  Labels total       : {rfid['total_len']}")
            print(f"  Labels used        : {rfid['used_len']}")
            remaining = rfid["total_len"] - rfid["used_len"]
            print(f"  Labels remaining   : {remaining}")
            print()
            print("  Note: the RFID tag does NOT encode the physical mm size.")
            print("  Niimbot SKUs map to a size; look up the barcode above, or")
            print("  measure the label. Our pipeline assumes 38 x 25 mm.")
        return 0
    finally:
        transport.close()


def rotate(image: Image.Image, degrees: int) -> Image.Image:
    if degrees == 0:
        return image
    # PIL rotates counter-clockwise; negate for clockwise like the printer app.
    return image.rotate(-degrees, expand=True)


def shift_content(image: Image.Image, top_mm: float, left_mm: float, px_per_mm: int = 8) -> Image.Image:
    """Nudge content down by top_mm and right by left_mm, keeping the overall
    image size fixed (equal amount is cropped off the opposite edge). Corrects
    small print-origin misalignment. Operates in printed-image space, so it's
    applied after rotation; negative values shift up / left."""
    dx = round(left_mm * px_per_mm)
    dy = round(top_mm * px_per_mm)
    if dx == 0 and dy == 0:
        return image
    bg = Image.new(image.mode, image.size, 255)  # white in "1"/"L"
    bg.paste(image, (dx, dy))
    return bg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="PNG file, folder of PNGs, or zip of PNGs")
    ap.add_argument("--port", help="Serial port (e.g. COM5). Auto-detected if omitted.")
    ap.add_argument("--list-ports", action="store_true", help="List serial ports and exit")
    ap.add_argument("--info", action="store_true", help="Query the printer and loaded label roll, then exit")
    ap.add_argument("--density", type=int, default=3, choices=range(1, 6), help="Print density 1-5 (default 3)")
    ap.add_argument("--line-delay", type=float, default=0.02, help="Seconds to wait between image lines; raise if pages truncate (default 0.02)")
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270], help="Clockwise rotation (default 0)")
    ap.add_argument("--width-mm", type=float, default=38.0, help="Label width in mm (default 38)")
    ap.add_argument("--height-mm", type=float, default=25.0, help="Label height in mm (default 25)")
    ap.add_argument("--threshold", type=int, default=128, help="B/W threshold 0-255 (default 128)")
    ap.add_argument("--top-mm", type=float, default=0.5, help="Shift content down by N mm to correct print-origin alignment (default 0.5, from calibration)")
    ap.add_argument("--left-mm", type=float, default=1.0, help="Shift content right by N mm to correct print-origin alignment (default 1.0, from calibration)")
    ap.add_argument("--no-fit", action="store_true", help="Stretch to size instead of fit+center")
    ap.add_argument("--copies", type=int, default=1, help="Copies per label (default 1)")
    ap.add_argument("--preview", action="store_true", help="Write normalized PNGs, do not print")
    ap.add_argument("--preview-dir", default="previews", help="Directory for --preview output")
    ap.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = ap.parse_args()

    if args.list_ports:
        return cmd_list_ports()

    if args.info:
        return cmd_info(args.port)

    if not args.source:
        ap.error("a source (PNG / folder / zip) is required unless --list-ports is used")

    try:
        labels = lbl.collect_labels(args.source)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Found {len(labels)} label(s) in {args.source}")

    # Normalize + rotate up front so we can validate width before touching the printer.
    prepared = []
    for label in labels:
        img = lbl.normalize(
            label.image,
            width_mm=args.width_mm,
            height_mm=args.height_mm,
            threshold=args.threshold,
            fit=not args.no_fit,
        )
        img = rotate(img, args.rotate)
        img = shift_content(img, args.top_mm, args.left_mm)
        if img.width > B1_MAX_WIDTH_PX:
            print(
                f"Error: '{label.name}' is {img.width}px wide after rotation, "
                f"exceeding the B1 limit of {B1_MAX_WIDTH_PX}px. "
                f"Try --rotate 90 or adjust --width-mm/--height-mm.",
                file=sys.stderr,
            )
            return 1
        prepared.append((label.name, img))

    if args.preview:
        outdir = Path(args.preview_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        for name, img in prepared:
            out = outdir / f"{Path(name).stem}.preview.png"
            img.save(out)
            print(f"  wrote {out}  ({img.width}x{img.height}px)")
        print("Preview complete. No data sent to the printer.")
        return 0

    # Connect.
    try:
        port = find_serial_port(args.port)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    total_prints = len(prepared) * args.copies
    print(f"Port: {port}")
    print(f"About to print {len(prepared)} label(s) x {args.copies} = {total_prints} impression(s) at density {args.density}.")
    if not args.yes:
        resp = input("Proceed? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted.")
            return 0

    transport = SerialTransport(port)
    try:
        printer = PrinterClient(transport)
        try:
            serial_no = printer.get_info(InfoEnum.DEVICESERIAL)
            battery = printer.get_info(InfoEnum.BATTERY)
            print(f"Connected. Serial: {serial_no}  Battery: {battery}")
        except Exception as e:  # noqa: BLE001 - info is best-effort
            print(f"(Could not read printer info: {e}; continuing anyway)")

        for idx, (name, img) in enumerate(prepared, 1):
            print(f"[{idx}/{len(prepared)}] printing {name} ({img.width}x{img.height}px) x{args.copies}")
            printer.print_image(img, density=args.density, quantity=args.copies, line_delay=args.line_delay)
            if idx < len(prepared):
                time.sleep(0.5)
        print("Done.")
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
