# Shorts Integration Plan — xpt → Main Pipeline

> **Status:** Experiment validated in `xpt/` (`love_is_gone_shorts.mp4`, `My_Body_Isnt_Ready_shorts.mp4`).  
> **Goal:** Add a **Shorts output mode** alongside existing 16:9 full videos without breaking current jobs.

---

## 1. What We Are Integrating

The `xpt/` experiment proved:

| Feature | Validated in xpt |
|---------|------------------|
| 9:16 player UI (720×1280 @ 30fps) | `background.py`, `player_ui.py` |
| Synced bar visualizer | `audio_sync.py` |
| Line-level blue karaoke | `lyric_layout.py` |
| Chorus clip + Whisper lines | `shorts_render.py --pipeline` |
| Decorative prev/pause/next in circles | `player_ui.py` |

**Not integrated yet:** Telegram format choice, DB fields, portrait backgrounds, stage5 Shorts upload tags.

**Experiment update:** `xpt/segment_picker.py` now picks **3 shorts per song** (1 chorus + 2 improv). Bundle debug: `xpt/job_{id}_shorts_bundle.json`.

---

## 2. Integration Principles

1. **Do not replace `stage4_render.py`** — add `pipeline/stage4_shorts.py` as a parallel renderer.
2. **Share stages 1–3** — same download, Demucs, Whisper alignment JSON.
3. **Branch at render time** — `output_format == 'short'` → `stage4_shorts`, else → `stage4_render`.
4. **Shorts skip stock-photo flow** — black starfield background; no Pexels/Pixabay, no mood overlay grid.
5. **Write debug artifacts every Shorts job** — see `docs/shorts/DEBUG.md`.
6. **Keep `xpt/`** until production Shorts match experiment quality for 3+ songs.

---

## 3. Target Architecture

```
User URL
  → [Full video | YouTube Short]     ← bot.py (new)
  → lyrics (optional, [Short: Chorus]) ← lyrics_sections.py (new)
  → FETCHING_PREVIEW
       ├─ full  → background_fetcher + preview_background (16:9)  [unchanged]
       └─ short → shorts_preview.py (9:16 static frame)           [new]
  → PENDING_BG_APPROVAL
       ├─ full  → approve bg → overlay grid → QUEUED
       └─ short → auto-approve (no bg) → QUEUED                    [simplified]
  → worker.run_pipeline
       ├─ stage2_demucs
       ├─ stage3_transcribe
       ├─ segment_picker.pick_clip()   [new, shorts only]
       ├─ stage4_shorts.run()          [new] OR stage4_render.run()
       └─ deliver / stage5_upload
```

---

## 4. File Promotion Map

### 4.1 Move xpt → pipeline (rename/refactor)

| xpt file | Pipeline destination | Notes |
|----------|---------------------|-------|
| `background.py` | `pipeline/shorts_background.py` | Star + smoke; seed from `job_id` |
| `player_ui.py` | `pipeline/shorts_player_ui.py` | Layout constants → `config.py` |
| `audio_sync.py` | `pipeline/shorts_visualizer.py` | Precompute frames |
| `lyric_layout.py` | `pipeline/shorts_lyrics.py` | Line grouping + karaoke |
| `shorts_render.py` | `pipeline/stage4_shorts.py` | `run(info, alignment) -> Path` API |

### 4.2 New pipeline modules (not in xpt yet)

| File | Purpose |
|------|---------|
| `pipeline/segment_picker.py` | Chorus / `[Short: …]` / repeat / energy → `ClipWindow` |
| `pipeline/lyrics_sections.py` | Parse `[Chorus]`, `[Short: Chorus]` before `clean_user_lyrics` |
| `pipeline/shorts_preview.py` | Telegram preview PNG (title + static visualizer) |
| `pipeline/shorts_debug.py` | Write `job_{id}_shorts_debug.json` + optional frame dumps |

### 4.3 Touch existing files

| File | Change |
|------|--------|
| `db.py` | New columns (§5) |
| `config.py` | Shorts constants + `get_shorts_profile()` |
| `bot.py` | Format keyboard; Shorts lyrics hint text |
| `telegram_ui.py` | Shorts status strings, no overlay grid for shorts |
| `worker.py` | Branch preview + render on `output_format` |
| `pipeline/lyrics_hint.py` | Call `lyrics_sections.save_sections()` |
| `pipeline/stage5_upload.py` | `#Shorts` title/tags when short |
| `.env.example` | Shorts env vars |

---

## 5. Database Schema

