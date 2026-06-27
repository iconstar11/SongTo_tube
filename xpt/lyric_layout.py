"""Line-level lyric layout and karaoke timing for Shorts experiment."""

from dataclasses import dataclass

WIDTH = 720
COLOR_TEXT = (250, 250, 250, 255)
COLOR_ACTIVE = (116, 167, 209, 255)  # #74A7D1

# Peak row centers from frame_003.png (11 lines)
REFERENCE_LINE_CENTERS_Y = [457, 515, 571, 628, 684, 740, 796, 853, 909, 965, 1022]

# Reference lyrics + approximate active windows (seconds) for static frame_003
REFERENCE_LYRICS = [
    ("I'm sorry", 0.0, 2.5),
    ("don't leave me", 2.5, 4.2),
    ("I want you here with me", 4.2, 6.1),
    ("I know that", 6.1, 7.5),
    ("your love is gone", 7.5, 9.2),
    ("I can't breathe", 9.2, 10.8),
    ("I'm so weak", 10.8, 12.2),
    ("I know this isn't easy", 12.2, 14.5),
    ("Don't tell me that", 14.5, 16.2),
    ("your love is gone", 16.2, 18.0),
    ("That your love is gone", 18.0, 28.2),
]

ACTIVE_LINE_INDEX = 2  # frame_003 highlights line 3


@dataclass
class LyricLine:
    text: str
    start_s: float
    end_s: float
    center_y: int


def reference_lines() -> list[LyricLine]:
    lines = []
    for i, (text, start, end) in enumerate(REFERENCE_LYRICS):
        cy = REFERENCE_LINE_CENTERS_Y[i] if i < len(REFERENCE_LINE_CENTERS_Y) else 457 + i * 56
        lines.append(LyricLine(text=text, start_s=start, end_s=end, center_y=cy))
    return lines


def layout_line_centers(n_lines: int, y0: int = 457, y1: int = 1022) -> list[int]:
    if n_lines <= 0:
        return []
    if n_lines == 1:
        return [y0]
    return [int(y0 + i * (y1 - y0) / (n_lines - 1)) for i in range(n_lines)]


def slice_alignment(words: list[dict], start_ms: int, end_ms: int) -> list[dict]:
    clipped = [w for w in words if w["end_ms"] > start_ms and w["start_ms"] < end_ms]
    return [
        {
            "word": w["word"],
            "start_ms": max(0, w["start_ms"] - start_ms),
            "end_ms": min(end_ms - start_ms, w["end_ms"] - start_ms),
        }
        for w in clipped
    ]


def group_words_into_lines(
    alignment: list[dict],
    *,
    max_width_px: int = 560,
    measure_width=None,
    gap_break_s: float = 0.45,
    max_words_per_line: int = 5,
) -> list[LyricLine]:
    """Group Whisper word alignment into display lines (for E5+)."""
    if not alignment:
        return reference_lines()

    lines_raw: list[list[dict]] = []
    current: list[dict] = []

    def flush():
        nonlocal current
        if current:
            lines_raw.append(current)
            current = []

    for i, word in enumerate(alignment):
        if current and len(current) >= max_words_per_line:
            flush()
        if current and measure_width:
            text = " ".join(w["word"] for w in current + [word])
            if measure_width(text) > max_width_px:
                flush()
        if current and i > 0:
            gap = (word["start_ms"] - current[-1]["end_ms"]) / 1000.0
            if gap >= gap_break_s:
                flush()
        current.append(word)
    flush()

    if not lines_raw:
        return reference_lines()

    centers = layout_line_centers(len(lines_raw))

    result: list[LyricLine] = []
    for i, chunk in enumerate(lines_raw):
        text = " ".join(w["word"] for w in chunk)
        if text:
            text = text[0].upper() + text[1:]
        result.append(LyricLine(
            text=text,
            start_s=chunk[0]["start_ms"] / 1000.0,
            end_s=chunk[-1]["end_ms"] / 1000.0,
            center_y=centers[i],
        ))
    return result


def active_line_index(lines: list[LyricLine], t_s: float) -> int | None:
    for i, line in enumerate(lines):
        if line.start_s <= t_s < line.end_s:
            return i
    if lines and t_s >= lines[-1].start_s:
        return len(lines) - 1
    return None


def clip_duration_s(lines: list[LyricLine], fallback: float = 28.2) -> float:
    if not lines:
        return fallback
    return max(lines[-1].end_s, fallback)


def draw_lyrics(
    img,
    lines: list[LyricLine],
    *,
    lyric_font,
    active_index: int | None,
    draw,
) -> None:
    for i, line in enumerate(lines):
        color = COLOR_ACTIVE if i == active_index else COLOR_TEXT
        bbox = draw.textbbox((0, 0), line.text, font=lyric_font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (WIDTH - text_w) // 2
        y = line.center_y - text_h // 2
        draw.text((x, y), line.text, font=lyric_font, fill=color)