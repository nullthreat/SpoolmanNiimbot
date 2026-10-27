# Spoolman → Niimbot B1

Print filament labels to a **Niimbot B1** straight from your **Spoolman**
inventory — over USB, with a simple local web app. No phone app, no cloud, no
Bluetooth.

- **Web app** — browse your Spoolman spools, preview the label, print. Labels
  are rendered locally (filament type, brand, name, and a QR that links back to
  the spool in Spoolman).
- **CLI tools** — print label PNGs you've exported from Spoolman (single, a
  folder, or a zip), plus a two-up mode for split cable-flag stock.

> **Scope:** this runs on the machine the printer is plugged into. A web server
> can't reach a USB printer on someone else's computer, so this is a
> **local** app (served to `localhost`), not a hosted multi-user service.

## Quick start

```bash
git clone <this-repo>
cd SpoolmanNiimbot
pip install -r requirements.txt

python webapp.py           # then open http://127.0.0.1:8777
```

In the browser: enter your Spoolman URL → **Load spools** → pick your printer
and label template → select spools → check the preview → **Print**.

No Spoolman? Print PNGs you exported from it instead:

```bash
python cli/print_labels.py mylabel.png --port COM3
```

## Requirements

- Python 3.11+ (tested on 3.14)
- A **Niimbot B1** (or **B1 Pro**, 300 dpi) connected over **USB**
- A reachable Spoolman instance (for the web app)

```bash
pip install -r requirements.txt
```

## Quick start — web app

```bash
python webapp.py
# then open http://127.0.0.1:8777
```

In the browser:
1. Enter your Spoolman URL (e.g. `http://spoolman-host:7912`) and **Load spools**.
2. Spools are grouped by material; search or filter by type, then select some.
3. Pick your **printer** (B1 or B1 Pro), **template** (38×25, 50×30, or 40×15
   two-up), **port** (e.g. `COM3`), and density.
4. Check the live **preview**, then **Print**.

The label layout: filament **type** and **brand** stacked above the **name**
(the hero), with a QR code anchored on the right that links to the spool.

### Printers and templates

Built-in printers: **Niimbot B1** (203 dpi) and **B1 Pro** (300 dpi). Built-in
templates: **38×25**, **50×30**, and **40×15** (two-up). Adding more is a
one-line change in `core/models.py` — see [LLM.md](LLM.md).

## CLI — print exported PNGs

Spoolman can export a PNG per label (or a zip of PNGs for 5+). These tools
normalize each image to the B1's native format (8 px/mm, 1-bit) and print it.

### Standard labels (38×25 mm) — `print_labels.py`

```bash
python cli/print_labels.py --list-ports                   # find the COM port
python cli/print_labels.py --info --port COM3             # read the loaded roll
python cli/print_labels.py labels.zip --preview           # dry run (no printing)
python cli/print_labels.py spool_42.png --port COM3       # print one
python cli/print_labels.py labels.zip --port COM3 --density 5 --yes
```

Source can be a single `.png`, a folder of PNGs, or a `.zip` of PNGs.

| Option | Default | Notes |
|---|---|---|
| `--port` | auto | e.g. `COM3`. Auto-detect fails if multiple ports exist. |
| `--density` | 3 | 1–5. Higher = darker. |
| `--rotate` | 0 | Clockwise. 0 prints landscape (upright as Spoolman shows it). |
| `--width-mm` / `--height-mm` | 38 / 25 | Match your label stock. |
| `--top-mm` / `--left-mm` | 0.5 / 1.0 | Print-origin alignment nudge (see calibration). |
| `--line-delay` | 0.01 | Seconds between image lines; raise if a page truncates. |
| `--copies` | 1 | Impressions per label. |
| `--preview` / `--yes` | off | Write previews and exit / skip the prompt. |

### Two-up split stock (40×15 /2) — `print_split_labels.py`

For stacked cable-flag stock: a 40×30 mm label die-cut into two 40×15 mm cells.
Packs **two different** labels per physical unit (one per cell), so you peel the
halves apart and use one per filament. Labels pair in sorted order: 1+2, 3+4, …

```bash
python cli/print_split_labels.py labels.zip --preview
python cli/print_split_labels.py labels.zip --port COM3 --yes
```

## Calibration

Print a test pattern and compare it to your physical label edges / die-cut.

```bash
python cli/calibrate.py --port COM3                  # single stock (38×25)
python cli/calibrate_split.py --port COM3 -W 40 -H 30  # split stock, top/bottom cells
```

## How it maps to the hardware

- B1 resolution: **8 px/mm (~203 dpi)**. 38×25 mm = 304×200 px; 40×30 mm = 320×240 px.
- Max print width: **384 px**.
- Images are pure black/white (no dithering) so text and QR codes stay crisp.
- RFID-tagged rolls report SKU, type, and remaining count (`--info`), but **not**
  the physical size — measure or calibrate to confirm.

## Project layout

```
webapp.py              # entry point: launches the local web app
core/                  # the library
  models.py            #   printer models + label templates (the extension point)
  printer.py           #   self-contained Niimbot B1 serial protocol
  labels.py            #   collect/normalize label PNGs (file, folder, or zip)
  render.py            #   render spool data -> label image (with QR)
  spoolman.py          #   minimal Spoolman REST API client
cli/                   # standalone command-line tools
  print_labels.py      #   print standard single labels
  print_split_labels.py#   print two-up split stock
  calibrate.py         #   calibration test pattern (single)
  calibrate_split.py   #   calibration test pattern (split)
web/static/index.html  # the web UI (single page)
```

## Troubleshooting

- **"No serial ports found"** — plug in over USB, power on, check Device Manager.
- **"Multiple serial ports found"** — pass `--port COMx` (the web app has a dropdown).
- **Blank print** — the line packets need pacing; this is handled in
  `core/printer.py`. If you see it, raise `--line-delay`.
- **Page prints only its top portion** — the printer must be polled to full-page
  completion before the print is ended; also handled in `core/printer.py`.
- **Overfeeds a blank label** — the image is taller than the label; confirm
  `--height-mm` (and any `--top-mm` padding) match the real label size.
- **Faint prints** — raise `--density`.
- **Misaligned print** — print `calibrate.py`, measure the offset, and set
  `--top-mm` / `--left-mm`.

## Credits

The B1 serial protocol is adapted from the MIT-licensed
[niimprint](https://github.com/AndBondStyle/niimprint) project. Spool data comes
from [Spoolman](https://github.com/Donkie/Spoolman).
