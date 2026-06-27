"""Decorative music-player chrome: flower, waveform, progress bar, controls."""

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

WIDTH = 720
COLOR_WHITE = (255, 255, 255, 255)
COLOR_TITLE = (250, 250, 250, 255)

# Layout tuned against xpt/frames/frame_003.png
TITLE_Y = 118
TITLE_X = 176
FLOWER_CENTER = (108, 142)
WAVEFORM_BOX = (60, 198, 660, 308)   # left, top, right, bottom
PROGRESS_Y = 318
PROGRESS_X0 = 96
PROGRESS_X1 = 624
CONTROLS_Y = 352
CONTROL_RADIUS = 18
CONTROL_GAP = 70
PLAYHEAD_X = 152  # frame_003 ~5s into clip


def _draw_flower(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
    petal_r = 11
    for angle in range(0, 360, 45):
        import math
        rad = math.radians(angle)
        px = cx + int(math.cos(rad) * 14)
        py = cy + int(math.sin(rad) * 14)
        draw.ellipse((px - petal_r, py - petal_r, px + petal_r, py + petal_r), fill=(255, 255, 255, 255))
    draw.ellipse((cx - 7, cy - 7, cx + 7, cy + 7), fill=(255, 210, 60, 255))
    draw.line((cx, cy + 10, cx - 4, cy + 34), fill=(70, 160, 70, 255), width=3)
    draw.ellipse((cx - 14, cy + 22, cx - 2, cy + 34), fill=(70, 150, 70, 255))


def _draw_control_circle(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int = CONTROL_RADIUS) -> None:
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(200, 200, 200, 255), width=2)


