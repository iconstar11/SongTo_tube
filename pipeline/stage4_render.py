import json
import subprocess
import shutil
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import config
from pipeline.color_overlay import apply_overlay, resolve_overlay

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

def _group_into_lines(alignment: list[dict]) -> list[dict]:
    lines = []
    current_words = []
    current_w = 0
    space_w = _measure_width(" ", config.FONT_SIZE)
    last_end = None

    for i, w in enumerate(alignment):
        word_text = w["word"].upper()
        word_w = _measure_width(word_text, config.FONT_SIZE)
        start = w["start_ms"]

        # Ensure we do NOT split the line right before this word if it starts at the same time
        # as the previous word (i.e. keep snapped/simultaneous words on the same screen/line)
        can_split = True
        if current_words and current_words[-1]["start_ms"] == start:
            can_split = False

        # Rule: Gap > 2s or Width exceeded
        gap_trigger = last_end is not None and start - last_end > 2000
        width_trigger = current_words and current_w + word_w + space_w > config.MAX_WIDTH_PX

        # Tempo Trigger: cap at 5 words if the words are fast-paced
        tempo_trigger = False
        if current_words and len(current_words) >= 5:
            avg_dur = sum(wd["end_ms"] - wd["start_ms"] for wd in current_words) / len(current_words)
            if avg_dur < config.FAST_WORD_DURATION_MS:
                tempo_trigger = True

        # Max Duration Trigger: split if line extends past max duration (6s)
        dur_trigger = False
        if current_words:
            line_duration = w["end_ms"] - current_words[0]["start_ms"]
            if line_duration > config.MAX_LINE_DURATION_MS:
                dur_trigger = True

        if can_split and (gap_trigger or width_trigger or tempo_trigger or dur_trigger):
            lines.append({"words": current_words})
            current_words = []
            current_w = 0

        current_words.append(w)
        current_w += word_w + space_w
        last_end = w["end_ms"]

    if current_words:
        lines.append({"words": current_words})
    
    for line in lines:
        line["start_sec"] = line["words"][0]["start_ms"] / 1000.0
        line["end_sec"] = line["words"][-1]["end_ms"] / 1000.0
    
    return lines

def _build_pages(lines: list[dict], intro_dur: float) -> list[dict]:
    pages = []
    i = 0
    page_split_gap_s = 2.0
    while i < len(lines):
        pair = [lines[i]]
        if i + 1 < len(lines):
            gap = lines[i + 1]["start_sec"] - lines[i]["end_sec"]
            if gap < page_split_gap_s:
                pair.append(lines[i + 1])
        
        pages.append({
            "lines": pair,
            "start_sec": pair[0]["start_sec"],
            "end_sec": pair[-1]["end_sec"]
        })
        i += len(pair)

    # Crossfade overlaps
    fade_s = config.FADE_MS / 1000.0
    lead_in_s = config.LEAD_IN_MS / 1000.0
    
    for pi in range(len(pages)):
        p = pages[pi]
        p["show_start"] = p["start_sec"] - lead_in_s
        p["show_end"] = p["end_sec"] + fade_s

    # Resolve page overlaps by shortening/truncating the previous page's end_sec (last-word shortening)
    for pi in range(len(pages)):
        if pi > 0:
            prev_page = pages[pi-1]
            curr_page = pages[pi]
            
            desired_start = curr_page["start_sec"] - lead_in_s
            desired_start = max(intro_dur, desired_start)
            
            if prev_page["show_end"] > desired_start:
                # Shorten prev_page["show_end"] to match the desired_start of current page
                new_end_sec = desired_start - fade_s
                
                # SAFETY CAP: Make sure we do not shorten past the last word's start time + 100ms
                last_word = prev_page["lines"][-1]["words"][-1]
                min_end_sec = last_word["start_ms"] / 1000.0 + 0.1
                
                new_end_sec = max(min_end_sec, new_end_sec)
                # Check that we don't shorten it past the start time of the previous page
                new_end_sec = max(prev_page["start_sec"], new_end_sec)
                
                prev_page["end_sec"] = new_end_sec
                prev_page["show_end"] = new_end_sec + fade_s
                
                # Update the corresponding word's end_ms in the alignment list
                last_word["end_ms"] = round(new_end_sec * 1000)
                if last_word["end_ms"] <= last_word["start_ms"]:
                    last_word["end_ms"] = last_word["start_ms"] + 1

    # Resolve remaining timing adjustments chronologically
    for pi in range(len(pages)):
        if pi > 0:
            limit_start = pages[pi-1]["show_end"]
        else:
            limit_start = intro_dur
            
        # FORCE absolute separation: current page's show_start must be >= previous page's show_end
        pages[pi]["show_start"] = max(limit_start, min(pages[pi]["start_sec"], pages[pi]["show_start"]))
        pages[pi]["show_start"] = max(intro_dur, pages[pi]["show_start"])
            
    return pages

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
        for li, line in enumerate(page["lines"]):
            y = config.CURRENT_LINE_Y + (li * config.NEXT_LINE_Y_DELTA)
            full_line_text = " ".join([w["word"].upper() for w in line["words"]])
            line_w = _measure_width(full_line_text, config.FONT_SIZE)
            start_x = (1920 - line_w) / 2
            curr_x = start_x
            for w in line["words"]:
                txt = w["word"].upper().replace("'", "").replace(":", "")
                
                if config.HIGHLIGHT_WORDS:
                    ts = max(ss, (w["start_ms"] - config.HIGHLIGHT_OFFSET_MS) / 1000.0)
                    te = w["end_ms"]/1000.0
                    
                    # Highlighted (Gold)
                    filters.append(
                        f"drawtext=fontfile='{font_path}':text='{txt}':fontsize={config.FONT_SIZE}:"
                        f"fontcolor={config.ACCENT_COLOR}:x={curr_x}:y={y}:shadowcolor={shadow}:shadowx=4:shadowy=4:"
                        f"enable='gte(t,{ts:.3f})*lt(t,{te:.3f})'"
                    )
                    # Normal (White) - only show when not highlighted
                    filters.append(
                        f"drawtext=fontfile='{font_path}':text='{txt}':fontsize={config.FONT_SIZE}:"
                        f"fontcolor={config.TEXT_COLOR}:x={curr_x}:y={y}:shadowcolor={shadow}:shadowx=4:shadowy=4:"
                        f"enable='gte(t,{ss:.3f})*lt(t,{se:.3f})*not(gte(t,{ts:.3f})*lt(t,{te:.3f}))'"
                    )
                else:
                    # Normal (White) - show during the entire page visibility
                    filters.append(
                        f"drawtext=fontfile='{font_path}':text='{txt}':fontsize={config.FONT_SIZE}:"
                        f"fontcolor={config.TEXT_COLOR}:x={curr_x}:y={y}:shadowcolor={shadow}:shadowx=4:shadowy=4:"
                        f"enable='gte(t,{ss:.3f})*lt(t,{se:.3f})'"
                    )
                curr_x += _measure_width(txt + " ", config.FONT_SIZE)
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
        
    output_path = config.OUTPUT_DIR / f"{title}_7clouds.mp4"
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

