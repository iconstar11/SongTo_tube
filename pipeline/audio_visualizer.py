"""
audio_visualizer.py

Adds a small, audio-reactive, rainbow "pill bar" visualizer overlay to a video.
Pure ffmpeg + numpy + Pillow -- no librosa / no heavy deps.

Visual style: vertical capsule-shaped bars centered on a horizontal midline
(so each bar grows up AND down, like a waveform), random fixed rainbow
colors per bar, mirrored left/right for a symmetric "hill" shape.

CLI:
    python audio_visualizer.py input.mp4 output.mp4 --position top-left

Pipeline integration (drop into VideoToClips as a stage):
    from audio_visualizer import add_visualizer
    add_visualizer("clip.mp4", "clip_viz.mp4", position="top-left")
"""

import argparse
import json
import math
import os
import random
import subprocess
import sys
import colorsys
import tempfile

import numpy as np
from PIL import Image, ImageDraw


# --------------------------------------------------------------------------
# Probing / audio extraction
# --------------------------------------------------------------------------

def probe(path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,width,height",
        "-show_entries", "format=duration",
        "-of", "json", path,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    width = int(stream["width"])
    height = int(stream["height"])
    duration = float(data["format"]["duration"])
    return fps, width, height, duration


def extract_audio_samples(path, sr=44100):
    """Decode the audio track to mono float32 samples in [-1, 1]."""
    cmd = [
        "ffmpeg", "-y", "-i", path,
        "-vn", "-ac", "1", "-ar", str(sr),
        "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=True)
    raw = proc.stdout
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size == 0:
        # No audio track -- return silence so the rest of the pipeline still works.
        samples = np.zeros(sr, dtype=np.float32)
    return samples, sr


# --------------------------------------------------------------------------
# Band analysis (FFT magnitude -> log-spaced bands -> smoothed levels)
# --------------------------------------------------------------------------

def compute_band_levels(samples, sr, fps, n_frames, n_bands,
                         win_size=2048, fmin=50, fmax=8000,
                         attack=0.55, release=0.12):
    """
    Returns array of shape (n_frames, n_bands) with smoothed levels in [0, 1].
    """
    half_win = win_size // 2
    window_fn = np.hanning(win_size).astype(np.float32)

    # log-spaced band edges
    edges = np.logspace(np.log10(fmin), np.log10(min(fmax, sr / 2)), n_bands + 1)
    freqs = np.fft.rfftfreq(win_size, 1.0 / sr)
    bin_idx = [np.searchsorted(freqs, e) for e in edges]

    raw = np.zeros((n_frames, n_bands), dtype=np.float32)
    n_samples = samples.shape[0]

    for i in range(n_frames):
        t = i / fps
        center = int(t * sr)
        start = center - half_win
        end = center + half_win
        if start < 0 or end > n_samples:
            chunk = np.zeros(win_size, dtype=np.float32)
            s0, s1 = max(0, start), min(n_samples, end)
            if s1 > s0:
                chunk[s0 - start:s1 - start] = samples[s0:s1]
        else:
            chunk = samples[start:end]

        spec = np.abs(np.fft.rfft(chunk * window_fn))
        for b in range(n_bands):
            lo, hi = bin_idx[b], max(bin_idx[b] + 1, bin_idx[b + 1])
            raw[i, b] = spec[lo:hi].mean() if hi > lo else 0.0

    # log-compress (so quiet detail is visible, loud doesn't clip everything to 1)
    raw = np.log1p(raw * 40.0)

    # normalize by a robust high percentile across the whole clip
    ref = np.percentile(raw, 97) or 1.0
    raw = np.clip(raw / ref, 0.0, 1.0)

    # attack/release smoothing per band, frame over frame
    smoothed = np.zeros_like(raw)
    prev = np.zeros(n_bands, dtype=np.float32)
    for i in range(n_frames):
        level = raw[i]
        rising = level > prev
        coeff = np.where(rising, attack, release)
        prev = prev + coeff * (level - prev)
        smoothed[i] = prev

    return smoothed


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def make_palette(n_bars, seed=None):
    rng = random.Random(seed)
    colors = []
    for _ in range(n_bars):
        h = rng.random()
        s = rng.uniform(0.65, 0.95)
        v = rng.uniform(0.85, 1.0)
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        colors.append((int(r * 255), int(g * 255), int(b * 255), 255))
    return colors


def render_overlay(levels, out_path, fps, width, height, n_bars,
                    min_bar_h=6, gap_ratio=0.35, palette_seed=None,
                    symmetric=True, supersample=2,
                    draw_origin_line=False, origin_line_color=(45, 45, 45)):
    """
    levels: (n_frames, n_bands) smoothed [0,1] values from compute_band_levels.
    n_bands should equal n_bars (symmetric=False) or n_bars // 2 (symmetric=True).

    Renders on a solid BLACK background (no alpha) and encodes as a normal
    H.264 mp4. Transparency is achieved later via ffmpeg's `colorkey` filter
    at composite time -- this sidesteps flaky VP9/WebM alpha support and
    works identically on Linux/Windows/Mac.

    supersample: render at this multiple then downscale for anti-aliased edges.
    """
    n_frames = levels.shape[0]
    colors = make_palette(n_bars, seed=palette_seed)

    sw, sh = width * supersample, height * supersample
    bar_w = sw / n_bars
    pad = bar_w * gap_ratio / 2.0
    cy = sh / 2.0

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "pipe:0",
        "-c:v", "libx264", "-preset", "fast", "-crf", "16",
        "-pix_fmt", "yuv420p", "-an",
        out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    for i in range(n_frames):
        frame_levels = levels[i]
        if symmetric:
            bars = np.concatenate([frame_levels, frame_levels[::-1]])
        else:
            bars = frame_levels

        img = Image.new("RGB", (sw, sh), (0, 0, 0))
        draw = ImageDraw.Draw(img)

        if draw_origin_line:
            # Draw dashed origin line behind the bars
            thickness = 3 * supersample
            dash_len = 8 * supersample
            dash_gap = 5 * supersample
            x = pad
            while x < sw - pad:
                draw.rectangle([x, cy - thickness/2, min(x + dash_len, sw - pad), cy + thickness/2], fill=origin_line_color)
                x += dash_len + dash_gap

        for idx, lvl in enumerate(bars):
            h = min_bar_h * supersample + lvl * (sh - min_bar_h * supersample)
            x0 = idx * bar_w + pad
            x1 = (idx + 1) * bar_w - pad
            y0 = cy - h / 2
            y1 = cy + h / 2
            radius = max(1.0, (x1 - x0) / 2.0)
            draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=colors[idx][:3])

        if supersample != 1:
            img = img.resize((width, height), Image.LANCZOS)

        proc.stdin.write(img.tobytes())

    proc.stdin.close()
    proc.wait()


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

POSITION_PRESETS = {
    "top-left": lambda W, H, w, h, mx, my: (mx, my),
    "top-right": lambda W, H, w, h, mx, my: (W - w - mx, my),
    "bottom-left": lambda W, H, w, h, mx, my: (mx, H - h - my),
    "bottom-right": lambda W, H, w, h, mx, my: (W - w - mx, H - h - my),
    "center": lambda W, H, w, h, mx, my: ((W - w) // 2, (H - h) // 2),
}


def add_visualizer(input_video, output_video, position="top-right",
                    n_bars=32, overlay_width=None, overlay_height=140,
                    margin=48, margin_y=None, palette_seed=None, symmetric=True,
                    gap_ratio=0.6, draw_origin_line=True, origin_line_color=(45, 45, 45)):
    """
    overlay_width defaults to 35% of the source video width if not given.
    """
    fps, vw, vh, duration = probe(input_video)
    if overlay_width is None:
        overlay_width = int(vw * 0.35)

    n_frames = int(round(duration * fps))
    n_bands = n_bars // 2 if symmetric else n_bars

    samples, sr = extract_audio_samples(input_video)
    levels = compute_band_levels(samples, sr, fps, n_frames, n_bands)

    overlay_path = os.path.join(tempfile.gettempdir(), "_viz_overlay.mp4")
    render_overlay(levels, overlay_path, fps, overlay_width, overlay_height,
                    n_bars, palette_seed=palette_seed, symmetric=symmetric,
                    gap_ratio=gap_ratio, draw_origin_line=draw_origin_line,
                    origin_line_color=origin_line_color)

    mx = margin
    my = margin if margin_y is None else margin_y
    x, y = POSITION_PRESETS[position](vw, vh, overlay_width, overlay_height, mx, my)

    # colorkey strips the solid-black background from the overlay clip so only
    # the bars themselves composite onto the source video.
    filt = (
        f"[1:v]colorkey=0x000000:0.15:0.05[keyed];"
        f"[0:v][keyed]overlay=x={x}:y={y}:shortest=1[v]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", input_video,
        "-i", overlay_path,
        "-filter_complex", filt,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        output_video,
    ]
    try:
        subprocess.run(cmd, check=True)
    finally:
        if os.path.exists(overlay_path):
            try:
                os.remove(overlay_path)
            except Exception:
                pass
    return output_video


def main():
    p = argparse.ArgumentParser(description="Add a rainbow audio visualizer overlay to a video.")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--position", default="top-right", choices=list(POSITION_PRESETS))
    p.add_argument("--bars", type=int, default=32)
    p.add_argument("--width", type=int, default=None)
    p.add_argument("--height", type=int, default=140)
    p.add_argument("--margin", type=int, default=48)
    p.add_argument("--margin-y", type=int, default=None)
    p.add_argument("--gap-ratio", type=float, default=0.6)
    p.add_argument("--no-origin-line", action="store_true")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--no-symmetric", action="store_true")
    args = p.parse_args()

    add_visualizer(
        args.input, args.output,
        position=args.position,
        n_bars=args.bars,
        overlay_width=args.width,
        overlay_height=args.height,
        margin=args.margin,
        margin_y=args.margin_y,
        palette_seed=args.seed,
        symmetric=not args.no_symmetric,
        gap_ratio=args.gap_ratio,
        draw_origin_line=not args.no_origin_line,
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
