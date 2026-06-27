# YouTube Shorts — Implementation Blueprint

> **Goal:** Produce vertical lyric clips (9:16, 15–55 s) from the existing SongToTube pipeline, usually from the chorus or another high-impact section. Full-length videos remain the default; Shorts is a parallel output mode.

---

## 1. Summary

| Property | Full video (today) | Shorts (new) |
|----------|-------------------|--------------|
| Aspect ratio | 16:9 | **9:16** |
| Resolution | 1920×1080 | **1080×1920** |
| Duration | Full song | **15–55 s** (target ~30–45 s) |
| Segment | Entire alignment | **Chorus / user hint / auto-pick** |
| Background API | `landscape` / `horizontal` | **`portrait` / `vertical`** |
| Intro card | 4–9 s title card | **1–2 s** (or skip) |
| Output suffix | `_7clouds.mp4` | `_shorts.mp4` |
| Whisper | Full song | **Still full song** (slice after) |

**Key design choice:** Transcribe the **full song once**, then slice the word-alignment JSON to the chosen window. Re-transcribing a clip loses context and repeats API cost.

---

## 2. User Flow (Telegram)

### 2.1 New step after URL paste

After the user submits a YouTube URL, add a **format choice** before lyrics:

```
What do you want?
  [ Full lyric video ]   [ YouTube Short ]
```

- **Full** → existing flow unchanged.
- **Short** → same lyrics/bg/overlay flow, but all previews and renders use 9:16.

### 2.2 Lyrics input — chorus hints

When the user picks **I have lyrics**, show updated guidance:

```
Paste lyrics. Section labels help us find the chorus:
  [Chorus]
  line one
  line two

You can also mark a specific section:
  [Short: Chorus]
  [Short: Verse 2]
  [Short: Bridge]

Audio still wins on timing — labels are hints only.
```

**Parsing rules (new, in `pipeline/lyrics_sections.py`):**

| User writes | Meaning |
|-------------|---------|
| `[Chorus]` | Store as chorus section; used if no explicit Short tag |
| `[Short: Chorus]` or `[Short: chorus]` | **Explicit** segment target (highest priority) |
| `[Short: Verse 2]` | Target that numbered section |
| `[Short: 0:45-1:30]` | Optional manual time range (advanced) |
| `[Verse 1]`, `[Bridge]` | Stored for matching; not auto-selected unless repeated |

Section lines are **stripped from Whisper spelling hints** (current behavior) but **preserved in a sidecar JSON** next to the lyrics file.

### 2.3 Optional segment preview (phase 2)

After transcription, before render queue:

```
Short segment: Chorus (0:42 – 1:18, 36s)
  [ Use this ]  [ Pick another ]  [ Auto-pick best ]
```

Phase 1 can skip this and auto-select silently.

---

## 3. Segment Selection Logic

**New module:** `pipeline/segment_picker.py`

### 3.1 Priority order

1. **Explicit user tag** — `[Short: …]` or `[Short: M:SS-M:SS]`
2. **Chorus from lyrics** — first `[Chorus]` block matched against alignment
3. **Repeated phrase detection** — longest repeated 4–8 word n-gram in alignment
4. **Energy peak** — highest RMS window on vocals stem (from Demucs output)
5. **Fallback** — middle third of song, trimmed to 30 s

### 3.2 Duration constraints

```python
SHORTS_MIN_S = 15.0
SHORTS_MAX_S = 55.0
SHORTS_TARGET_S = 35.0   # prefer ~30–45 s
SHORTS_PAD_S = 0.5       # breathe before/after matched lyrics
```

After picking anchor lyrics, expand outward to natural gap breaks (`GAP_STRONG_BREAK_S` from config) until duration is in range.

### 3.3 Matching lyrics sections → timestamps

```python
@dataclass
class LyricSection:
    label: str          # "chorus", "verse", "bridge", …
    index: int | None   # 1 for "Verse 1"
    lines: list[str]    # cleaned lyric lines (no brackets)
    is_short_target: bool

@dataclass
class ClipWindow:
    start_ms: int
    end_ms: int
    duration_s: float
    source: str         # "user_tag", "chorus_match", "repeat_detect", "energy", "fallback"
    label: str          # human-readable, e.g. "Chorus"
```

**Matching algorithm:**

