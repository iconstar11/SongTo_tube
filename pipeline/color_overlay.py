"""Mood overlay presets and compositing for lyric video backgrounds."""

from PIL import Image

from pipeline.mood_overlay import PALETTE, build_overlay_stack, resolve_overlay_params

PRODUCTION_OVERLAYS: dict[str, dict] = {
    "black": {
        "label": "Black",
        "mood": "black",
        "color": PALETTE["black"],
    },
    "royal_purple": {
        "label": "Royal Purple",
        "mood": "royal_purple",
        "color": PALETTE["royal_purple"],
    },
    "noir_velvet": {
        "label": "Noir Velvet",
        "mood": "noir_velvet",
        "color": PALETTE["noir_velvet"],
    },
    "wine_burgundy": {
        "label": "Wine Burgundy",
        "mood": "wine_burgundy",
        "color": PALETTE["wine_burgundy"],
    },
}


def resolve_overlay(name: str | None) -> dict | None:
    if not name:
        return None
    key = name.strip().lower()
    return PRODUCTION_OVERLAYS.get(key)


def apply_overlay(img: Image.Image, overlay_key: str | None) -> Image.Image:
    """Composite the full mood overlay stack onto a background image."""
    preset = resolve_overlay(overlay_key)
    if not preset:
        return img

    mood = preset["mood"]
    params = resolve_overlay_params(mood)
    w, h = img.size
    stack = build_overlay_stack(w, h, preset["color"], **params)
    return Image.alpha_composite(img.convert("RGBA"), stack).convert("RGB")


# Backwards-compatible alias used by older call sites
apply_tint = apply_overlay