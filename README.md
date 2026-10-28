# Spoolman → Niimbot B1

Print filament labels to a **Niimbot B1** straight from your **Spoolman**
inventory — over USB, with a simple local web app. No phone app, no cloud, no
Bluetooth.

- **Web app** — browse your Spoolman spools, preview the label, print. Labels
  are rendered locally (filament type, brand, name, and a QR that links back to
  the spool in Spoolman).
- **CLI tools** — print label PNGs you've exported from Spoolman (single, a
  folder, or a zip).

> **Scope:** this runs on the machine the printer is plugged into. A web server
> can't reach a USB printer on someone else's computer, so this is a
> **local** app (served to `localhost`), not a hosted multi-user service.

![Spoolman to Niimbot web app: spools grouped by material on the left, label preview and print options on the right](https://github.com/user-attachments/assets/d2497c86-9b3f-4baa-a7c4-e281234351ca)

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

## Docker

Best suited to running on a **Linux host** (e.g. a Raspberry Pi or server with
the printer attached). Build and run:

```bash
docker build -t spoolman-niimbot .

# Linux host — pass the USB serial device through so printing works.
# Find it with `ls /dev/ttyACM* /dev/ttyUSB*` (often /dev/ttyACM0).
docker run --rm -p 8777:8777 \
  --device=/dev/ttyACM0 \
  --group-add dialout \
  spoolman-niimbot
```

Then open `http://<host>:8777`.

> **USB caveat:** printing needs the host's USB serial port passed into the
> container. This works on a **Linux host** with `--device=...`. On **Docker
> Desktop (Windows/macOS)** the engine runs in a VM with no host USB access, so
> the web UI and label **preview** work but **printing does not** — run
> `python webapp.py` directly on those hosts to print.

## Quick start — web app

```bash
python webapp.py
# then open http://127.0.0.1:8777
```

In the browser:
1. Enter your Spoolman URL (e.g. `http://spoolman-host:7912`) and **Load spools**.
2. Spools are grouped by material; search or filter by type, then select some.
3. Pick your **printer** (B1 or B1 Pro), **template** (50×30 or 38×25, each
   in the main layout or style B), **port** (e.g. `COM3`), and density.
4. Check the live **preview**, then **Print**.

The label layout: a rounded frame whose left edge is a black spine carrying
the filament **type** in vertical letters; the **name** as the hero beside a
QR code that links to the spool; the **brand** and optional remaining weight
in a footer. **Style B** is a simpler column: the type as a bold black tag,
the brand, then the name, with the QR on the right.

### Printers and templates

Built-in printers: **Niimbot B1** (203 dpi) and **B1 Pro** (300 dpi). Built-in
templates: **50×30** (default) and **38×25**, each in the main layout and
style B. Adding more is a
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

## Calibration

Print a test pattern and compare it to your physical label edges / die-cut.

```bash
python cli/calibrate.py --port COM3
```

## How it maps to the hardware

- B1 resolution: **8 px/mm (~203 dpi)**. 50×30 mm = 400×240 px; 38×25 mm = 304×200 px.
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
  calibrate.py         #   calibration test pattern
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
