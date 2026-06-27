"""Audio-synced bar visualizer for Shorts experiment."""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from player_ui import _read_audio_samples, COLOR_WHITE


def slice_samples(samples: np.ndarray, sr: int, start_s: float, end_s: float) -> np.ndarray:
    i0 = max(0, int(start_s * sr))
    i1 = min(len(samples), int(end_s * sr))
    return samples[i0:i1] if i1 > i0 else np.zeros(1, dtype=np.float32)


def _frame_amps_centered(
    samples: np.ndarray,
    center_idx: int,
    window_n: int,
    width: int,
) -> np.ndarray:
    """Amplitude bars for a window centered on the current playhead."""
    half = window_n // 2
    start_idx = max(0, center_idx - half)
    end_idx = min(len(samples), center_idx + half)
    chunk = samples[start_idx:end_idx]
    amps = np.zeros(width, dtype=np.float32)
    if chunk.size <= 1:
        return amps
    for x in range(width):
        c0 = int(x * len(chunk) / width)
        c1 = max(c0 + 1, int((x + 1) * len(chunk) / width))
        amps[x] = float(np.max(np.abs(chunk[c0:c1])))
    kernel = np.ones(7, dtype=np.float32) / 7.0
    return np.convolve(amps, kernel, mode="same")


def precompute_waveform_frames(
    samples: np.ndarray,
    sr: int,
    *,
    fps: int,
    duration_s: float,
    width: int,
    height: int,
    window_s: float = 4.0,
) -> list[Image.Image]:
    """
    Pre-render one bar-strip per frame: a scrolling amplitude window centered
    on the current time (synced visualizer that keeps bar variation).
    """
    n_frames = max(1, int(round(duration_s * fps)))
    window_n = max(1, int(window_s * sr))
    mid = height // 2

    amp_rows: list[np.ndarray] = []
    for i in range(n_frames):
        center_idx = min(len(samples), int((i / fps) * sr))
        amp_rows.append(_frame_amps_centered(samples, center_idx, window_n, width))

    global_peak = max(
        (float(np.percentile(a, 98)) for a in amp_rows if a.max() > 0),
        default=1.0,
    )

    n_bars = min(140, max(80, width // 4))

    frames: list[Image.Image] = []
    for amps in amp_rows:
        norm = np.sqrt(np.clip(amps / global_peak, 0, 1))

        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        for b in range(n_bars):
            x0 = int(b * width / n_bars)
            x1 = max(x0 + 1, int((b + 1) * width / n_bars))
            amp = float(norm[x0:x1].max())
            x = int((b + 0.5) * width / n_bars)
            h = max(2, int((0.06 + amp * 0.62) * (height * 0.34)))
            draw.line((x, mid - h, x, mid + h), fill=COLOR_WHITE, width=2)
        frames.append(img)

    return frames


def load_clip_samples(audio_path: Path, clip_start_s: float, clip_end_s: float, sr: int = 22050) -> np.ndarray:
    full = _read_audio_samples(audio_path, sr=sr)
    return slice_samples(full, sr, clip_start_s, clip_end_s)