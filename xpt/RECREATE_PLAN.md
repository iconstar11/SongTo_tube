# Reference Video Evaluation + Recreation Plan

> **Scope:** Everything below stays in `xpt/` until shorts quality is confirmed.  
> **Reference:** `xpt/#lyrics.mp4` — *"Love Is Gone"* by SpeakSoul English

---

## 1. Reference Video — What We Analyzed

| Property | Value |
|----------|-------|
| File | `xpt/#lyrics.mp4` |
| Title metadata | "Love Is Gone" — SpeakSoul English |
| Resolution | **720×1280** (9:16) |
| FPS | 30 |
| Duration | **28.2 s** |
| Video codec | H.264 High, yuv420p, bt709 |
| Audio | AAC 44.1 kHz stereo, 128 kbps |
| File size | ~1.4 MB (heavily compressed, Shorts-friendly) |

This is **not** our current 7clouds output. It is a **music-player UI lyric short** — black cinematic background, decorative waveform, scrolling blue karaoke highlight.

---

## 2. Visual Evaluation (frame-by-frame)

### 2.1 Layout zones (720×1280)

```
┌─────────────────────────────┐  y=0
│  🌼  Love Is Gone           │  Header (icon + title, always visible)
│  ═══ waveform bars ═══      │  Decorative waveform (~y=200–280)
│  ────●──────── progress     │  Scrubber bar + playhead (animates)
│      ⏮  ⏸  ⏭              │  Player controls (static icons)
│                             │
│         I'm sorry           │
│         don't leave me      │  Full lyric block
│    ► I want you here ◄      │  One line highlighted blue (#74a7d1)
│         I know that         │
│         your love is gone   │
│            …                │
│                             │
│   ★  star particles  ★      │  Black bg + subtle stars
│   ~~~~ smoke ~~~~           │  Dark smoke at bottom (~y=900+)
└─────────────────────────────┘  y=1280
```

### 2.2 Style traits to match

| Element | Reference behavior | Our pipeline today |
|---------|-------------------|-------------------|
| Background | Pure black + star dust + bottom smoke | Stock photo + mood overlay |
| Aspect | 9:16 portrait | 16:9 landscape |
| Lyrics case | **Sentence case** | ALL CAPS |
| Lyric mode | **All lines visible**, one active line | 1–3 lines per page, word-by-word |
| Active line color | **Blue `#74a7d1`** | White (optional gold per-word) |
| Title treatment | Flower icon + song title, persistent | 4–9 s title card then hidden |
| Waveform | White bar waveform, top area | Rainbow symmetric pill bars (optional) |
| Progress bar | White line + dot, advances with time | None |
| Player chrome | Rewind / pause / skip icons | None |
| Intro | **None** — lyrics UI from frame 0 | Long title card intro |
| Font | Clean sans-serif, medium weight | edosz.ttf, bold, large |
| Duration | ~28 s clip | Full song |

### 2.3 Motion analysis

- **Background:** Static (stars/smoke do not move).
- **Waveform:** Mostly static decoration; small changes frame-to-frame (likely tied loosely to audio or a pre-baked image).
- **Progress bar:** Playhead position advances over the clip (primary motion besides lyric highlight).
- **Lyrics:** Karaoke-style — **one line turns blue at a time**, stepping through the full block. By ~14 s, highlight reaches the bottom lines ("I know this isn't easy" → "That your love is gone").
- **No camera movement, no cuts, no photo backgrounds.**

### 2.4 Lyrics content (full block shown)

```
I'm sorry
don't leave me
I want you here with me
I know that
your love is gone
I can't breathe
I'm so weak
I know this isn't easy
Don't tell me that
your love is gone
That your love is gone
```

This is the **chorus / emotional hook** of SLANDER ft. Dylan Matthew — "Love Is Gone". The 28 s clip covers essentially this entire repeated section, which aligns with our Shorts goal (chorus-first, 15–55 s).

### 2.5 Color palette (sampled)

