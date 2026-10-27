#!/usr/bin/env python3
"""
Print Spoolman PNG labels to a Niimbot B1 on two-up split stock (e.g. 40x15/2).

The physical label is 40x30 mm, die-cut into two 40x15 mm cells stacked
vertically. Spoolman exports each label as a 40x15 mm PNG.

This tool packs TWO DIFFERENT Spoolman labels onto each physical unit, one per
cell, both upright. You peel the two halves apart and use one per filament, so
8 labels print on 4 physical units. If there is an odd label out, its unit's
bottom cell is left blank.

Source may be a single PNG, a folder of PNGs, or a zip of PNGs (the Spoolman
5+ export). Labels are paired in sorted order: 1+2, 3+4, 5+6, ...

Examples
--------
  python print_split_labels.py spoolman-labels.zip --preview
  python print_split_labels.py spoolman-labels.zip --port COM3 --yes
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from core import labels as lbl
from core.printer import InfoEnum, PrinterClient, SerialTransport, find_serial_port

PX_PER_MM = 8
B1_MAX_WIDTH_PX = 384


def compose_two_up(
    top_img: Image.Image | None,
    bottom_img: Image.Image | None,
    label_w_mm: float,
    cell_h_mm: float,
) -> Image.Image:
    """Return a 1-bit image of a full two-up label: one Spoolman label in the
    top cell and a different one in the bottom cell, both upright. A None cell
    is left blank."""
    cell_w = round(label_w_mm * PX_PER_MM)
    cell_h = round(cell_h_mm * PX_PER_MM)

    canvas = Image.new("1", (cell_w, cell_h * 2), 1)  # white
    if top_img is not None:
        top = lbl.normalize(top_img, width_mm=label_w_mm, height_mm=cell_h_mm)
        canvas.paste(top, (0, 0))
    if bottom_img is not None:
        bottom = lbl.normalize(bottom_img, width_mm=label_w_mm, height_mm=cell_h_mm)
        canvas.paste(bottom, (0, cell_h))
    return canvas


def pair_labels(labels):
    """Yield (top_label, bottom_label_or_None) tuples, two per physical unit."""
    for i in range(0, len(labels), 2):
        top = labels[i]
        bottom = labels[i + 1] if i + 1 < len(labels) else None
        yield top, bottom


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="PNG file, folder of PNGs, or zip of PNGs")
    ap.add_argument("--port", help="Serial port (e.g. COM3). Auto-detected if omitted.")
    ap.add_argument("--density", type=int, default=5, choices=range(1, 6), help="Print density 1-5 (default 5)")
    ap.add_argument("--label-width-mm", type=float, default=40.0, help="Cell width in mm (default 40)")
    ap.add_argument("--cell-height-mm", type=float, default=15.0, help="Each cell height in mm (default 15)")
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270], help="Rotate the whole two-up label (default 0)")
    ap.add_argument("--copies", type=int, default=1, help="Copies per physical unit (default 1)")
    ap.add_argument("--preview", action="store_true", help="Write composed PNGs, do not print")
    ap.add_argument("--preview-dir", default="previews_split", help="Directory for --preview output")
    ap.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = ap.parse_args()

    if not args.source:
        ap.error("a source (PNG / folder / zip) is required")

    try:
        labels = lbl.collect_labels(args.source)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    pairs = list(pair_labels(labels))
    print(f"Found {len(labels)} label(s) in {args.source}")
    print(f"Packing 2 labels per physical unit -> {len(pairs)} unit(s).")

    prepared = []
    for top, bottom in pairs:
        top_img = top.image
        bottom_img = bottom.image if bottom is not None else None
        img = compose_two_up(top_img, bottom_img, args.label_width_mm, args.cell_height_mm)
        if args.rotate:
            img = img.rotate(-args.rotate, expand=True)
        if img.width > B1_MAX_WIDTH_PX:
            print(
                f"Error: unit is {img.width}px wide after composition/rotation, "
                f"exceeding the B1 limit of {B1_MAX_WIDTH_PX}px.",
                file=sys.stderr,
            )
            return 1
        top_stem = Path(top.name).stem
        bottom_stem = Path(bottom.name).stem if bottom is not None else "blank"
        unit_name = f"{top_stem}+{bottom_stem}"
        prepared.append((unit_name, img))

    if args.preview:
        outdir = Path(args.preview_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        for name, img in prepared:
            out = outdir / f"{Path(name).stem}.2up.png"
            img.save(out)
            print(f"  wrote {out}  ({img.width}x{img.height}px)")
        print("Preview complete. No data sent to the printer.")
        return 0

    try:
        port = find_serial_port(args.port)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    total = len(prepared) * args.copies
    print(f"Port: {port}")
    print(f"About to print {len(prepared)} two-up label(s) x {args.copies} = {total} impression(s) at density {args.density}.")
    if not args.yes:
        if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 0

    transport = SerialTransport(port)
    try:
        printer = PrinterClient(transport)
        try:
            print(f"Connected. Serial: {printer.get_info(InfoEnum.DEVICESERIAL)}  Battery: {printer.get_info(InfoEnum.BATTERY)}")
        except Exception as e:  # noqa: BLE001
            print(f"(printer info unavailable: {e}; continuing)")

        for idx, (name, img) in enumerate(prepared, 1):
            print(f"[{idx}/{len(prepared)}] printing {name} ({img.width}x{img.height}px) x{args.copies}")
            printer.print_image(img, density=args.density, quantity=args.copies)
            if idx < len(prepared):
                time.sleep(0.5)
        print("Done.")
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
