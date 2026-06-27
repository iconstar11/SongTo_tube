import json
import subprocess
import shutil
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import config
from pipeline.color_overlay import apply_overlay, resolve_overlay
from pipeline.output_paths import to_post_video_path

# Add project root to path for package imports
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

_UNSET = object()

# ── Font helpers ──────────────────────────────────────────────────────────────
_pil_fonts = {}

def _measure_width(text: str, font_size: int) -> int:
    global _pil_fonts
    if font_size not in _pil_fonts:
        try:
            _pil_fonts[font_size] = ImageFont.truetype(str(config.FONT_FILE), font_size)
        except:
            _pil_fonts[font_size] = None
            
    f = _pil_fonts[font_size]
    if f is None:
        return int(len(text) * font_size * 0.5)
    bbox = f.getbbox(text)
    return bbox[2] - bbox[0]

def _get_scaled_font_size(text: str, base_size: int, max_width: int) -> int:
    size = base_size
    while size > 40 and _measure_width(text, size) > max_width:
        size -= 10
    return size


# ── Layout logic ──────────────────────────────────────────────────────────────

def _clean_and_align_timestamps(alignment: list[dict], snap_threshold_ms: int = 100) -> list[dict]:
    # Make a copy of alignment to avoid mutating the original input
    words = [dict(w) for w in alignment]
    n = len(words)
    if n == 0:
        return words

    # Sort alignment by start_ms, and then end_ms
    words.sort(key=lambda x: (x["start_ms"], x["end_ms"]))

    # Step 1: Clean basic issues (end_ms <= start_ms)
    # Set to at least start_ms + 1 ms to prevent zero duration
    for i in range(n):
        if words[i]["end_ms"] <= words[i]["start_ms"]:
            words[i]["end_ms"] = words[i]["start_ms"] + 1

    # Step 2: Snap close starts (within threshold) to be exactly identical
    i = 0
    while i < n:
        group = [i]
        j = i + 1
        while j < n and abs(words[j]["start_ms"] - words[i]["start_ms"]) <= snap_threshold_ms:
            group.append(j)
            j += 1
        
        if len(group) > 1:
            min_start = min(words[g]["start_ms"] for g in group)
            for g in group:
                words[g]["start_ms"] = min_start
                
            # If the end times in the group are also very close, snap them too
            min_end = min(words[g]["end_ms"] for g in group)
            max_end = max(words[g]["end_ms"] for g in group)
            if max_end - min_end <= snap_threshold_ms:
                for g in group:
                    words[g]["end_ms"] = max_end
        
        i = j

    # Step 3: Resolve sequential overlaps locally
    for i in range(1, n):
        prev = words[i-1]
        curr = words[i]
        
        if curr["start_ms"] > prev["start_ms"]:
            # They are sequential
            if prev["end_ms"] > curr["start_ms"]:
                # Trim prev end to curr start
                prev["end_ms"] = curr["start_ms"]
                if prev["end_ms"] <= prev["start_ms"]:
                    prev["end_ms"] = prev["start_ms"] + 1

    return words

def _gap_break_strength(gap_s: float) -> float:
    """Higher score = better place to end a line or page (natural singing pause)."""
    if gap_s >= config.GAP_STRONG_BREAK_S:
        return 1.0
    if gap_s >= config.GAP_MEDIUM_BREAK_S:
        return 0.6
    if gap_s >= config.GAP_TIGHT_S:
        return 0.25
    return 0.0


def _line_gap_sec(prev_line: dict, next_line: dict) -> float:
    return max(0.0, next_line["start_sec"] - prev_line["end_sec"])


def _word_gap_ms(prev_word: dict, next_word: dict) -> int:
    return max(0, next_word["start_ms"] - prev_word["end_ms"])


def _measure_words_width(words: list[dict], font_size: int) -> int:
    if not words:
        return 0
    space_w = _measure_width(" ", font_size)
    total = 0
    for i, w in enumerate(words):
        total += _measure_width(w["word"].upper(), font_size)
        if i < len(words) - 1:
            total += space_w
    return total