| Role | Hex | Notes |
|------|-----|-------|
| Background | `#000000` | Pure black |
| Primary text | `#FAFAFA` | Near-white |
| Active lyric | `#74A7D1` | Soft blue |
| Waveform / UI | `#FFFFFF` | White |
| Stars | `#FFFFFF` @ ~15% opacity | Sparse dots |
| Smoke | `#1A1A1A` – `#333333` | Soft gradient blobs, bottom |

---

## 3. Gap vs Current Pipeline

Our pipeline can supply **audio + word timestamps** (stages 1–3) but **cannot** produce this visual style without a new renderer:

| Reuse from main pipeline | Must build new in `xpt/` |
|--------------------------|--------------------------|
| yt-dlp audio download | Player UI template layout |
| Demucs vocals | Star + smoke background generator |
| Whisper word alignment | Line-level karaoke highlight |
| Segment picker (chorus clip) | Decorative waveform + progress bar |
| — | Player control icons |
| — | 720×1280 @ 30 fps profile |

**Do not modify `pipeline/stage4_render.py` yet.** Prototype the look entirely in `xpt/`.

---

## 4. Recreation Strategy

### Phase A — Static template (match one frame)

Goal: Produce a PNG that pixel-matches `frame_003.png` (or close).

**Files to create in `xpt/`:**

```
xpt/
├── RECREATE_PLAN.md          ← this file
├── assets/
│   ├── flower.png            ← daisy icon (or emoji rasterized)
│   ├── icons/
│   │   ├── rewind.png
│   │   ├── pause.png
│   │   └── forward.png
│   └── smoke_overlay.png     ← pre-made bottom smoke
├── background.py             ← black + stars + smoke composite
├── player_ui.py              ← waveform, progress bar, icons
├── lyric_layout.py           ← line grouping + Y positions
└── shorts_render.py          ← ffmpeg compositor (experiment entry point)
```

**`background.py`**

```python
def build_background(width=720, height=1280, seed=42) -> Image:
    # 1. Fill #000000
    # 2. Sprinkle ~80-120 white dots (1-2px, random, opacity 0.1-0.4)
    # 3. Alpha-composite smoke_overlay.png at bottom
```

**`player_ui.py`**

```python
def build_waveform_strip(audio_path, width=600, height=80) -> Image:
    # ffmpeg showwavespic or showwaves with monochrome white bars
    # ffmpeg -i clip.wav -filter_complex "showwavespic=s=600x80:colors=white" wave.png

def render_progress_bar(frame_t, duration, width=600) -> (bar_img, playhead_x):
    # white 2px line, circle r=6 at x = (t/duration)*width
```

**`lyric_layout.py`**

```python
def group_words_into_lines(alignment, max_width_px=560) -> list[dict]:
    # Use gap breaks (reuse logic ideas from stage4_render)
    # Returns [{text, start_ms, end_ms, y}, ...]

def line_color(line, current_ms) -> str:
    return "#74A7D1" if line.start_ms <= current_ms < line.end_ms else "#FAFAFA"
```

### Phase B — Animated karaoke (match motion)

For each frame at 30 fps:

1. Composite static background.
2. Draw header (flower + title).
3. Paste static waveform PNG (or lightly animated `showwaves` video overlay).
4. Draw progress bar at `t / duration`.
5. Draw all lyric lines; color active line blue.

**Two implementation options:**

| Approach | Pros | Cons |
|----------|------|------|
| **Pillow frame sequence → ffmpeg** | Full control over blue highlight | Slower render |
| **ffmpeg drawtext chain** | Fast, matches main pipeline pattern | Many drawtext filters (11 lines × 2 colors) |

**Recommended for experiment:** Pillow frames → encode:

```bash
ffmpeg -y -framerate 30 -i frame_%06d.png -i clip.wav \
  -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p \
  -c:a aac -b:a 128k -shortest output.mp4
```

### Phase C — Wire to real audio (pipeline hook)

Once the template looks right on the reference clip:

```python
# xpt/run_short_experiment.py
# 1. Download audio (stage1) OR use local file
# 2. Demucs + Whisper (stages 2-3) — imports from pipeline/
# 3. segment_picker → 15-55s chorus window
# 4. Trim audio to window
# 5. shorts_render.render(trimmed_audio, sliced_alignment, title, output)
```