```sql
ALTER TABLE jobs ADD COLUMN output_format TEXT DEFAULT 'full';
-- 'full' | 'short'

ALTER TABLE jobs ADD COLUMN clip_start_ms INTEGER;
ALTER TABLE jobs ADD COLUMN clip_end_ms INTEGER;
ALTER TABLE jobs ADD COLUMN clip_source TEXT;    -- user_tag | chorus | repeat | energy | fallback
ALTER TABLE jobs ADD COLUMN clip_label TEXT;     -- "Chorus", human-readable
ALTER TABLE jobs ADD COLUMN lyrics_sections_path TEXT;
ALTER TABLE jobs ADD COLUMN shorts_debug_path TEXT;
```

**`db.py` helpers:**

- `add_job(..., output_format='short')`
- `save_clip_window(job_id, window)`
- `save_shorts_debug(job_id, path)`
- `is_short_job(job) -> bool`

---

## 6. Config (`config.py`)

```python
# Shorts output
SHORTS_WIDTH = 720
SHORTS_HEIGHT = 1280
SHORTS_FPS = 30
SHORTS_MIN_S = 15.0
SHORTS_MAX_S = 55.0
SHORTS_TARGET_S = 35.0
SHORTS_CRf = 20
SHORTS_AUDIO_BITRATE = "128k"

# Visual
SHORTS_ACTIVE_COLOR = "#74A7D1"
SHORTS_TEXT_COLOR = "#FAFAFA"
SHORTS_FONT_SIZE = 27
SHORTS_TITLE_FONT_SIZE = 34

# Debug
SHORTS_DEBUG = os.getenv("SHORTS_DEBUG", "true").lower() == "true"
SHORTS_DEBUG_SAVE_FRAMES = os.getenv("SHORTS_DEBUG_SAVE_FRAMES", "false").lower() == "true"
```

---

## 7. `stage4_shorts.run()` Contract

Match existing stage pattern:

```python
def run(info: dict, alignment: list[dict]) -> Path:
    """
    info keys:
      title, artist, audio_path (Path), duration (float),
      clip_start_ms, clip_end_ms, clip_label (optional),
      job_id (for debug + background seed)

    Returns: outputs/{title}_shorts.mp4
    """
```

**Internal steps:**

1. `segment_picker` already ran in worker → alignment sliced, clip bounds in `info`
2. Extract clip WAV to `temp/job_{id}_clip.wav`
3. `group_words_into_lines(sliced_alignment)`
4. `precompute_waveform_frames(clip_samples)`
5. Render PNG sequence → ffmpeg encode
6. `shorts_debug.write_report(job_id, ...)` → JSON path saved to DB

---

## 8. Worker Changes (`worker.py`)

### 8.1 Preview path

```python
if job.get("output_format") == "short":
    preview_path = shorts_preview.build(job, title, artist, audio_path)
    # Skip background_fetcher; status → QUEUED directly OR PENDING_STYLE with no overlay
else:
    # existing preview path
```

**Recommended for v1:** Shorts skip bg approval → after lyrics, go straight to `QUEUED` with a single preview frame (optional approve later in v2).

### 8.2 Render path (after stage 3)

```python
alignment = stage3_transcribe.run(...)

if job.get("output_format") == "short":
    from pipeline import segment_picker, shorts_debug
    sections = load_sections(job.get("lyrics_sections_path"))
    window = segment_picker.pick_clip(alignment, vocals_path, sections, info["duration"])
    db.save_clip_window(job_id, window)
    sliced = slice_alignment(alignment, window.start_ms, window.end_ms)
    info.update(clip_start_ms=window.start_ms, clip_end_ms=window.end_ms, job_id=job_id)
    video_path = stage4_shorts.run(info, sliced)
    shorts_debug.finalize(job_id, window, sliced, video_path)
else:
    video_path = stage4_render.run(info, alignment)
```

---

## 9. Bot / Telegram UX

### 9.1 After URL paste

```
[ Full lyric video ]  [ YouTube Short ]
```

Callback: `fmt:full:{id}` / `fmt:short:{id}`

### 9.2 Lyrics prompt (Shorts)

```
Paste lyrics. Mark the clip section:
  [Short: Chorus]
  or [Chorus] (auto-pick if no Short tag)
```

### 9.3 Status messages

```
Job #15 — Shorts (chorus, 21s)
Segment: Chorus 0:54–1:15
Status: Rendering…
```

---

## 10. Segment Picker (production logic)

Priority (from `SHORTS_PLAN.md`):

1. `[Short: …]` / `[Short: M:SS-M:SS]` from `lyrics_sections.json`
2. First `[Chorus]` block matched to alignment
3. Repeated n-gram hook (2nd/3rd occurrence)
4. Energy peak on vocals stem
5. Fallback: middle third, snapped to gaps

