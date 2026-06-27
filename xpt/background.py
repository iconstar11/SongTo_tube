"""Black starfield + bottom smoke background for Shorts experiment."""

import random
from PIL import Image, ImageDraw, ImageFilter

WIDTH = 720
HEIGHT = 1280
BG_COLOR = (0, 0, 0)
STAR_COUNT = 110


def _draw_smoke(img: Image.Image) -> None:
    """Soft dark clouds along the bottom third."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    blobs = [
        (540, 1190, 280, 140, 42),
        (170, 1230, 250, 125, 38),
        (360, 1100, 320, 155, 34),
        (70, 1140, 210, 105, 30),
        (620, 1060, 190, 95, 26),
    ]
    for cx, cy, rx, ry, alpha in blobs:
        draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=(26, 26, 30, alpha))
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=28))
    img.paste(overlay, (0, 0), overlay)


def _draw_stars(img: Image.Image, seed: int = 42) -> None:
    rng = random.Random(seed)
    draw = ImageDraw.Draw(img)
    for _ in range(STAR_COUNT):
        x = rng.randint(0, WIDTH - 1)
        y = rng.randint(0, int(HEIGHT * 0.82))
        alpha = rng.randint(18, 90)
        size = rng.choice([1, 1, 1, 2])
        draw.ellipse((x, y, x + size, y + size), fill=(255, 255, 255, alpha))


def build_background(width: int = WIDTH, height: int = HEIGHT, seed: int = 42) -> Image.Image:
    img = Image.new("RGBA", (width, height), BG_COLOR + (255,))
    _draw_stars(img, seed=seed)
    _draw_smoke(img)
    return img