def _best_split_index(words: list[dict], *, min_words: int = 1) -> int:
    """Pick the split index with the strongest inter-word pause, else midpoint."""
    if len(words) <= min_words:
        return 0

    best_score = -1.0
    best_idx = len(words) // 2
    for idx in range(min_words, len(words)):
        gap_s = _word_gap_ms(words[idx - 1], words[idx]) / 1000.0
        score = _gap_break_strength(gap_s)
        if score > best_score:
            best_score = score
            best_idx = idx

    return best_idx if best_score > 0 else max(min_words, len(words) // 2)


def _finalize_line(words: list[dict]) -> dict:
    return {
        "words": words,
        "start_sec": words[0]["start_ms"] / 1000.0,
        "end_sec": words[-1]["end_ms"] / 1000.0,
    }


def _group_into_lines(alignment: list[dict]) -> list[dict]:
    lines: list[dict] = []
    current_words: list[dict] = []
    current_w = 0
    space_w = _measure_width(" ", config.FONT_SIZE)
    last_end = None
    strong_gap_ms = int(config.GAP_STRONG_BREAK_S * 1000)

    for w in alignment:
        word_text = w["word"].upper()
        word_w = _measure_width(word_text, config.FONT_SIZE)
        start = w["start_ms"]

        can_split = not (current_words and current_words[-1]["start_ms"] == start)

        gap_trigger = last_end is not None and start - last_end >= strong_gap_ms
        width_trigger = bool(current_words and current_w + word_w + space_w > config.MAX_WIDTH_PX)

        tempo_trigger = False
        if current_words and len(current_words) >= 5:
            avg_dur = sum(wd["end_ms"] - wd["start_ms"] for wd in current_words) / len(current_words)
            tempo_trigger = avg_dur < config.FAST_WORD_DURATION_MS

        dur_trigger = False
        if current_words:
            line_duration = w["end_ms"] - current_words[0]["start_ms"]
            dur_trigger = line_duration > config.MAX_LINE_DURATION_MS

        if can_split and current_words and (gap_trigger or width_trigger or tempo_trigger or dur_trigger):
            if gap_trigger:
                lines.append(_finalize_line(current_words))
                current_words = []
                current_w = 0
            elif len(current_words) > 1:
                split_at = _best_split_index(current_words)
                lines.append(_finalize_line(current_words[:split_at]))
                current_words = current_words[split_at:]
                current_w = _measure_words_width(current_words, config.FONT_SIZE)
            else:
                lines.append(_finalize_line(current_words))
                current_words = []
                current_w = 0

        current_words.append(w)
        current_w += word_w + space_w
        last_end = w["end_ms"]

    if current_words:
        lines.append(_finalize_line(current_words))

    return lines


def _pack_pages(lines: list[dict]) -> list[dict]:
    """Group 1–3 lines per page using timestamp-gap scoring."""
    if not lines:
        return []

    pages: list[dict] = []
    i = 0
    while i < len(lines):
        page_lines = [lines[i]]
        j = i + 1

        while j < len(lines) and len(page_lines) < config.PAGE_MAX_LINES:
            gap = _line_gap_sec(page_lines[-1], lines[j])
            strength = _gap_break_strength(gap)

            if strength >= 0.8:
                break

            page_start = page_lines[0]["start_sec"]
            page_end = lines[j]["end_sec"]
            if page_end - page_start > config.PAGE_TARGET_MAX_S and len(page_lines) >= 1:
                break

            if strength >= 0.5 and len(page_lines) >= 2:
                break

            page_lines.append(lines[j])
            j += 1

        pages.append({
            "lines": page_lines,
            "content_start": page_lines[0]["start_sec"],
            "content_end": page_lines[-1]["end_sec"],
        })
        i = j

    return pages


def _lead_anim_duration(lead_window: float, animate: bool) -> float:
    if not animate or lead_window < config.PAGE_LEAD_ANIM_MIN_S:
        return 0.0
    target = config.PAGE_LEAD_ANIM_MAX_S * config.PAGE_LEAD_ANIM_RATIO
    return min(target, lead_window)


def _fade_anim_duration(hold_window: float, animate: bool) -> float:
    if not animate or hold_window < config.PAGE_FADE_ANIM_MIN_S:
        return 0.0
    target = config.PAGE_FADE_ANIM_MAX_S * config.PAGE_FADE_ANIM_RATIO
    return min(target, hold_window)


def _min_lead_before_first_word(page: dict) -> float:
    ms = config.PAGE_MIN_LEAD_MS if page.get("animate") else config.PAGE_MIN_LEAD_TIGHT_MS
    return ms / 1000.0


def _apply_page_anim_flags(page: dict) -> None:
    lead_window = max(0.0, page["content_start"] - page["show_start"])
    hold_window = max(0.0, page["show_end"] - page["content_end"])

    page["lead_anim_dur"] = _lead_anim_duration(lead_window, page["animate"])
    if page["lead_anim_dur"] > 0:
        # Finish fade-in by the first sung word so sing-along text is fully readable.
        page["lead_anim_dur"] = min(page["lead_anim_dur"], lead_window)

    page["fade_out_dur"] = _fade_anim_duration(hold_window, page["animate"])
    page["animate_lead_in"] = page["lead_anim_dur"] >= 0.25
    page["animate_fade_out"] = page["fade_out_dur"] >= 0.25


def _incoming_gap_s(pages: list[dict], pi: int) -> float:
    if pi == 0:
        return config.PAGE_HOLD_MAX_S + config.LEAD_IN_MS / 1000.0
    return max(0.0, pages[pi]["content_start"] - pages[pi - 1]["content_end"])


def _trim_prev_for_tight_next(pages: list[dict], pi: int, intro_dur: float) -> None:
    """End the previous page early so a tight next page can show before its first word."""
    if pi == 0:
        return

    page = pages[pi]
    prev = pages[pi - 1]
    if not page.get("tight_incoming"):
        return

    min_lead = config.PAGE_MIN_LEAD_TIGHT_MS / 1000.0
    deadline = page["content_start"] - min_lead

    prev["show_end"] = min(prev["show_end"], deadline)
    prev["show_end"] = max(prev["show_end"], prev["content_start"])
    prev["hold_after"] = max(0.0, prev["show_end"] - prev["content_end"])
    prev["fade_out_dur"] = 0.0
    prev["animate_fade_out"] = False

    page["show_start"] = max(prev["show_end"], page["_ideal_start"], intro_dur)
    page["show_start"] = min(page["show_start"], page["content_start"])
    if "show_end" in page:
        page["show_end"] = max(page["show_end"], page["show_start"] + 0.05)
        _apply_page_anim_flags(page)
    _apply_page_anim_flags(prev)


def _enforce_first_word_lead(pages: list[dict], intro_dur: float) -> None:
    """Tight pages: never appear after the first word; always keep a small read-ahead."""
    for pi, page in enumerate(pages):
        if not page.get("tight_incoming"):
            continue

        cs = page["content_start"]
        min_lead = config.PAGE_MIN_LEAD_TIGHT_MS / 1000.0
        latest_show_start = cs - min_lead

        if page["show_start"] <= latest_show_start:
            continue

        _trim_prev_for_tight_next(pages, pi, intro_dur)
        page["show_start"] = min(page["show_start"], latest_show_start, cs)
        if pi > 0:
            page["show_start"] = max(page["show_start"], pages[pi - 1]["show_end"])
        else:
            page["show_start"] = max(page["show_start"], intro_dur)

        page["show_end"] = max(page["show_end"], page["show_start"] + 0.05)
        _apply_page_anim_flags(page)


def _compute_page_timing(pages: list[dict], intro_dur: float) -> None:
    """Assign show_start/show_end and animation flags without mutating word timestamps."""
    lead_max = config.LEAD_IN_MS / 1000.0
    tight_lead = config.PAGE_MIN_LEAD_TIGHT_MS / 1000.0

    for pi, page in enumerate(pages):
        cs = page["content_start"]
        ce = page["content_end"]
        incoming_gap = _incoming_gap_s(pages, pi)

        if pi + 1 < len(pages):
            transition_gap = max(0.0, pages[pi + 1]["content_start"] - ce)
        else:
            transition_gap = config.PAGE_HOLD_MAX_S + lead_max

        tight_incoming = incoming_gap < config.ANIM_MIN_GAP_S
        tight_outgoing = transition_gap < config.ANIM_MIN_GAP_S
        page["tight_incoming"] = tight_incoming
        page["tight_outgoing"] = tight_outgoing
        page["transition_gap"] = transition_gap

        if tight_outgoing:
            hold = 0.0
            fade_out = 0.0
            animate = False
            eff_lead = tight_lead if tight_incoming else 0.0
        elif transition_gap < config.GAP_MEDIUM_BREAK_S:
            eff_lead = min(lead_max, transition_gap * 0.25)
            hold = min(transition_gap * 0.3, config.PAGE_HOLD_MAX_S * 0.5)
            fade_out = 0.0
            animate = False
        else:
            eff_lead = min(lead_max, transition_gap * 0.65)
            hold = min(transition_gap * 0.45, config.PAGE_HOLD_MAX_S)
            animate = True
            fade_out = _fade_anim_duration(hold, animate)

        if tight_incoming:
            eff_lead = max(eff_lead, tight_lead)
            animate = False

        page["effective_lead_in"] = eff_lead
        page["hold_after"] = hold
        page["animate"] = animate
        page["fade_out_dur"] = fade_out
        page["_ideal_start"] = cs - eff_lead
        page["_show_end"] = ce + hold + fade_out

    for pi, page in enumerate(pages):
        if pi == 0:
            page["show_start"] = max(page["_ideal_start"], intro_dur)
        else:
            prev = pages[pi - 1]
            page["show_start"] = max(prev["show_end"], page["_ideal_start"], intro_dur)
            if prev["show_end"] > page["show_start"]:
                prev["show_end"] = page["show_start"]
                prev["fade_out_dur"] = 0.0
                prev["animate_fade_out"] = False

        page["show_start"] = min(page["show_start"], page["content_start"])
        page["show_end"] = max(page["_show_end"], page["show_start"] + 0.05)
        _apply_page_anim_flags(page)

        page["start_sec"] = page["content_start"]
        page["end_sec"] = page["content_end"]

    for pi in range(1, len(pages)):
        if pages[pi].get("tight_incoming"):
            _trim_prev_for_tight_next(pages, pi, intro_dur)

    _enforce_first_word_lead(pages, intro_dur)


def _build_pages(lines: list[dict], intro_dur: float) -> list[dict]:
    pages = _pack_pages(lines)
    _compute_page_timing(pages, intro_dur)
    return pages


def _line_y_positions(line_count: int) -> list[float]:
    spacing = config.LINE_SPACING
    center = config.LYRIC_CENTER_Y
    block = (line_count - 1) * spacing
    start_y = center - block / 2
    return [start_y + i * spacing for i in range(line_count)]


def _page_alpha_expr(page: dict) -> str | None:
    ss = page["show_start"]
    se = page["show_end"]

    if not page.get("animate_lead_in") and not page.get("animate_fade_out"):
        return None

    if page.get("animate_lead_in") and page["lead_anim_dur"] > 0:
        ad = page["lead_anim_dur"]
        cs = page["content_start"]
        t1 = ss + ad
        expr = (
            f"if(lt(t,{ss:.3f}),0,"
            f"if(lt(t,{cs:.3f}),if(lt(t,{t1:.3f}),(t-{ss:.3f})/{ad:.3f},1),1))"
        )
    else:
        expr = f"if(between(t,{ss:.3f},{se:.3f}),1,0)"

    if page.get("animate_fade_out") and page["fade_out_dur"] > 0:
        fd = page["fade_out_dur"]
        fs = se - fd
        expr = f"if(gt(t,{fs:.3f}),max(0,({se:.3f}-t)/{fd:.3f}),{expr})"

    return expr


def _page_y_expr(base_y: float, page: dict) -> str:
    if not page.get("animate_lead_in") or page["lead_anim_dur"] <= 0:
        return f"{base_y:.1f}"

    ss = page["show_start"]
    cs = page["content_start"]
    ad = page["lead_anim_dur"]
    slide = config.PAGE_LEAD_SLIDE_PX
    return (
        f"{base_y:.1f}+if(lt(t,{cs:.3f}),"
        f"max(0,{slide}*(1-min(1,max(0,(t-{ss:.3f})/{ad:.3f})))),0)"
    )

def _crop_and_resize(img: Image.Image, target_w: int = 1920, target_h: int = 1080) -> Image.Image:
    target_ratio = target_w / target_h
    w, h = img.size
    img_ratio = w / h
    
    if img_ratio > target_ratio:
        # Image is wider: crop sides
        new_w = int(target_ratio * h)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    elif img_ratio < target_ratio:
        # Image is taller: crop top/bottom
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
        
    return img.resize((target_w, target_h), Image.Resampling.LANCZOS)


def _apply_color_overlay(img: Image.Image, overlay_key: str | None) -> Image.Image:
    preset = resolve_overlay(overlay_key)
    if not preset:
        return img
    print(f"Applying mood overlay: {overlay_key} ({preset['label']}, {preset['color']})")
    return apply_overlay(img, overlay_key)


def _generate_base_background(output_path: Path, downloaded_bg_path: Path = None, overlay_key=_UNSET):
    img = None

    if downloaded_bg_path and downloaded_bg_path.exists():
        try:
            img = Image.open(downloaded_bg_path)
            img = _crop_and_resize(img)
        except Exception as e:
            print(f"Error loading downloaded background {downloaded_bg_path}, falling back: {e}")

    if img is None:
        bg_dir = config.BG_DIR
        bg_files = []
        if bg_dir.exists():
            bg_files = list(bg_dir.glob("*.jpg")) + list(bg_dir.glob("*.jpeg")) + list(bg_dir.glob("*.png"))

        if bg_files:
            try:
                img = Image.open(bg_files[0])
                img = _crop_and_resize(img)
            except Exception as e:
                print(f"Error loading background image, falling back to solid color: {e}")

    if img is None:
        img = Image.new('RGB', (1920, 1080), color=(15, 15, 15))

    if overlay_key is _UNSET:
        effective_overlay = config.COLOR_OVERLAY or None
    else:
        effective_overlay = overlay_key
    img = _apply_color_overlay(img, effective_overlay)
    img.convert("RGB").save(output_path)

def _wrap_text(text: str, max_words: int = 4) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines = []
    curr = []
    for w in words:
        curr.append(w)
        if len(curr) >= max_words:
            lines.append(" ".join(curr))
            curr = []
    if curr:
        lines.append(" ".join(curr))
    return lines


def _build_filters(title: str, artist: str, pages: list[dict], intro_dur: float) -> str:
    font_path = str(config.FONT_FILE).replace("\\", "/").replace(":", "\\:")
    shadow = f"{config.SHADOW_COLOR}@0.75"
    filters = []

    if intro_dur > 0.0:
        # Clean text to prevent FFmpeg filter syntax issues
        clean_title = title.upper().replace("'", "").replace(":", "")
        clean_artist = artist.upper().replace("'", "").replace(":", "")

        # Wrap title/artist if they are long or wide
        if len(clean_title.split()) > 5 or _measure_width(clean_title, 140) > 1750:
            title_lines = _wrap_text(clean_title, max_words=4)
        else:
            title_lines = [clean_title]

        if len(clean_artist.split()) > 5 or _measure_width(clean_artist, 80) > 1750:
            artist_lines = _wrap_text(clean_artist, max_words=4)
        else:
            artist_lines = [clean_artist]

        # Dynamic font scaling to ensure each line fits the screen (max width 1750px)
        title_font_size = 140
        for line in title_lines:
            title_font_size = min(title_font_size, _get_scaled_font_size(line, 140, 1750))
            
        artist_font_size = 80
        for line in artist_lines:
            artist_font_size = min(artist_font_size, _get_scaled_font_size(line, 80, 1750))

        # Calculate total height of the title card block
        title_h = len(title_lines) * title_font_size + (len(title_lines) - 1) * 20 if title_lines else 0
        artist_h = len(artist_lines) * artist_font_size + (len(artist_lines) - 1) * 15 if artist_lines else 0
        gap = 40 if (title_h and artist_h) else 0
        total_h = title_h + gap + artist_h
        
        start_y = (1080 - total_h) / 2

        # Calculate dynamic fade duration (e.g. 1.0s or half the intro duration if very short)
        fade_dur = min(1.0, intro_dur / 2.0)
        alpha_expr = (
            f"if(lt(t,{fade_dur:.2f}), t/{fade_dur:.2f}, "
            f"if(gt(t,{intro_dur:.2f}-{fade_dur:.2f}), ({intro_dur:.2f}-t)/{fade_dur:.2f}, 1.0))"
        )

        # Draw Title lines (animated fade-in / fade-out, centered)
        for i, line in enumerate(title_lines):
            y = start_y + i * (title_font_size + 20)
            filters.append(
                f"drawtext=fontfile='{font_path}':text='{line}':fontsize={title_font_size}:"
                f"fontcolor=white:x=(w-text_w)/2:y={y:.1f}:shadowcolor={shadow}:shadowx=4:shadowy=4:"
                f"alpha='{alpha_expr}':enable='lt(t,{intro_dur})'"
            )

        # Draw Artist lines (animated fade-in / fade-out, centered)
        for j, line in enumerate(artist_lines):
            y = start_y + title_h + gap + j * (artist_font_size + 15)
            filters.append(
                f"drawtext=fontfile='{font_path}':text='{line}':fontsize={artist_font_size}:"
                f"fontcolor={config.ACCENT_COLOR}:x=(w-text_w)/2:y={y:.1f}:shadowcolor={shadow}:shadowx=4:shadowy=4:"
                f"alpha='{alpha_expr}':enable='lt(t,{intro_dur})'"
            )

    for page in pages:
        ss, se = page["show_start"], page["show_end"]
        alpha_expr = _page_alpha_expr(page)
        y_positions = _line_y_positions(len(page["lines"]))

        for li, line in enumerate(page["lines"]):
            base_y = y_positions[li]
            y_expr = _page_y_expr(base_y, page)
            full_line_text = " ".join([w["word"].upper() for w in line["words"]])
            font_size = _get_scaled_font_size(full_line_text, config.FONT_SIZE, config.MAX_WIDTH_PX)
            line_w = _measure_width(full_line_text, font_size)
            start_x = (1920 - line_w) / 2
            curr_x = start_x

            for w in line["words"]:
                txt = w["word"].upper().replace("'", "").replace(":", "")

                base = (
                    f"drawtext=fontfile='{font_path}':text='{txt}':fontsize={font_size}:"
                    f"x={curr_x}:y='{y_expr}':shadowcolor={shadow}:shadowx=4:shadowy=4"
                )
                alpha_suffix = f":alpha='{alpha_expr}'" if alpha_expr else ""

                if config.HIGHLIGHT_WORDS:
                    ts = max(ss, (w["start_ms"] - config.HIGHLIGHT_OFFSET_MS) / 1000.0)
                    te = w["end_ms"] / 1000.0

                    filters.append(
                        f"{base}:fontcolor={config.ACCENT_COLOR}:"
                        f"enable='gte(t,{ts:.3f})*lt(t,{te:.3f})'{alpha_suffix}"
                    )
                    filters.append(
                        f"{base}:fontcolor={config.TEXT_COLOR}:"
                        f"enable='gte(t,{ss:.3f})*lt(t,{se:.3f})*not(gte(t,{ts:.3f})*lt(t,{te:.3f}))'"
                        f"{alpha_suffix}"
                    )
                else:
                    filters.append(
                        f"{base}:fontcolor={config.TEXT_COLOR}:"
                        f"enable='gte(t,{ss:.3f})*lt(t,{se:.3f})'{alpha_suffix}"
                    )

                curr_x += _measure_width(txt + " ", font_size)
    return ",".join(filters)

def run(info: dict, alignment: list) -> Path:
    title, artist = info['title'], info['artist']
    background_img = config.TEMP_DIR / "base_background.png"
    downloaded_bg = Path(info['background_path']) if info.get('background_path') else None
    overlay_arg = info["color_overlay"] if "color_overlay" in info else _UNSET
    _generate_base_background(background_img, downloaded_bg, overlay_key=overlay_arg)
    
    # Clean up featured artists for the Title Card representation using regex
    import re
    feat_pattern = r'\s+[\(\[]?(?:feat\.?|ft\.?|featuring)\s+([^\]\)]+)[\]\)]?'
    parts = re.split(feat_pattern, title, flags=re.IGNORECASE)
    display_title = parts[0].strip()
    feat_artist = parts[1].strip() if len(parts) > 1 else None
    
    display_artist = artist
    if feat_artist and feat_artist.lower() not in display_artist.lower():
        display_artist = f"{display_artist} ft. {feat_artist}"
    
    # Preprocess alignment to snap close starts and remove sequential overlaps
    alignment = _clean_and_align_timestamps(alignment)
    
    # Calculate when the first word is sung
    first_word_orig_s = alignment[0]["start_ms"] / 1000.0 if alignment else 10.0
    
    # Dual timeline logic (Early vs Late vocals)
    crossover_threshold = 7.25
    if first_word_orig_s < crossover_threshold:
        # Case 1: Early Vocals
        intro_dur = 4.0
        lead_in_s = 1.5
        target_first_word_s = intro_dur + lead_in_s # 5.5s
        
        pad_delay_s = max(0.0, target_first_word_s - first_word_orig_s)
        trim_offset_s = max(0.0, first_word_orig_s - target_first_word_s)
    else:
        # Case 2: Late Vocals
        intro_dur = 5.0
        clean_gap = 2.5
        lead_in_s = 1.5
        target_first_word_s = intro_dur + clean_gap + lead_in_s # 9.0s
        
        pad_delay_s = max(0.0, target_first_word_s - first_word_orig_s)
        trim_offset_s = max(0.0, first_word_orig_s - target_first_word_s)

    # Shift alignment timestamps based on pad/trim actions
    shift_ms = int(pad_delay_s * 1000) - int(trim_offset_s * 1000)
    shifted_alignment = []
    for w in alignment:
        shifted_alignment.append({
            "word": w["word"],
            "start_ms": w["start_ms"] + shift_ms,
            "end_ms": w["end_ms"] + shift_ms,
        })
    alignment = shifted_alignment
        
    lines = _group_into_lines(alignment)
    pages = _build_pages(lines, intro_dur)
    filter_chain = _build_filters(display_title, display_artist, pages, intro_dur)
    
    # Compile visualizer overlay if enabled
    visualizer_overlay = None
    if config.ENABLE_VISUALIZER:
        try:
            from pipeline import audio_visualizer
            import numpy as np
            
            print("Visualizer: extracting audio samples...")
            samples, sr = audio_visualizer.extract_audio_samples(str(info['audio_path']))
            
            # Apply sync logic to samples in Python
            if trim_offset_s > 0.0:
                samples = samples[int(trim_offset_s * sr):]
            if pad_delay_s > 0.0:
                samples = np.concatenate([np.zeros(int(pad_delay_s * sr), dtype=np.float32), samples])
                
            fps = 25.0
            video_duration = info['duration'] - trim_offset_s + pad_delay_s
            n_frames = int(round(video_duration * fps))
            n_bands = 16 # 32 bars // 2 symmetric bands
            
            print("Visualizer: computing band levels...")
            levels = audio_visualizer.compute_band_levels(samples, sr, fps, n_frames, n_bands)
            
            visualizer_overlay = config.TEMP_DIR / f"{title}_viz_overlay.mp4"
            print(f"Visualizer: rendering overlay video to {visualizer_overlay}...")
            audio_visualizer.render_overlay(
                levels, str(visualizer_overlay), fps,
                width=672, height=140, n_bars=32,
                symmetric=True, gap_ratio=0.6,
                draw_origin_line=True
            )
        except Exception as ex:
            print(f"Error compiling visualizer: {ex}")
            visualizer_overlay = None
            
    filter_file = config.TEMP_DIR / "filters.txt"
    
    # Prepend silence to delayed audio if padding is active
    if pad_delay_s > 0.0:
        delay_ms = int(pad_delay_s * 1000)
        audio_filter = f";[1:a]adelay={delay_ms}|{delay_ms}[outa]"
        map_audio = "[outa]"
    else:
        audio_filter = ""
        map_audio = "1:a"
        
    if visualizer_overlay:
        vis_overlay_filter = (
            "[2:v]colorkey=0x000000:0.15:0.05[keyed_vis]; "
            "[0:v][keyed_vis]overlay=x=1200:y=48:shortest=1[bg_vis]; "
        )
        filter_script_content = f"{vis_overlay_filter}[bg_vis]{filter_chain}[outv]{audio_filter}"
    else:
        filter_script_content = f"[0:v]{filter_chain}[outv]{audio_filter}"
        
    filter_file.write_text(filter_script_content, encoding="utf-8")
        
    output_path = to_post_video_path(title)
    video_duration = info['duration'] - trim_offset_s + pad_delay_s
    
    # Dynamically build FFmpeg command to support input seeking (-ss) for trimming
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-t", f"{video_duration:.3f}", "-i", str(background_img),
    ]
    if trim_offset_s > 0.0:
        ffmpeg_cmd.extend(["-ss", f"{trim_offset_s:.3f}"])
    ffmpeg_cmd.extend(["-i", str(info['audio_path'])])
    
    if visualizer_overlay:
        ffmpeg_cmd.extend(["-i", str(visualizer_overlay)])
        
    ffmpeg_cmd.extend([
        "-filter_complex_script", str(filter_file),
        "-map", "[outv]", "-map", map_audio,
        "-c:v", "libx264", "-preset", "medium", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", "-shortest",
        str(output_path)
    ])
    
    try:
        subprocess.run(ffmpeg_cmd, check=True)
    finally:
        if visualizer_overlay and visualizer_overlay.exists():
            try:
                visualizer_overlay.unlink()
            except Exception:
                pass
                
    return output_path