1. Tokenize section lines → word sequence (lowercase, strip punctuation).
2. Tokenize alignment → word sequence.
3. Find best sliding-window match (Ratcliff/Obershelp or simple token equality with fuzzy apostrophe handling).
4. `start_ms` = first matched word `start_ms` − `SHORTS_PAD_S`.
5. `end_ms` = last matched word `end_ms` + `SHORTS_PAD_S`.
6. If too short → extend to next strong gap up to `SHORTS_MAX_S`.
7. If too long → shrink from ends at strong gaps down to `SHORTS_MAX_S`.

### 3.4 Repeated-phrase detection (no lyrics)

```python
def find_repeated_hook(words: list[dict], min_words: int = 4, max_words: int = 8) -> ClipWindow | None:
    """
    Scan n-grams; score by (repeat_count * length).
    Pick the occurrence with strongest surrounding gaps (clean chorus boundaries).
    """
```

Choruses repeat 2–4×; pick the **second or third** occurrence (often better produced than the first).

### 3.5 Energy-based fallback

```python
def find_energy_peak(vocals_path: Path, target_s: float = 35.0) -> ClipWindow:
    """
    ffmpeg → mono float32 samples
    RMS in 0.5 s hops → smooth → argmax peak
    Center a target_s window on peak, snap to word boundaries
    """
```

**ffmpeg extract:**

```bash
ffmpeg -y -i vocals.wav -ac 1 -ar 22050 -f f32le -acodec pcm_f32le pipe:1
```

### 3.6 Slice alignment

```python
def slice_alignment(words: list[dict], start_ms: int, end_ms: int) -> list[dict]:
    clipped = [w for w in words if w["end_ms"] > start_ms and w["start_ms"] < end_ms]
    base = start_ms
    return [
        {
            "word": w["word"],
            "start_ms": max(0, w["start_ms"] - base),
            "end_ms": min(end_ms - base, w["end_ms"] - base),
        }
        for w in clipped
    ]
```

---

## 4. Database Changes

**File:** `db.py`

```sql
ALTER TABLE jobs ADD COLUMN output_format TEXT DEFAULT 'full';   -- 'full' | 'short'
ALTER TABLE jobs ADD COLUMN clip_start_ms INTEGER;
ALTER TABLE jobs ADD COLUMN clip_end_ms INTEGER;
ALTER TABLE jobs ADD COLUMN clip_source TEXT;                    -- segment_picker source
ALTER TABLE jobs ADD COLUMN clip_label TEXT;                     -- "Chorus", etc.
ALTER TABLE jobs ADD COLUMN lyrics_sections_path TEXT;         -- JSON sidecar
```

**`add_job()`** — accept `output_format='short'`.

**New helpers:**

- `save_clip_window(job_id, window: ClipWindow)`
- `get_output_dimensions(job)` → `(1080, 1920)` or `(1920, 1080)`

---

## 5. Config Additions

**File:** `config.py` (or `config_shorts.py` imported when needed)

```python
# Output format
OUTPUT_FORMAT_FULL = "full"
OUTPUT_FORMAT_SHORT = "short"

# Shorts geometry
SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920
FULL_WIDTH = 1920
FULL_HEIGHT = 1080

# Shorts timing
SHORTS_MIN_S = 15.0
SHORTS_MAX_S = 55.0
SHORTS_TARGET_S = 35.0
SHORTS_PAD_S = 0.5
SHORTS_INTRO_S = 1.5          # title card; 0 to skip
SHORTS_LEAD_IN_S = 0.8        # lyric lead-in after title

# Typography (scaled from 16:9)
SHORTS_FONT_SIZE = 72
SHORTS_LINE_SPACING = 90
SHORTS_LYRIC_CENTER_Y = 960   # lower third / center — tune in preview
SHORTS_MAX_WIDTH_PX = 980     # ~91% of 1080

# Visualizer position (top-right in portrait)
SHORTS_VIZ_X = 680
SHORTS_VIZ_Y = 80
SHORTS_VIZ_W = 360
SHORTS_VIZ_H = 100

# Background search
SHORTS_AESTHETIC_QUERIES = [
    "vertical aesthetic sunset",
    "portrait moody sky",
    "phone wallpaper nature",
    "vertical neon city night",
    "portrait golden hour",
    "vertical ocean waves",
    "portrait forest path",
    "vertical galaxy stars",
]
```

**Helper:**

```python
def get_render_profile(output_format: str) -> dict:
    if output_format == OUTPUT_FORMAT_SHORT:
        return { "width": SHORTS_WIDTH, "height": SHORTS_HEIGHT, ... }
    return { "width": FULL_WIDTH, "height": FULL_HEIGHT, ... }
```

