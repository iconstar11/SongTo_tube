"""Mood overlay stack: tint, caption gradient, vignette, and optional washes."""

from PIL import Image
import numpy as np

PALETTE = {
    "black": "#000000",
    "royal_purple": "#4B2E6F",
    "noir_velvet": "#1A1520",
    "wine_burgundy": "#5C1A2E",
}

MOOD_DEFAULTS = {
    "black": {
        "tint_opacity": 0.58,
        "gradient_opacity": 0.90,
        "vignette_strength": 0.58,
        "dark_wash_opacity": 0.0,
        "cream_opacity": 0.0,
    },
    "royal_purple": {
        "tint_opacity": 0.35,
        "gradient_opacity": 0.70,
        "vignette_strength": 0.40,
        "dark_wash_opacity": 0.0,
        "cream_opacity": 0.0,
    },
    "noir_velvet": {
        "tint_opacity": 0.44,
        "gradient_opacity": 0.84,
        "vignette_strength": 0.50,
        "dark_wash_opacity": 0.10,
        "cream_opacity": 0.14,
    },
    "wine_burgundy": {
        "tint_opacity": 0.35,
        "gradient_opacity": 0.70,
        "vignette_strength": 0.40,
        "dark_wash_opacity": 0.0,
        "cream_opacity": 0.0,
    },
}

CREAM_COLOR = "#EDE3D0"
DEFAULT_MOOD = "wine_burgundy"


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def mood_tint(width: int, height: int, color: str, opacity: float) -> Image.Image:
    r, g, b = hex_to_rgb(color)
    alpha = int(255 * opacity)
    return Image.new("RGBA", (width, height), (r, g, b, alpha))


def caption_gradient(
    width: int,
    height: int,
    opacity: float = 0.7,
    start_frac: float = 0.55,
) -> Image.Image:
    start_y = int(height * start_frac)
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    if start_y < height:
        y_range = np.arange(start_y, height)
        t = (y_range - start_y) / max(1, (height - start_y))
        alpha_col = (255 * opacity * t).astype(np.uint8)
        pixels[start_y:height, :, 3] = alpha_col[:, None]
    return Image.fromarray(pixels, mode="RGBA")


def vignette(width: int, height: int, strength: float = 0.4) -> Image.Image:
    yy, xx = np.mgrid[0:height, 0:width]
    cx, cy = width / 2, height / 2
    max_dist = np.sqrt(cx ** 2 + cy ** 2)
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max_dist
    alpha = np.clip((dist - 0.4) / 0.6, 0, 1) * 255 * strength
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[:, :, 3] = alpha.astype(np.uint8)
    return Image.fromarray(pixels, mode="RGBA")


def dark_wash(width: int, height: int, opacity: float = 0.15, color: str = "#14110E") -> Image.Image:
    return mood_tint(width, height, color, opacity)


def cream_wash(width: int, height: int, opacity: float = 0.2, color: str = CREAM_COLOR) -> Image.Image:
    return mood_tint(width, height, color, opacity)


def build_overlay_stack(
    width: int,
    height: int,
    mood_color: str,
    tint_opacity: float = 0.35,
    gradient_opacity: float = 0.7,
    vignette_strength: float = 0.4,
    dark_wash_opacity: float = 0.0,
    cream_opacity: float = 0.0,
) -> Image.Image:
    base = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    base = Image.alpha_composite(base, mood_tint(width, height, mood_color, tint_opacity))
    if dark_wash_opacity > 0:
        base = Image.alpha_composite(base, dark_wash(width, height, dark_wash_opacity))
    base = Image.alpha_composite(base, caption_gradient(width, height, gradient_opacity))
    base = Image.alpha_composite(base, vignette(width, height, vignette_strength))
    if cream_opacity > 0:
        base = Image.alpha_composite(base, cream_wash(width, height, cream_opacity))
    return base


def resolve_overlay_params(mood: str, **overrides) -> dict:
    params = MOOD_DEFAULTS.get(mood, MOOD_DEFAULTS[DEFAULT_MOOD]).copy()
    params.update({k: v for k, v in overrides.items() if v is not None})
    return params