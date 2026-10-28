"""
Printer models and label templates — the single source of truth and the
extension point for adding new hardware or label sizes.

To add a new label size: append a Template to TEMPLATES.
To add a new printer: append a PrinterModel to PRINTER_MODELS.
See LLM.md at the repo root for a step-by-step guide.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PrinterModel:
    key: str            # stable id used in the API/UI (e.g. "b1")
    label: str          # human name (e.g. "Niimbot B1")
    px_per_mm: float    # print resolution. B1 = 8 (~203 dpi); B1 Pro ~11.81 (300 dpi)
    max_width_px: int   # widest image the print head accepts
    usb_serial_prefixes: tuple[str, ...] = ()  # USB serial-number prefixes that identify this model
    # Nudge printed content to correct the print origin (negative = up / left).
    print_offset_down_mm: float = 0.0
    print_offset_right_mm: float = 0.0


# Niimbot's USB vendor id (seen as "VID:PID=3513:0002" on the B1).
NIIMBOT_USB_VID = 0x3513


@dataclass(frozen=True)
class Template:
    key: str            # stable id used in the API/UI (e.g. "single_38x25")
    label: str          # human name shown in the UI
    width_mm: float
    height_mm: float
    style: str          # render style: "single" or "b"
    note: str = ""      # optional hint shown to the user


# --- Printer models ---------------------------------------------------------
# px_per_mm is the knob that makes a template render crisp on each printer.
PRINTER_MODELS: list[PrinterModel] = [
    # The B1 reports a USB serial number like "B1-XXXXXXXX".
    # Prints sit ~0.5 mm high and ~0.25 mm right on the B1 (measured on
    # printed labels), so content is nudged down and left.
    PrinterModel("b1", "Niimbot B1 (203 dpi)", 8.0, 384,
                 usb_serial_prefixes=("B1-",),
                 print_offset_down_mm=0.5, print_offset_right_mm=-0.25),
    # B1 Pro prints at 300 dpi = 300/25.4 ~= 11.81 px/mm. Head width scales
    # with dpi, so ~50mm usable -> ~591 px. Serial prefixes are a best guess
    # ("B1P"/"B1-PRO") until confirmed on real hardware.
    PrinterModel("b1pro", "Niimbot B1 Pro (300 dpi)", 11.81, 591,
                 usb_serial_prefixes=("B1P", "B1-PRO", "B1PRO")),
]

PRINTER_BY_KEY = {m.key: m for m in PRINTER_MODELS}
DEFAULT_PRINTER = "b1"


def detect_model(serial_number: str | None, vid: int | None = None) -> str | None:
    """Best-effort USB auto-detect. Returns a printer key or None if unknown.

    Matches the USB serial-number prefix (e.g. 'B1-...' -> b1). Most-specific
    (longest) prefixes are tried first so 'B1P...' wins over 'B1-'. If a VID is
    given and it isn't Niimbot's, we don't guess.
    """
    if vid is not None and vid != NIIMBOT_USB_VID:
        return None
    if not serial_number:
        return None
    s = serial_number.strip().upper()
    matches = []
    for m in PRINTER_MODELS:
        for pref in m.usb_serial_prefixes:
            if s.startswith(pref.upper()):
                matches.append((len(pref), m.key))
    if not matches:
        return None
    matches.sort(reverse=True)  # longest prefix wins
    return matches[0][1]


# --- Label templates --------------------------------------------------------
TEMPLATES: list[Template] = [
    Template("single_50x30", "50×30 mm", 50.0, 30.0, "single"),
    Template("b_50x30", "50×30 mm — style B", 50.0, 30.0, "b"),
    Template("single_38x25", "38×25 mm", 38.0, 25.0, "single"),
    Template("b_38x25", "38×25 mm — style B", 38.0, 25.0, "b"),
]

TEMPLATE_BY_KEY = {t.key: t for t in TEMPLATES}
DEFAULT_TEMPLATE = "single_50x30"


def resolve(printer_key: str | None, template_key: str | None) -> tuple[PrinterModel, Template]:
    """Look up a (printer, template) pair, falling back to defaults."""
    printer = PRINTER_BY_KEY.get(printer_key or DEFAULT_PRINTER, PRINTER_BY_KEY[DEFAULT_PRINTER])
    template = TEMPLATE_BY_KEY.get(template_key or DEFAULT_TEMPLATE, TEMPLATE_BY_KEY[DEFAULT_TEMPLATE])
    return printer, template