---

## 6. Background Image APIs

**File:** `pipeline/background_fetcher.py`

Add `orientation` parameter to `run()` and search functions.

### Pexels

```
GET https://api.pexels.com/v1/search
  ?query={query}
  &orientation=portrait      # was: landscape
  &per_page=15
  &page={page}

Headers: Authorization: {PEXELS_API_KEY}
```

Pick URL: `src.large2x` → `src.original` → `src.large` (same as today).

### Pixabay

```
GET https://pixabay.com/api/
  ?key={PIXABAY_API_KEY}
  &q={query}
  &image_type=photo
  &orientation=vertical      # was: horizontal
  &per_page=20
  &page={page}
```

Pick URL: `largeImageURL` → `webformatURL`.

### Crop / resize

**File:** `pipeline/stage4_render.py` — generalize `_crop_and_resize`:

```python
def _crop_and_resize(img: Image.Image, target_w: int = 1920, target_h: int = 1080) -> Image.Image:
    # center-crop to target aspect, then LANCZOS resize
```

Call with `(1080, 1920)` for Shorts everywhere: preview, overlay stack, final render.

### Mood overlay gradient

**File:** `pipeline/mood_overlay.py` — `caption_gradient(..., start_frac=0.55)` darkens bottom 45%. For 9:16, consider `start_frac=0.60` so captions sit above YouTube Shorts UI chrome.

---

## 7. Whisper / Transcription (unchanged core, new sidecar)

**File:** `pipeline/stage3_transcribe.py` — no change to API call.

Still transcribe **full vocals**; segment picking runs **after** stage 3.

### Existing API (reference)

```python
client.audio.transcriptions.create(
    model="whisper-1",
    file=f,
    response_format="verbose_json",
    timestamp_granularities=["word"],
    prompt=chunk_prompt,
)
```

### Existing prompts (`pipeline/lyrics_hint.py`)

**First chunk:**

```
{artist}. {title}. Song vocals. Spellings: {hint1}, {hint2}, …
```

Max 224 chars. Hints from tricky words only — **not** lyric passages.

**Later chunks:**

```
{tail of previous transcript, last 224 chars}
```

### New: lyrics sections sidecar

**File:** `pipeline/lyrics_sections.py`

When saving lyrics, also write `job_{id}_sections.json`:

```json
{
  "sections": [
    {
      "label": "verse",
      "index": 1,
      "lines": ["First line", "Second line"],
      "is_short_target": false
    },
    {
      "label": "chorus",
      "index": null,
      "lines": ["Hook line one", "Hook line two"],
      "is_short_target": true
    }
  ],
  "raw_short_tag": "[Short: Chorus]"
}
```

`save_lyrics_md()` in `lyrics_hint.py` calls `parse_sections(raw_text)` before `clean_user_lyrics()`.

---

## 8. Render Pipeline Changes

**File:** `pipeline/stage4_render.py`

### 8.1 Parameterize dimensions

Replace hardcoded `1920`, `1080`, `(1920 - line_w) / 2`, `LYRIC_CENTER_Y` with values from `get_render_profile(output_format)`.

### 8.2 Shorts intro (replace dual 4–9 s timeline)

For Shorts, **skip the early/late crossover logic**. Use fixed short intro:

```python
if output_format == "short":
    intro_dur = config.SHORTS_INTRO_S       # 1.5 s
    lead_in_s = config.SHORTS_LEAD_IN_S    # 0.8 s
    target_first_word_s = intro_dur + lead_in_s
    # same pad/trim math as today, but smaller targets
```

Optional: `SHORTS_INTRO_S = 0` → lyrics start immediately (good for 15 s clips).

### 8.3 Audio trim for Shorts

Clip window `(clip_start_s, clip_end_s)` from segment picker.

```python
clip_start_s = clip_start_ms / 1000.0
clip_duration_s = (clip_end_ms - clip_start_ms) / 1000.0

# Alignment already sliced and rebased to 0
# Audio: seek to clip start, limit duration
```

**FFmpeg inputs:**

```python
ffmpeg_cmd = [
    "ffmpeg", "-y", "-loglevel", "error",
    "-loop", "1", "-t", f"{video_duration:.3f}", "-i", background_png,
    "-ss", f"{clip_start_s + trim_offset_s:.3f}",   # input seek on audio
    "-t", f"{clip_duration_s - trim_offset_s + pad_delay_s:.3f}",
    "-i", audio_path,
]
```

