#!/usr/bin/env python3
"""
Local web app: browse Spoolman spools, preview labels, print to a Niimbot B1.

Runs on your own PC so it can reach the USB printer. Open the printed URL in a
browser.

    python webapp.py
    # then open http://127.0.0.1:8777

Reuses the working print engine (core/printer.py) and label tooling.
"""

from __future__ import annotations

import io
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from serial.tools.list_ports import comports as list_comports

from core import render
from core import models
from core.printer import InfoEnum, PrinterClient, SerialTransport, find_serial_port
from core.spoolman import SpoolmanClient, SpoolmanError, summarize_spool

APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "web" / "static"
app = FastAPI(title="Spoolman → Niimbot B1")


# --- rendering helpers -------------------------------------------------------

def render_label(spool, spoolman_url, printer_key, template_key, include_qr, include_weight):
    printer, template = models.resolve(printer_key, template_key)
    if template.style == "cell":
        return render.render_cell(
            spool, spoolman_url,
            width_mm=template.width_mm, height_mm=template.height_mm,
            include_qr=include_qr, px_per_mm=printer.px_per_mm,
        )
    return render.render_single(
        spool, spoolman_url,
        width_mm=template.width_mm, height_mm=template.height_mm,
        include_qr=include_qr, include_weight=include_weight,
        px_per_mm=printer.px_per_mm,
    )


def png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --- API ---------------------------------------------------------------------

@app.get("/api/ports")
def api_ports():
    ports = []
    detected_printer = None
    for p in list_comports():
        model = models.detect_model(p.serial_number, p.vid)
        ports.append({
            "device": p.device,
            "description": p.description,
            "serial_number": p.serial_number,
            "detected_model": model,
        })
        if model and detected_printer is None:
            detected_printer = model
    auto = None
    try:
        auto = find_serial_port(None)
    except Exception:  # noqa: BLE001
        auto = None
    # If exactly one Niimbot port was recognized, suggest its device too.
    niimbot_ports = [pt for pt in ports if pt["detected_model"]]
    auto_niimbot = niimbot_ports[0]["device"] if len(niimbot_ports) == 1 else None
    return {
        "ports": ports,
        "auto": auto or auto_niimbot,
        "detected_printer": detected_printer,
    }


@app.get("/api/printer")
def api_printer(port: str | None = Query(None)):
    """Read printer + loaded roll info."""
    try:
        port = find_serial_port(port)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    transport = SerialTransport(port)
    try:
        p = PrinterClient(transport)
        info = {"port": port}
        try:
            info["serial"] = p.get_info(InfoEnum.DEVICESERIAL)
            info["battery"] = p.get_info(InfoEnum.BATTERY)
        except Exception as e:  # noqa: BLE001
            info["warning"] = f"info unavailable: {e}"
        try:
            info["rfid"] = p.get_rfid()
        except Exception:  # noqa: BLE001
            info["rfid"] = None
        return info
    finally:
        transport.close()


@app.get("/api/spools")
def api_spools(url: str = Query(...), include_archived: bool = Query(False)):
    client = SpoolmanClient(url)
    try:
        if not client.health():
            raise HTTPException(502, f"Spoolman at {url} is not healthy / reachable")
        spools = client.spools(include_archived=include_archived)
    except SpoolmanError as e:
        raise HTTPException(502, str(e))
    return {"spools": [summarize_spool(s) for s in spools]}


@app.get("/api/options")
def api_options():
    """Printer models and label templates the UI can offer."""
    return {
        "printers": [{"key": m.key, "label": m.label} for m in models.PRINTER_MODELS],
        "templates": [
            {"key": t.key, "label": t.label, "note": t.note} for t in models.TEMPLATES
        ],
        "default_printer": models.DEFAULT_PRINTER,
        "default_template": models.DEFAULT_TEMPLATE,
    }


@app.get("/api/preview")
def api_preview(
    url: str = Query(...),
    spool_id: int = Query(...),
    printer: str = Query(models.DEFAULT_PRINTER),
    template: str = Query(models.DEFAULT_TEMPLATE),
    qr: bool = Query(True),
    weight: bool = Query(False),
):
    client = SpoolmanClient(url)
    try:
        spool = summarize_spool(client.spool(spool_id))
    except SpoolmanError as e:
        raise HTTPException(502, str(e))
    img = render_label(spool, url, printer, template, qr, weight)
    return Response(content=png_bytes(img), media_type="image/png")


@app.post("/api/print")
def api_print(payload: dict = Body(...)):
    url = payload.get("url", "")
    spool_ids = payload.get("spool_ids", [])
    printer_key = payload.get("printer", models.DEFAULT_PRINTER)
    template_key = payload.get("template", models.DEFAULT_TEMPLATE)
    qr = payload.get("qr", True)
    weight = payload.get("weight", False)
    density = int(payload.get("density", 3))
    port = payload.get("port") or None

    if not spool_ids:
        raise HTTPException(400, "No spools selected")

    try:
        ids = [int(sid) for sid in spool_ids]
    except (TypeError, ValueError):
        raise HTTPException(400, "spool_ids must be integers")

    client = SpoolmanClient(url)
    try:
        spools = [summarize_spool(client.spool(sid)) for sid in ids]
    except SpoolmanError as e:
        raise HTTPException(502, str(e))

    try:
        port = find_serial_port(port)
    except RuntimeError as e:
        raise HTTPException(400, str(e))

    try:
        transport = SerialTransport(port)
    except Exception as e:  # noqa: BLE001 - serial open failure
        raise HTTPException(
            400,
            f"Could not open printer on {port}: {e}. "
            "Is the Niimbot B1 plugged in over USB and powered on?",
        )

    results = []
    try:
        pc = PrinterClient(transport)
        for spool in spools:
            img = render_label(spool, url, printer_key, template_key, qr, weight)
            try:
                pc.print_image(img, density=density)
                results.append({"spool_id": spool["id"], "ok": True})
            except Exception as e:  # noqa: BLE001
                results.append({"spool_id": spool["id"], "ok": False, "error": str(e)})
    finally:
        transport.close()

    return {"port": port, "results": results}


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import os

    # Defaults to localhost for a normal desktop run. In Docker, HOST is set to
    # 0.0.0.0 so the published port is reachable from the host.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8777"))
    shown = "127.0.0.1" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"Open http://{shown}:{port} in your browser")
    # Set reload=True while developing to auto-restart on source changes.
    uvicorn.run(app, host=host, port=port, log_level="info")
