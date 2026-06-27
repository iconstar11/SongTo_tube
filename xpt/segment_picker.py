"""
Pick up to 3 non-overlapping Shorts windows per song.

Slot 1 — chorus (label or repeated hook)
Slot 2 — improvise (energy peak / opening moment)
Slot 3 — improvise (bridge / late peak / 2nd chorus)
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

SHORTS_MIN_S = 15.0
SHORTS_MAX_S = 55.0
SHORTS_TARGET_S = 35.0
SHORTS_PAD_MS = 500
MIN_CLIP_GAP_MS = 5_000
MIN_OVERLAP_MS = 3_000
GAP_STRONG_MS = 1200

_SECTION_LABELS = frozenset({
    "chorus", "verse", "bridge", "intro", "outro", "hook", "interlude",
    "refrain", "pre-chorus", "prechorus", "post-chorus", "postchorus",
})


@dataclass
class ClipWindow:
    start_ms: int
    end_ms: int
    source: str
    label: str
    slot: str  # chorus | improv_a | improv_b
    score: float = 0.0

    @property
    def duration_s(self) -> float:
        return max(0.0, (self.end_ms - self.start_ms) / 1000.0)


def _word_key(w: str) -> str:
    return re.sub(r"[^\w'-]", "", w.strip()).lower()


def _gap_ms(words: list[dict], i: int) -> int:
    if i <= 0 or i >= len(words):
        return 0
    return max(0, words[i]["start_ms"] - words[i - 1]["end_ms"])


def _section_blocks(words: list[dict]) -> list[tuple[str, int, int]]:
    """Return (label, content_start_ms, content_end_ms) for each labeled block."""
    blocks: list[tuple[str, int, int]] = []
    i = 0
    n = len(words)
    while i < n:
        key = _word_key(words[i]["word"])
        if key in _SECTION_LABELS:
            label = key
            j = i + 1
            if j < n and re.fullmatch(r"\d+", _word_key(words[j]["word"])):
                j += 1
            content_start = words[j]["start_ms"] if j < n else words[i]["end_ms"]
            k = j
            while k < n:
                nk = _word_key(words[k]["word"])
                if nk in _SECTION_LABELS and k > j:
                    break
                if k > j and _gap_ms(words, k) >= GAP_STRONG_MS * 3:
                    break
                k += 1
            content_end = words[k - 1]["end_ms"] if k > j else content_start + 1000
            if content_end > content_start:
                blocks.append((label, content_start, content_end))
            i = k
        else:
            i += 1
    return blocks


def _expand_window(start_ms: int, end_ms: int, words: list[dict], duration_ms: int) -> tuple[int, int]:
    """Grow or shrink window toward SHORTS_MIN/MAX at natural gaps."""
    start_ms = max(0, start_ms - SHORTS_PAD_MS)
    end_ms = min(duration_ms, end_ms + SHORTS_PAD_MS)
    dur = end_ms - start_ms
    min_ms = int(SHORTS_MIN_S * 1000)
    max_ms = int(SHORTS_MAX_S * 1000)
    target_ms = int(SHORTS_TARGET_S * 1000)

    if dur < min_ms:
        need = min_ms - dur
        end_ms = min(duration_ms, end_ms + need // 2 + need - need // 2)
        start_ms = max(0, start_ms - (need - (end_ms - start_ms - dur)))
        dur = end_ms - start_ms
        if dur < min_ms:
            end_ms = min(duration_ms, start_ms + min_ms)

    if dur > max_ms:
        trim = dur - target_ms if dur > target_ms else dur - max_ms
        end_ms -= trim // 2
        start_ms += trim - trim // 2

    return start_ms, end_ms


def _window_from_words(words: list[dict], i0: int, i1: int, duration_ms: int) -> tuple[int, int]:
    start_ms = words[i0]["start_ms"]
    end_ms = words[i1]["end_ms"]
    return _expand_window(start_ms, end_ms, words, duration_ms)


def _chorus_candidates(words: list[dict], duration_ms: int) -> list[ClipWindow]:
    out: list[ClipWindow] = []
    for label, s, e in _section_blocks(words):
        if label != "chorus":
            continue
        start_ms, end_ms = _expand_window(s, e, words, duration_ms)
        if end_ms - start_ms >= int(SHORTS_MIN_S * 1000) * 0.8:
            out.append(ClipWindow(
                start_ms=start_ms, end_ms=end_ms,
                source="chorus_label", label="Chorus", slot="chorus",
                score=100.0,
            ))
    return out


def _ngram_windows(words: list[dict], duration_ms: int, n: int = 5) -> list[tuple[int, int, int, str]]:
    """Return list of (start_idx, end_idx, repeat_count, phrase)."""
    if len(words) < n:
        return []
    keys = [_word_key(w["word"]) for w in words]
    counts: dict[str, int] = {}
    spans: dict[str, list[tuple[int, int]]] = {}
    for i in range(len(words) - n + 1):
        phrase = " ".join(keys[i:i + n])
        if not phrase or phrase in _SECTION_LABELS:
            continue
        counts[phrase] = counts.get(phrase, 0) + 1
        spans.setdefault(phrase, []).append((i, i + n - 1))

    ranked = sorted(counts.items(), key=lambda x: (-x[1], -len(x[0])))
    results = []
    for phrase, cnt in ranked[:12]:
        if cnt < 2:
            continue
        occ = spans[phrase]
        pick = occ[1] if len(occ) > 1 else occ[0]
        results.append((pick[0], pick[1], cnt, phrase))
    return results


def _hook_candidates(filtered: list[dict], duration_ms: int) -> list[ClipWindow]:
    out: list[ClipWindow] = []
    for i0, i1, cnt, phrase in _ngram_windows(filtered, duration_ms, n=4):
        start_ms, end_ms = _window_from_words(filtered, i0, i1, duration_ms)
        # extend around hook
        start_ms, end_ms = _expand_window(start_ms, end_ms, filtered, duration_ms)
        out.append(ClipWindow(
            start_ms=start_ms, end_ms=end_ms,
            source="repeat_hook", label=f"Hook ({cnt}×)",
            slot="chorus", score=60.0 + cnt * 5,
        ))
    return out


def _read_vocals_rms(vocals_path: Path, hop_s: float = 0.5) -> tuple[np.ndarray, float]:
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(vocals_path),
            "-ac", "1", "-ar", "22050",
            "-f", "f32le", "-acodec", "pcm_f32le", "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    samples = np.frombuffer(proc.stdout, dtype=np.float32)
    if samples.size == 0:
        return np.zeros(1), 22050.0
    sr = 22050.0
    hop = max(1, int(hop_s * sr))
    rms = []
    for i in range(0, len(samples), hop):
        chunk = samples[i:i + hop]
        rms.append(float(np.sqrt(np.mean(chunk ** 2))))
    return np.array(rms, dtype=np.float32), sr / hop


def _energy_candidates(
    vocals_path: Path | None,
    duration_ms: int,
    *,
    slot: str,
    label: str,
    score: float,
) -> list[ClipWindow]:
    if not vocals_path or not vocals_path.exists():
        return []
    rms, rms_hz = _read_vocals_rms(vocals_path)
    if rms.size < 3:
        return []
    kernel = np.ones(5) / 5
    smooth = np.convolve(rms, kernel, mode="same")
    target_ms = int(SHORTS_TARGET_S * 1000)
    half_win = int((target_ms / 1000) * rms_hz / 2)

    peaks = []
    for center in range(half_win, len(smooth) - half_win):
        window = smooth[center - half_win:center + half_win]
        peaks.append((float(window.mean()), center))
    peaks.sort(reverse=True)

    out: list[ClipWindow] = []
    for peak_val, center in peaks[:20]:
        t_s = center / rms_hz
        start_ms = int(max(0, (t_s - SHORTS_TARGET_S / 2) * 1000))
        end_ms = int(min(duration_ms, start_ms + target_ms))
        start_ms, end_ms = _expand_window(start_ms, end_ms, [], duration_ms)
        out.append(ClipWindow(
            start_ms=start_ms, end_ms=end_ms,
            source="energy_peak", label=label, slot=slot, score=score + peak_val * 10,
        ))
    return out


def _opening_candidate(filtered: list[dict], duration_ms: int) -> ClipWindow | None:
    """First dense lyric moment after intro."""
    if len(filtered) < 8:
        return None
    # skip long oh/yeah runs at start
    i = 0
    while i < len(filtered) and filtered[i]["start_ms"] < 20_000:
        if _gap_ms(filtered, i) > 2000:
            break
        i += 1
    i0 = min(i, len(filtered) - 1)
    i1 = min(len(filtered) - 1, i0 + 40)
    start_ms, end_ms = _window_from_words(filtered, i0, i1, duration_ms)
    return ClipWindow(
        start_ms=start_ms, end_ms=end_ms,
        source="opening_moment", label="Opening", slot="improv_a", score=45.0,
    )


def _bridge_candidates(words: list[dict], duration_ms: int) -> list[ClipWindow]:
    out: list[ClipWindow] = []
    for label, s, e in _section_blocks(words):
        if label != "bridge":
            continue
        start_ms, end_ms = _expand_window(s, e, words, duration_ms)
        out.append(ClipWindow(
            start_ms=start_ms, end_ms=end_ms,
            source="bridge_label", label="Bridge", slot="improv_b", score=70.0,
        ))
    return out


def _late_song_candidate(filtered: list[dict], duration_ms: int) -> ClipWindow | None:
    """Last third — emotional closer."""
    cutoff = int(duration_ms * 0.62)
    tail = [w for w in filtered if w["start_ms"] >= cutoff]
    if len(tail) < 6:
        return None
    i0, i1 = 0, min(len(tail) - 1, 35)
    start_ms, end_ms = _window_from_words(tail, i0, i1, duration_ms)
    return ClipWindow(
        start_ms=start_ms, end_ms=end_ms,
        source="late_moment", label="Outro moment", slot="improv_b", score=40.0,
    )


def _overlaps(a: ClipWindow, b: ClipWindow) -> bool:
    overlap_ms = min(a.end_ms, b.end_ms) - max(a.start_ms, b.start_ms)
    if overlap_ms <= 0:
        sep = max(a.start_ms, b.start_ms) - min(a.end_ms, b.end_ms)
        return sep < MIN_CLIP_GAP_MS
    return overlap_ms >= MIN_OVERLAP_MS


def _pick_best(candidates: list[ClipWindow], taken: list[ClipWindow]) -> ClipWindow | None:
    for c in sorted(candidates, key=lambda x: -x.score):
        if c.duration_s < SHORTS_MIN_S * 0.85:
            continue
        if any(_overlaps(c, t) for t in taken):
            continue
        return c
    return None


def pick_three_clips(
    raw_words: list[dict],
    filtered_words: list[dict],
    duration_s: float,
    vocals_path: Path | None = None,
) -> list[ClipWindow]:
    """
    Return exactly 3 clip windows: 1 chorus + 2 improvised sections.
    """
    duration_ms = int(duration_s * 1000)
    taken: list[ClipWindow] = []

    chorus_pool = _chorus_candidates(raw_words, duration_ms)
    if not chorus_pool:
        chorus_pool = _hook_candidates(filtered_words, duration_ms)
    chorus = _pick_best(chorus_pool, taken)
    if chorus:
        chorus.slot = "chorus"
        taken.append(chorus)

    improv_a_pool: list[ClipWindow] = []
    improv_a_pool.extend(_energy_candidates(
        vocals_path, duration_ms, slot="improv_a", label="Peak moment", score=55.0,
    ))
    opening = _opening_candidate(filtered_words, duration_ms)
    if opening:
        improv_a_pool.append(opening)
    improv_a = _pick_best(improv_a_pool, taken)
    if improv_a:
        improv_a.slot = "improv_a"
        taken.append(improv_a)

    improv_b_pool: list[ClipWindow] = []
    improv_b_pool.extend(_bridge_candidates(raw_words, duration_ms))
    # 2nd chorus occurrence
    choruses = _chorus_candidates(raw_words, duration_ms)
    if len(choruses) > 1:
        improv_b_pool.append(choruses[1])
    improv_b_pool.extend(_hook_candidates(filtered_words, duration_ms))
    improv_b_pool.extend(_energy_candidates(
        vocals_path, duration_ms, slot="improv_b", label="Energy lift", score=50.0,
    ))
    late = _late_song_candidate(filtered_words, duration_ms)
    if late:
        improv_b_pool.append(late)
    improv_b = _pick_best(improv_b_pool, taken)
    if improv_b:
        improv_b.slot = "improv_b"
        taken.append(improv_b)

    required_slots = ["chorus", "improv_a", "improv_b"]
    fallbacks = [
        ("chorus", 0.38, "Highlight"),
        ("improv_a", 0.10, "Early vibe"),
        ("improv_b", 0.58, "Mid song"),
        ("improv_b", 0.78, "Late vibe"),
    ]

    def _slot_filled(slot: str) -> bool:
        return any(t.slot == slot for t in taken)

    for slot, frac, label in fallbacks:
        if all(_slot_filled(s) for s in required_slots):
            break
        if _slot_filled(slot):
            continue
        start_ms = int(duration_ms * frac)
        end_ms = min(duration_ms, start_ms + int(SHORTS_TARGET_S * 1000))
        start_ms, end_ms = _expand_window(start_ms, end_ms, filtered_words, duration_ms)
        fb = ClipWindow(
            start_ms=start_ms, end_ms=end_ms,
            source="fallback", label=label, slot=slot, score=10.0,
        )
        if fb.duration_s >= SHORTS_MIN_S * 0.85 and not any(_overlaps(fb, t) for t in taken):
            taken.append(fb)

    # One entry per slot, best score if duplicates
    by_slot: dict[str, ClipWindow] = {}
    for c in sorted(taken, key=lambda x: -x.score):
        if c.slot not in by_slot:
            by_slot[c.slot] = c

    result = [by_slot[s] for s in required_slots if s in by_slot]
    return result[:3]


def clips_to_debug_dict(clips: list[ClipWindow]) -> list[dict]:
    return [asdict(c) for c in clips]