`video_duration` for Shorts = `clip_duration - trim_offset + pad_delay` (not full song).

### 8.4 Visualizer sync for Shorts

When trimming audio, slice samples from `clip_start_s` to `clip_end_s`, then apply pad/trim:

```python
start_sample = int((clip_start_s + trim_offset_s) * sr)
end_sample = int(clip_end_s * sr)
samples = samples[start_sample:end_sample]
```

### 8.5 Output naming

```python
suffix = "_shorts" if output_format == "short" else "_7clouds"
output_path = OUTPUT_DIR / f"{safe_title}{suffix}.mp4"
```

### 8.6 Full filter graph (Shorts, with visualizer)

```
[2:v]colorkey=0x000000:0.15:0.05[keyed_vis];
[0:v][keyed_vis]overlay=x=680:y=80:shortest=1[bg_vis];
[bg_vis]{drawtext chain}[outv];
[1:a]adelay={delay_ms}|{delay_ms}[outa]
```

Encode: `libx264 -preset medium -crf 22 -pix_fmt yuv420p -c:a aac -b:a 192k -shortest`

---

## 9. Worker Integration

**File:** `worker.py` — `run_pipeline()`

```python
# After stage 3
alignment = stage3_transcribe.run(...)

if job["output_format"] == "short":
    from pipeline.segment_picker import pick_clip_window, slice_alignment
    from pipeline.lyrics_sections import load_sections

    sections = load_sections(job.get("lyrics_sections_path"))
    window = pick_clip_window(
        alignment,
        vocals_path=vocals_path,
        sections=sections,
        duration_s=info["duration"],
    )
    db.save_clip_window(job["id"], window)
    alignment = slice_alignment(alignment, window.start_ms, window.end_ms)
    info["clip_start_ms"] = window.start_ms
    info["clip_end_ms"] = window.end_ms
    info["output_format"] = "short"

# stage 4
video_path = stage4_render.run(info, alignment)
```

**`run_preview()`** — pass `output_format` to `background_fetcher.run()` and `build_preview(..., width, height)`.

---

## 10. Bot / UI Changes

| File | Change |
|------|--------|
| `bot.py` | Format keyboard after URL; store `output_format` |
| `telegram_ui.py` | Copy for Shorts flow, segment summary in status |
| `db.py` | Schema + helpers (§4) |

**New callback patterns:**

```
fmt:full:{job_id}
fmt:short:{job_id}
```

**Status message example:**

```
Job #42 — Shorts
Segment: Chorus (0:42–1:17, 35s)
Status: Rendering…
```

---

## 11. YouTube Upload (optional)

**File:** `pipeline/stage5_upload.py`

When `output_format == "short"`:

```python
title = f"{artist} - {title} (Lyrics Short)"
tags = [..., "shorts", "short"]
description += "\n\n#Shorts"
```

YouTube Shorts eligibility: ≤ 60 s and vertical — our 55 s cap satisfies this.

---

## 12. New OpenAI Prompt (segment disambiguation — optional phase 2)

When heuristics tie (two choruses, sparse lyrics), call GPT once:

**System:**

```
You pick a 15–55 second clip from a song for a YouTube Short lyric video.
Prefer the chorus or the most memorable hook. Return JSON only.
```

**User:**

```
Title: {title}
Artist: {artist}
Duration: {duration_s}s

Sections from user lyrics:
{sections_json}

Repeated phrases found in transcript:
{top_ngrams}

Candidate windows:
{candidates with start/end/label/score}

Pick one window. Constraints: 15 <= duration <= 55 seconds.
```

**Response schema:**

```json
{
  "start_ms": 42000,
  "end_ms": 77000,
  "label": "Chorus (2nd occurrence)",
  "reason": "Strongest hook, clean 35s boundary"
}
```

Env gate: `SHORTS_USE_GPT_PICKER=false` (default off; heuristics only).

---

## 13. File Plan (what to build)