**v1 shortcut (already working in experiment):** detect Whisper `"Chorus"` label timestamps + next block boundaries (as used manually for job #14).

---

## 11. Debug Documentation (required MD files)

Create and maintain under `docs/shorts/`:

| File | Purpose |
|------|---------|
| [`docs/shorts/README.md`](../docs/shorts/README.md) | Operator overview, env vars, CLI |
| [`docs/shorts/ARCHITECTURE.md`](../docs/shorts/ARCHITECTURE.md) | Data flow, module map, state machine |
| [`docs/shorts/DEBUG.md`](../docs/shorts/DEBUG.md) | **Debugging playbook** — artifacts, commands, common failures |
| [`docs/shorts/SEGMENT_PICKER.md`](../docs/shorts/SEGMENT_PICKER.md) | How clip windows are chosen + override |
| [`docs/shorts/RENDER_SPEC.md`](../docs/shorts/RENDER_SPEC.md) | Layout coordinates, colors, timing rules |

### 11.1 Per-job debug JSON (automatic)

Every Shorts render writes `temp/job_{id}_shorts_debug.json`:

```json
{
  "job_id": 14,
  "output_format": "short",
  "title": "My Body Isn't Ready",
  "artist": "sombr",
  "clip": {
    "start_ms": 54259,
    "end_ms": 75220,
    "duration_s": 20.96,
    "source": "chorus_label",
    "label": "Chorus"
  },
  "lines": [
    {"text": "Like you but my body", "start_s": 0.0, "end_s": 2.1, "center_y": 457}
  ],
  "render": {
    "fps": 30,
    "frames": 629,
    "resolution": "720x1280",
    "output": "outputs/My_Body_Isnt_Ready_shorts.mp4",
    "elapsed_s": 72.4
  },
  "warnings": []
}
```

When `SHORTS_DEBUG_SAVE_FRAMES=true`, also keep `temp/job_{id}_shorts_frames/` (first, middle, last PNG).

---

## 12. Implementation Phases (PR order)

### PR 1 — Foundation (no user-visible change)

- [ ] `config.py` shorts constants
- [ ] `db.py` schema + helpers
- [ ] `pipeline/shorts_debug.py` + `docs/shorts/*`
- [ ] Promote `xpt/*.py` → `pipeline/shorts_*.py` (imports only, no worker hook)

### PR 2 — Segment + lyrics sections

- [ ] `pipeline/lyrics_sections.py`
- [ ] `pipeline/segment_picker.py`
- [ ] Unit-test segment picker against job #14 alignment (expected 54.3–75.2s)

### PR 3 — Renderer

- [ ] `pipeline/stage4_shorts.py` (port from `xpt/shorts_render.py`)
- [ ] CLI: `python -m pipeline.stage4_shorts --job 14` for offline debug

### PR 4 — Worker + bot

- [ ] `bot.py` format toggle
- [ ] `worker.py` branch render
- [ ] `telegram_ui.py` Shorts copy
- [ ] Shorts skip bg/overlay (v1)

### PR 5 — Upload + polish

- [ ] `stage5_upload.py` Shorts metadata
- [ ] Optional: `SHORTS_WIDTH=1080` upscale profile
- [ ] Remove hardcoded `CHORUS_CLIP_*` from experiment

---

## 13. Offline Debug Commands (post-integration)

```bash
# Re-render a completed job as Shorts without Telegram
python -m pipeline.stage4_shorts --job 14

# Dry-run segment pick only
python -m pipeline.segment_picker --job 14 --print

# Dump debug report
python -m pipeline.shorts_debug --job 14 --cat

# Compare to xpt reference
python xpt/shorts_render.py --pipeline --job 14
diff temp/job_14_shorts_debug.json temp/job_14_shorts_debug_xpt.json
```

---

## 14. Testing Checklist (before removing xpt gate)

- [ ] Full-video job unchanged (regression on job #14 format=full)
- [ ] Shorts job: segment 15–55s
- [ ] `[Short: Chorus]` in lyrics picks correct window
- [ ] No lyrics: repeat or energy pick works
- [ ] Visualizer animates; playhead syncs to audio
- [ ] Debug JSON written every Shorts job
- [ ] Telegram delivers MP4 ≤ 50MB
- [ ] `docs/shorts/DEBUG.md` resolves top 5 failure modes

---

## 15. Rollback

- `output_format` defaults to `'full'` — existing jobs unaffected
- Feature flag: `ENABLE_SHORTS=false` in `.env` hides bot button
- `stage4_shorts` is never called when `output_format != 'short'`

---

## 16. Sign-off Criteria (xpt → production)

Promote when **all** pass:

1. Side-by-side with `xpt/#lyrics.mp4` — layout match approved by you
2. Pipeline Short for 3 different songs (chorus, no lyrics, user `[Short: …]`)
3. Debug JSON explains every clip decision without reading code
4. `docs/shorts/DEBUG.md` used to fix one real issue end-to-end

---

*See `docs/shorts/` for debugging playbooks. Experiment code stays in `xpt/` until §16 sign-off.*