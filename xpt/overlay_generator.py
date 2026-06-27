"""
overlay_generator.py

Generates mood-tint, caption-legibility gradient, and vignette overlays for
lyric video backgrounds, and composites them onto a video with ffmpeg.

Conventions match the VideoToClips pipeline:
- Idempotent: skips work if the output file already exists.
- print()-only logging.
"""

import argparse
import os
import subprocess

from PIL import Image
import numpy as np

# ---------------------------------------------------------------------------
# Palette (4 moods)
# ---------------------------------------------------------------------------

PALETTE = {
    "black": "#000000",
    "royal_purple": "#4B2E6F",
    "noir_velvet": "#1A1520",
    "wine_burgundy": "#5C1A2E",
}

# Tune per-mood defaults here. CLI flags override any value.
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


def hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


# ---------------------------------------------------------------------------
# Layer generators (each returns an RGBA PIL.Image)
# ---------------------------------------------------------------------------

def mood_tint(width: int, height: int, color: str, opacity: float) -> Image.Image:
    """Flat color wash over the whole frame. opacity: 0.0-1.0"""
    r, g, b = hex_to_rgb(color)
    alpha = int(255 * opacity)
    return Image.new("RGBA", (width, height), (r, g, b, alpha))


def caption_gradient(width: int, height: int, opacity: float = 0.7,
                      start_frac: float = 0.55) -> Image.Image:
    """Black-to-transparent gradient over the bottom portion of the frame,
    for caption legibility. start_frac is where the gradient begins
    (0.55 means the bottom 45% of the frame)."""
    start_y = int(height * start_frac)
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    if start_y < height:
        y_range = np.arange(start_y, height)
        t = (y_range - start_y) / max(1, (height - start_y))
        alpha_col = (255 * opacity * t).astype(np.uint8)
        pixels[start_y:height, :, 3] = alpha_col[:, None]
    return Image.fromarray(pixels, mode="RGBA")


def vignette(width: int, height: int, strength: float = 0.4) -> Image.Image:
    """Darkens the edges, keeps the center clear. strength: 0.0-1.0"""
    yy, xx = np.mgrid[0:height, 0:width]
    cx, cy = width / 2, height / 2
    max_dist = np.sqrt(cx ** 2 + cy ** 2)
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max_dist
    alpha = np.clip((dist - 0.4) / 0.6, 0, 1) * 255 * strength
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[:, :, 3] = alpha.astype(np.uint8)
    return Image.fromarray(pixels, mode="RGBA")


def dark_wash(width: int, height: int, opacity: float = 0.15,
              color: str = "#14110E") -> Image.Image:
    """Warm near-black wash that deepens shadows without killing contrast."""
    return mood_tint(width, height, color, opacity)


def cream_wash(width: int, height: int, opacity: float = 0.2,
               color: str = CREAM_COLOR) -> Image.Image:
    """Soft ivory wash on top — adds a milky, vintage-film warmth."""
    return mood_tint(width, height, color, opacity)


# ---------------------------------------------------------------------------
# Stack builder
# ---------------------------------------------------------------------------

def build_overlay_stack(width: int, height: int, mood_color: str,
                         tint_opacity: float = 0.35,
                         gradient_opacity: float = 0.7,
                         vignette_strength: float = 0.4,
                         dark_wash_opacity: float = 0.0,
                         cream_opacity: float = 0.0) -> Image.Image:
    """Combines mood tint + optional dark/cream washes + caption gradient +
    vignette into one RGBA image (tint at the bottom, cream on top)."""
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
    """Merge CLI overrides with mood-specific preset values."""
    params = MOOD_DEFAULTS.get(mood, MOOD_DEFAULTS["wine_burgundy"]).copy()
    params.update({k: v for k, v in overrides.items() if v is not None})
    return params


def save_overlay_png(width, height, mood_color, output_path, **kwargs) -> str:
    if os.path.exists(output_path):
        print(f"[skip] overlay already exists: {output_path}")
        return output_path
    overlay = build_overlay_stack(width, height, mood_color, **kwargs)
    overlay.save(output_path)
    print(f"[done] overlay saved: {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# ffmpeg compositing
# ---------------------------------------------------------------------------

def apply_overlay_to_video(input_video: str, overlay_png: str, output_video: str) -> str:
    """Composites the overlay PNG over a video using ffmpeg's overlay filter.
    Idempotent: skips if output_video already exists. Assumes overlay_png
    matches the video's resolution."""
    if os.path.exists(output_video):
        print(f"[skip] output already exists: {output_video}")
        return output_video

    cmd = [
        "ffmpeg", "-y",
        "-i", input_video,
        "-i", overlay_png,
        "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto",
        "-c:a", "copy",
        output_video,
    ]
    print(f"[run] {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[error] ffmpeg failed:\n{result.stderr}")
        raise RuntimeError("ffmpeg overlay compositing failed")
    print(f"[done] video saved: {output_video}")
    return output_video


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate and apply lyric video overlays")
    parser.add_argument("--mood", choices=list(PALETTE.keys()), default="wine_burgundy")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--tint-opacity", type=float, default=None)
    parser.add_argument("--gradient-opacity", type=float, default=None)
    parser.add_argument("--vignette-strength", type=float, default=None)
    parser.add_argument("--dark-wash-opacity", type=float, default=None)
    parser.add_argument("--cream-opacity", type=float, default=None)
    parser.add_argument("--overlay-out", default="overlay.png")
    parser.add_argument("--video-in", default=None)
    parser.add_argument("--video-out", default=None)
    args = parser.parse_args()

    color = PALETTE[args.mood]
    params = resolve_overlay_params(
        args.mood,
        tint_opacity=args.tint_opacity,
        gradient_opacity=args.gradient_opacity,
        vignette_strength=args.vignette_strength,
        dark_wash_opacity=args.dark_wash_opacity,
        cream_opacity=args.cream_opacity,
    )
    save_overlay_png(args.width, args.height, color, args.overlay_out, **params)

    if args.video_in and args.video_out:
        apply_overlay_to_video(args.video_in, args.overlay_out, args.video_out)


if __name__ == "__main__":
    main()