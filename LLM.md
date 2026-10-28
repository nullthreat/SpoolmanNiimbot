# LLM guide: extending this project

This file orients an AI assistant (or a new contributor) to add **label
templates** and **printer models** safely. Keep changes small and localized —
the layout and print-sequence code is deliberately conservative.

## Architecture in one minute

```
webapp.py            FastAPI entry point; renders + prints + serves the UI
core/
  models.py          <-- SINGLE SOURCE OF TRUTH: printer models + templates
  render.py          renders spool data -> 1-bit label image
  printer.py         Niimbot B1 serial protocol (do not change lightly)
  labels.py          PNG/zip collection + normalize (for the CLI PNG workflow)
  spoolman.py        Spoolman REST client
cli/                 standalone command-line tools
web/static/          the single-page UI
```

Key facts:
- Labels are **1-bit black/white** (no grayscale). Thin lines/small text must stay crisp.
- Layouts are composed at a **base resolution of 8 px/mm** (`render.BASE_PX_PER_MM`),
  then scaled to the target printer's `px_per_mm` by `render._scale_to`. So you
  design once in "8 px/mm space" and every printer gets a correctly-sized image.
- Two render styles, both tuned for 50×30 with 38×25 as a fallback (sizes
  derive from the label height):
  - `"single"` (`render_single`): text column + QR anchored on the right.
  - `"b"` (`render_b`): rounded frame with a black spine carrying the material
    vertically; the name is the hero beside a top-right QR (wrapping under it
    when long); brand + weight in a footer under a rule.
- Templates wider than a printer's head are clamped to its printable width in
  `webapp.render_label` (50 mm stock on the B1's 48 mm head).

## Add a new label template (most common task)

1. Open `core/models.py`.
2. Append a `Template(...)` to the `TEMPLATES` list:

   ```python
   Template("single_60x40", "60×40 mm (single, XL)", 60.0, 40.0, "single",
            note="Optional hint shown in the UI"),
   ```

   - `key`: a stable, unique id (used by the API/UI). Convention: `style_WxH`.
   - `style`: `"single"` or `"b"` — pick an existing render function.
   - `width_mm` / `height_mm`: the physical label size.

3. That's it. The web UI's Template dropdown reads from `/api/options`, which
   reads `TEMPLATES`. No UI or endpoint edits needed.

4. Verify (no printer required):

   ```bash
   python -c "import webapp; s={'id':1,'vendor':'Acme','material':'PLA','name':'Test','remaining_weight':500}; webapp.render_label(s,'http://x','b1','single_60x40',True,True).save('out.png')"
   ```

   Open `out.png` and confirm nothing overflows or collides.

### If the template needs a genuinely new layout

Only if `"single"`/`"b"` don't fit the shape:
1. Add a `render_<name>(spool, spoolman_url, *, width_mm, height_mm, include_qr,
   px_per_mm=BASE_PX_PER_MM)` function in `core/render.py`. Compose at
   `BASE_PX_PER_MM`, return `_scale_to(img, width_mm, height_mm, px_per_mm)`.
2. Give it a new `style` value and branch to it in `webapp.render_label`
   (and `cli/print_labels.py` if you want CLI support).
3. Reuse the helpers: `_qr_scaled`, `_fit_block`, `_fit_flow`, `_fit_line`, `_badge`, `_vertical_text`,
   `_draw_lines`, `_ellipsize`. `_fit_block` already guarantees text never
   overflows (wrap up to N lines, then ellipsize) — keep that guarantee for any
   new layout. Lay text out on cap heights/baselines (`_cap_height`,
   `anchor="ls"`), not bboxes, so spacing doesn't vary with the glyphs.

## Add a new printer model

1. In `core/models.py`, append a `PrinterModel(...)` to `PRINTER_MODELS`:

   ```python
   PrinterModel("b18", "Niimbot B18 (203 dpi)", 8.0, 384),
   ```

   - `px_per_mm`: resolution. 203 dpi = 8.0; 300 dpi ≈ 11.81.
   - `max_width_px`: widest image the print head accepts (scales with dpi).

2. The UI picks it up automatically via `/api/options`.
3. The CLI print tools assume the B1 (`B1_MAX_WIDTH_PX = 384`); update those
   constants only if you're adding CLI support for a wider/different head.

## Guardrails — do NOT change without strong reason

- **`core/printer.py` `print_image` sequence.** Pace lines + drain the
  printer's return bytes during the send → wait for `end_page_print` → poll
  `get_print_status` to full-page completion → `end_print`. Removing the line
  pacing OR the mid-send drain prints a **blank** label; removing the status
  poll prints only the **top fraction** of the page. All three were found the
  hard way — keep them.
- **1-bit output.** Don't introduce grayscale/dithering; thermal printing is
  pure black/white.
- Keep `models.py` declarative — it's data, not logic.

## Checklist before committing

- [ ] `python -c "import webapp"` succeeds (imports the whole stack).
- [ ] New template renders at both `b1` and `b1pro` with no overflow.
- [ ] `/api/options` lists your addition (start `python webapp.py`, GET it).
- [ ] No changes to `print_image` unless that was the explicit task.
