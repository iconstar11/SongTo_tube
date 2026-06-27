# Lyric video overlays

Generates mood-tint, caption-legibility gradient, and vignette overlays for
lyric video backgrounds, then composites them onto your video with ffmpeg.

## Export to your main project

Copy these two files into your pipeline (e.g. next to your other VideoToClips stages):

```
overlay_generator.py
README.md          # optional — keep for reference
```

Install dependencies in that project's Python environment:

```bash
pip install pillow numpy
```

ffmpeg must already be on your PATH.

### Pipeline placement

Run this stage **after** background fetch/scale and **before** caption burn-in:

```
background.mp4  →  overlay stage  →  background_overlaid.mp4  →  captions  →  final.mp4
```

The overlay sits under the lyrics, not on top of them.

### Quick integration (Python)

```python
from overlay_generator import (
    PALETTE,
    MOOD_DEFAULTS,
    build_overlay_stack,
    resolve_overlay_params,
    save_overlay_png,
    apply_overlay_to_video,
)

mood = "wine_burgundy"
width, height = 1920, 1080

params = resolve_overlay_params(mood)
overlay_path = save_overlay_png(
    width, height, PALETTE[mood], "stage_overlay.png", **params
)
apply_overlay_to_video("bg.mp4", overlay_path, "bg_overlaid.mp4")
```

Delete the output files first if you need to force a re-run (the module skips
existing outputs by design).

---

## The 4 moods

| Mood | Hex | Character | Best for |
|------|-----|-----------|----------|
| `black` | `#000000` | Heavy pure-black wash, max contrast | Darkest sections, dramatic drops |
| `royal_purple` | `#4B2E6F` | Cool spiritual purple | Slower, reflective passages |
| `noir_velvet` | `#1A1520` | Elegant velvet + subtle champagne cream | Polished cinematic look |
| `wine_burgundy` | `#5C1A2E` | Warm intense red | Reverent, emotional, gothic moments |

---

## What each overlay contains

Stacking order (bottom → top):

1. **Mood tint** — flat color wash (the mood hex above)
2. **Dark wash** *(noir_velvet only by default)* — warm near-black depth
3. **Caption gradient** — black-to-transparent over the bottom ~45%
4. **Vignette** — edge darkening
5. **Cream wash** *(noir_velvet only by default)* — soft ivory warmth on top

---

## Adjusting overlays

### Option A — edit presets in code (permanent)

Open `overlay_generator.py` and edit `MOOD_DEFAULTS`. Each mood has five knobs:

| Key | Range | Effect |
|-----|-------|--------|
| `tint_opacity` | 0.0–1.0 | Strength of the mood color wash |
| `gradient_opacity` | 0.0–1.0 | Bottom caption darkening |
| `vignette_strength` | 0.0–1.0 | Edge darkening |
| `dark_wash_opacity` | 0.0–1.0 | Extra warm shadow layer |
| `cream_opacity` | 0.0–1.0 | Ivory warmth on top |

Current defaults:

| Mood | Tint | Gradient | Vignette | Dark wash | Cream |
|------|------|----------|----------|-----------|-------|
| `black` | 0.58 | 0.90 | 0.58 | 0.00 | 0.00 |
| `royal_purple` | 0.35 | 0.70 | 0.40 | 0.00 | 0.00 |
| `noir_velvet` | 0.44 | 0.84 | 0.50 | 0.10 | 0.14 |
| `wine_burgundy` | 0.35 | 0.70 | 0.40 | 0.00 | 0.00 |

### Option B — CLI flags (one-off tweaks)

Flags override the preset for that run only:

```bash
python overlay_generator.py \
  --mood black \
  --tint-opacity 0.65 \
  --gradient-opacity 0.92 \
  --vignette-strength 0.60 \
  --overlay-out overlay_black.png
```

All adjustment flags:

| Flag | Description |
|------|-------------|
| `--tint-opacity` | Mood color wash strength |
| `--gradient-opacity` | Caption gradient strength |
| `--vignette-strength` | Edge vignette strength |
| `--dark-wash-opacity` | Warm shadow layer (try 0.10–0.20) |
| `--cream-opacity` | Ivory warmth on top (try 0.10–0.25) |

### Practical tuning tips

- **Lyrics hard to read** → raise `--gradient-opacity` (start at 0.85)
- **Too dark overall** → lower `--tint-opacity` or `--vignette-strength`
- **Too flat/colorless** → raise `--tint-opacity` slightly
- **Want noir_velvet warmth on another mood** → add `--cream-opacity 0.14 --dark-wash-opacity 0.10`

---

## CLI usage

### Overlay PNG only

```bash
python overlay_generator.py --mood wine_burgundy --width 1920 --height 1080 --overlay-out overlay.png
```

### Overlay + composite in one step

```bash
python overlay_generator.py \
  --mood noir_velvet \
  --width 1920 --height 1080 \
  --overlay-out overlay.png \
  --video-in background.mp4 \
  --video-out background_overlaid.mp4
```

### Full CLI reference

| Flag | Default | Description |
|------|---------|-------------|
| `--mood` | `wine_burgundy` | `black`, `royal_purple`, `noir_velvet`, or `wine_burgundy` |
| `--width` / `--height` | `1920` / `1080` | Must match video resolution |
| `--overlay-out` | `overlay.png` | Output PNG path |
| `--video-in` | none | Input video |
| `--video-out` | none | Output video (skipped if exists) |

Outputs are skipped if they already exist (smart-resume).

---

## Notes

- Overlay resolution must match the video. Scale/crop the background first.
- To add grain or particles later, extend `build_overlay_stack()` — compositing
  order is tint → dark wash → gradient → vignette → cream → (your layer).