| # | File | Action |
|---|------|--------|
| 1 | `pipeline/lyrics_sections.py` | **New** — parse `[Chorus]`, `[Short: …]`, save JSON |
| 2 | `pipeline/segment_picker.py` | **New** — pick + slice clip window |
| 3 | `pipeline/lyrics_hint.py` | Call section parser on save |
| 4 | `config.py` | Shorts constants + `get_render_profile()` |
| 5 | `db.py` | New columns + `save_clip_window()` |
| 6 | `pipeline/background_fetcher.py` | `orientation` param |
| 7 | `pipeline/preview_background.py` | Pass dimensions |
| 8 | `pipeline/mood_overlay.py` | Accept width/height (already does) |
| 9 | `pipeline/overlay_preview.py` | Portrait previews |
| 10 | `pipeline/stage4_render.py` | Profile-aware layout + Shorts trim |
| 11 | `pipeline/audio_visualizer.py` | Accept position/size from profile |
| 12 | `worker.py` | Segment pick between stage 3 and 4 |
| 13 | `bot.py` + `telegram_ui.py` | Format choice + lyrics hint text |
| 14 | `pipeline/stage5_upload.py` | Shorts metadata |

**Experiment folder:** prototype `segment_picker.py` and `lyrics_sections.py` in `xpt/` first, then promote to `pipeline/`.

---

## 14. Implementation Order

### Phase 1 — Core Shorts (MVP)

1. DB + config + `get_render_profile()`
2. `lyrics_sections.py` — parse and store sections
3. `segment_picker.py` — user tag + chorus match + repeat detect
4. `stage4_render.py` — 9:16 layout + audio trim + short intro
5. `background_fetcher.py` — portrait search
6. Worker hook between stage 3 and 4
7. Bot format toggle

### Phase 2 — Polish

1. Energy-peak fallback
2. Segment preview / approve in Telegram
3. GPT disambiguation for ambiguous songs
4. Dual output: same job produces full + short (optional)

### Phase 3 — Tuning

1. A/B caption Y position for Shorts safe zone
2. Shorter page hold times for 15 s clips
3. `#Shorts` upload automation

---

## 15. Testing Checklist

- [ ] Song with `[Short: Chorus]` in pasted lyrics → correct window
- [ ] Song with only `[Chorus]` labels → chorus matched
- [ ] Song with no lyrics → repeat or energy pick, 15–55 s
- [ ] 3-minute song → output ≤ 55 s, vertical 1080×1920
- [ ] 20-second song → entire song or pad to 15 s min
- [ ] Background preview is portrait, not stretched
- [ ] Lyric timing matches audio after trim (no drift)
- [ ] Visualizer aligned with trimmed audio
- [ ] Telegram video ≤ 50 MB for typical Shorts
- [ ] Full-video jobs unchanged (regression)

---

## 16. Example End-to-End (Shorts)

```
User → YouTube URL
Bot  → [Full] [Short]  → user picks Short
Bot  → [I have lyrics] → user pastes:

       [Verse 1]
       Walking down the road

       [Chorus]
       We are alive tonight

       [Short: Chorus]

Worker preview:
  - download audio
  - Pexels portrait background
  - preview 1080×1920 PNG → Telegram

User approves bg → picks wine_burgundy overlay → QUEUED

Worker render:
  - Demucs vocals
  - Whisper full alignment → alignment.json
  - parse sections → chorus lines ["we are alive tonight"]
  - match → 42.0s – 77.0s (35s)
  - slice alignment → rebase to 0
  - render 1080×1920, 1.5s title, trimmed audio
  - output: outputs/Song Title_shorts.mp4
```

---

## 17. Environment Variables (new)

| Variable | Default | Purpose |
|----------|---------|---------|
| `SHORTS_MIN_S` | `15` | Minimum clip length |
| `SHORTS_MAX_S` | `55` | Maximum clip length |
| `SHORTS_TARGET_S` | `35` | Preferred length |
| `SHORTS_INTRO_S` | `1.5` | Title card duration |
| `SHORTS_USE_GPT_PICKER` | `false` | GPT segment disambiguation |

Existing keys reused: `OPENAI_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`.

---

## 18. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Chorus lyrics don't match Whisper words | Fuzzy token match; fall back to repeat detect |
| User `[Short: Verse 2]` but no Verse 2 in audio | Second-best section + log warning |
| Portrait stock scarce | Rotate `SHORTS_AESTHETIC_QUERIES`; crop landscape center as last resort |
| Intro eats half of a 15 s clip | Allow `SHORTS_INTRO_S=0` for very short selections |
| Full song transcription cost | Unchanged — one Whisper pass; slice is free |

---

*This document is the single source of truth for implementing Shorts. Prototype new logic in `xpt/` before merging into `pipeline/`.*