def _draw_icon_rewind(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
    _draw_control_circle(draw, cx, cy)
    h = 9
    # two left-pointing chevrons, centered in circle
    for dx in (5, -5):
        tip_x = cx + dx
        base_x = tip_x + 7
        draw.polygon([(tip_x, cy), (base_x, cy - h), (base_x, cy + h)], fill=COLOR_WHITE)


def _draw_icon_pause(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
    _draw_control_circle(draw, cx, cy)
    bar_h, bar_w, gap = 14, 4, 6
    left_x = cx - gap // 2 - bar_w
    right_x = cx + gap // 2
    top = cy - bar_h // 2
    bottom = cy + bar_h // 2
    draw.rectangle((left_x, top, left_x + bar_w, bottom), fill=COLOR_WHITE)
    draw.rectangle((right_x, top, right_x + bar_w, bottom), fill=COLOR_WHITE)


def _draw_icon_forward(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
    _draw_control_circle(draw, cx, cy)
    h = 9
    for dx in (-5, 5):
        tip_x = cx + dx
        base_x = tip_x - 7
        draw.polygon([(tip_x, cy), (base_x, cy - h), (base_x, cy + h)], fill=COLOR_WHITE)


def _read_audio_samples(audio_path: Path, sr: int = 22050) -> np.ndarray:
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(audio_path),
            "-ac", "1", "-ar", str(sr),
            "-f", "f32le", "-acodec", "pcm_f32le", "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    samples = np.frombuffer(proc.stdout, dtype=np.float32)
    return samples if samples.size else np.zeros(sr, dtype=np.float32)


def _bar_waveform_from_samples(samples: np.ndarray, width: int, height: int) -> Image.Image:
    """Vertical bar waveform matching the reference Short style."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    mid = height // 2
    n = len(samples)
    if n < 2:
        return img

    amps = np.zeros(width, dtype=np.float32)
    for x in range(width):
        i0 = int(x * n / width)
        i1 = max(i0 + 1, int((x + 1) * n / width))
        amps[x] = float(np.max(np.abs(samples[i0:i1])))

    # Smooth + normalize like the reference decorative strip
    kernel = np.ones(9, dtype=np.float32) / 9.0
    amps = np.convolve(amps, kernel, mode="same")
    peak = float(np.percentile(amps, 98)) or 1.0
    amps = np.clip(amps / peak, 0, 1)

    for x in range(width):
        amp = float(amps[x])
        h = max(1, int((0.06 + amp * 0.86) * (height * 0.42)))
        draw.line((x, mid - h, x, mid + h), fill=COLOR_WHITE, width=1)
    return img


def _fallback_waveform(width: int, height: int) -> Image.Image:
    """Synthetic bar waveform when no audio is available."""
    fake = np.sin(np.linspace(0, 18, width * 400)) * np.exp(-np.linspace(-1.2, 1.8, width * 400) ** 2)
    return _bar_waveform_from_samples(fake.astype(np.float32), width, height)


def build_waveform_from_audio(audio_path: Path | None, width: int = 600, height: int = 110) -> Image.Image:
    if audio_path and audio_path.exists():
        try:
            samples = _read_audio_samples(audio_path)
            return _bar_waveform_from_samples(samples, width, height)
        except (subprocess.CalledProcessError, OSError, ValueError):
            pass
    return _fallback_waveform(width, height)


def playhead_x_at(t_s: float, duration_s: float) -> int:
    if duration_s <= 0:
        return PROGRESS_X0
    frac = min(1.0, max(0.0, t_s / duration_s))
    return int(PROGRESS_X0 + frac * (PROGRESS_X1 - PROGRESS_X0))


def build_waveform_strip(audio_path: Path | None) -> Image.Image:
    left, top, right, bottom = WAVEFORM_BOX
    wave_w, wave_h = right - left, bottom - top - 40
    return build_waveform_from_audio(audio_path, wave_w, wave_h)


def waveform_paste_box() -> tuple[int, int, int, int]:
    left, top, right, bottom = WAVEFORM_BOX
    wave_w, wave_h = right - left, bottom - top - 40
    wave_y = top + 8
    paste_x = left + (wave_w - wave_w) // 2
    return paste_x, wave_y, wave_w, wave_h


def paste_waveform(img: Image.Image, waveform: Image.Image) -> Image.Image:
    out = img.copy()
    paste_x, wave_y, wave_w, _ = waveform_paste_box()
    out.paste(waveform, (paste_x, wave_y), waveform)
    return out


def draw_player_base(base: Image.Image, *, title: str, title_font) -> Image.Image:
    """Flower + title + control icons (no waveform / playhead)."""
    img = base.copy()
    draw = ImageDraw.Draw(img)
    _draw_flower(draw, *FLOWER_CENTER)
    draw.text((TITLE_X, TITLE_Y), title, font=title_font, fill=COLOR_TITLE)
    cx = WIDTH // 2
    _draw_icon_rewind(draw, cx - CONTROL_GAP, CONTROLS_Y)
    _draw_icon_pause(draw, cx, CONTROLS_Y)
    _draw_icon_forward(draw, cx + CONTROL_GAP, CONTROLS_Y)
    return img


def draw_player_chrome(
    base: Image.Image,
    *,
    title: str,
    title_font,
    waveform: Image.Image | None = None,
    audio_path: Path | None = None,
) -> Image.Image:
    """Header + optional waveform + controls (no playhead)."""
    img = draw_player_base(base, title=title, title_font=title_font)
    if waveform is not None:
        img = paste_waveform(img, waveform)
    elif audio_path is not None:
        img = paste_waveform(img, build_waveform_strip(audio_path))
    return img


def draw_progress_bar(img: Image.Image, playhead_x: int) -> Image.Image:
    out = img.copy()
    draw = ImageDraw.Draw(out)
    draw.line((PROGRESS_X0, PROGRESS_Y, PROGRESS_X1, PROGRESS_Y), fill=(210, 210, 210, 255), width=2)
    draw.ellipse(
        (playhead_x - 7, PROGRESS_Y - 7, playhead_x + 7, PROGRESS_Y + 7),
        fill=COLOR_WHITE,
    )
    return out


def draw_player_ui(
    base: Image.Image,
    *,
    title: str,
    title_font,
    audio_path: Path | None = None,
    playhead_x: int = PLAYHEAD_X,
    waveform: Image.Image | None = None,
) -> Image.Image:
    img = draw_player_chrome(
        base, title=title, title_font=title_font, waveform=waveform, audio_path=audio_path
    )
    return draw_progress_bar(img, playhead_x)