---

## 5. ffmpeg Recipes

### 5.1 Generate waveform still (white bars on transparent/black)

```bash
ffmpeg -y -i clip.wav \
  -filter_complex "showwavespic=s=600x80:colors=white|white:scale=trunc(iw/2)*2:trunc(ih/2)*2" \
  -frames:v 1 wave.png
```

### 5.2 Animated waveform (optional, if we want motion)

```bash
ffmpeg -y -i clip.wav \
  -filter_complex "showwaves=s=600x80:mode=line:colors=white:rate=30" \
  -c:v libx264 -pix_fmt yuv420p wave.mp4
```

### 5.3 Trim audio to chorus window

```bash
ffmpeg -y -ss 42.0 -t 28.2 -i full_song.mp3 -c:a aac -b:a 128k clip.wav
```

### 5.4 Final composite (drawtext approach sketch)

```bash
# One drawtext per line, enable=between(t,start,end) for blue window
# Example single line:
drawtext=fontfile=font.ttf:text='I want you here with me':
  fontsize=28:fontcolor=74A7D1:x=(w-text_w)/2:y=550:
  enable='between(t,4.2,6.1)'
```

### 5.5 Encode target (match reference)

```bash
-c:v libx264 -profile:v high -level 3.1 -crf 23
-r 30 -s 720x1280 -pix_fmt yuv420p
-c:a aac -b:a 128k -ar 44100
```

---

## 6. Typography Spec (tuned from reference)

| Setting | Value |
|---------|-------|
| Font | Sans-serif (try `assets/fonts/Inter-Medium.ttf` or `Segoe UI`) — **not** edosz |
| Size | ~26–30 px at 720w (scale ×1.5 for 1080w) |
| Line spacing | ~52–58 px between baselines |
| Lyric block top | y ≈ 400 |
| Max text width | ~560 px (centered) |
| Title size | ~36–40 px bold |
| Title Y | ~100 |

---

## 7. Segment Selection for This Style

For "Love Is Gone" the reference uses the **full chorus block** (~28 s). General rules:

1. User pastes lyrics with `[Chorus]` or `[Short: Chorus]`.
2. Whisper aligns words → match chorus text → get `start_ms` / `end_ms`.
3. If window > 55 s → trim to best 35–45 s centered on hook line ("your love is gone").
4. If window < 15 s → extend to next natural gap.

The reference clip needs **no title-card intro** — first lyric line should be active within ~1 s of video start. Adjust: `clip_start_ms - 500ms` padding, rebase timestamps to 0.

---

## 8. Experiment Milestones (stay in `xpt/`)

| Step | Deliverable | Success criteria |
|------|-------------|------------------|
| **E1** | `background.py` → `bg_test.png` | Black + stars + smoke matches reference mood |
| **E2** | `player_ui.py` → `ui_test.png` | Waveform + bar + icons aligned like frame_003 |
| **E3** | `lyric_layout.py` + static frame | All 11 lines positioned, one blue |
| **E4** | `shorts_render.py` on reference audio | 28 s video, highlight steps line-by-line |
| **E5** | `run_short_experiment.py` + pipeline audio | New song → auto chorus clip in same style |
| **E6** | Side-by-side review | You confirm quality ≥ reference |

**Only after E6:** promote `xpt/shorts_render.py` → `pipeline/stage4_shorts.py` and add bot toggle.

---

## 9. What We Are NOT Trying to Match (yet)

- Exact SpeakSoul branding / flower asset (close equivalent is fine).
- Byte-identical waveform shape (decorative match is enough).
- English-learning channel metadata / hashtags.
- 720 vs 1080 (start at 720 to match reference; upscale later).

---

## 10. Immediate Next Step

Build **E1 + E2 + E3** as a single static frame render, compare side-by-side with `xpt/frames/frame_003.png`, then animate (E4).

```bash
cd xpt
python shorts_render.py --reference-frame  # static match mode
python shorts_render.py --audio clip.wav --title "Love Is Gone"  # full video
```

---

*Reference frames extracted to `xpt/frames/` for comparison. Delete `frames/` and `audio.wav` when experiment is done.*