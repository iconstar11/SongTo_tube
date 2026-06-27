# Shorts Architecture

## Mode comparison

| | Full video | Shorts |
|---|-----------|--------|
| `output_format` | `full` | `short` |
| Resolution | 1920×1080 | 720×1280 |
| Duration | Full song | 15–55 s clip |
| Background | Stock photo + mood overlay | Black + stars + smoke |
| Renderer | `stage4_render.py` | `stage4_shorts.py` |
| Preview | Pexels/Pixabay photo | Static player frame |
| Overlay step | Required | Skipped (v1) |

## Job state machine (Shorts)

```
PENDING_LYRICS
  → user picks Short
PENDING_LYRICS_INPUT (optional)
  → lyrics + sections saved
FETCHING_PREVIEW
  → shorts_preview PNG (no stock photo)
QUEUED                    ← v1 skips PENDING_BG_APPROVAL / PENDING_STYLE
  → DEMUCS → TRANSCRIBING → RENDERING → COMPLETED
```

Full-video states are unchanged when `output_format=full`.

## Render pipeline (Shorts only)

```
audio_path (full song)
    ↓
stage2_demucs → vocals.wav
    ↓
stage3_transcribe → alignment.json (word timestamps)
    ↓
segment_picker.pick_clip()
    → ClipWindow { start_ms, end_ms, source, label }
    ↓
slice_alignment(start_ms, end_ms)
    ↓
stage4_shorts.run(info, sliced_alignment)
    ├─ extract clip WAV
    ├─ group_words_into_lines()
    ├─ precompute_waveform_frames()  ← synced visualizer
    ├─ render PNG frames
    ├─ ffmpeg encode
    └─ shorts_debug.write_report()
    ↓
outputs/{title}_shorts.mp4
```

## Module map

```
pipeline/
├── stage4_shorts.py       # Entry: run(info, alignment)
├── shorts_background.py   # Starfield + smoke
├── shorts_player_ui.py    # Title, visualizer slot, controls, progress
├── shorts_visualizer.py   # audio_sync: per-frame bars
├── shorts_lyrics.py       # Line layout + blue karaoke
├── shorts_preview.py      # Telegram preview frame
├── shorts_debug.py        # JSON debug reports
├── segment_picker.py      # Clip window selection
└── lyrics_sections.py     # [Chorus] / [Short: …] parsing

worker.py                  # Branches on output_format
bot.py                     # fmt:short / fmt:full callbacks
db.py                      # clip_* columns, shorts_debug_path
config.py                  # SHORTS_* constants
```

## Data passed to `stage4_shorts.run()`

```python
info = {
    "job_id": 14,
    "title": "My Body Isn't Ready",
    "artist": "sombr",
    "audio_path": Path("temp/....mp3"),
    "duration": 217.3,
    "clip_start_ms": 54259,
    "clip_end_ms": 75220,
    "clip_label": "Chorus",
    "clip_source": "chorus_label",
}
alignment = [  # already sliced, timestamps rebased to 0
    {"word": "I", "start_ms": 0, "end_ms": 120},
    ...
]
```

## Shared with full pipeline

- `stage1_download` — audio download (preview phase)
- `stage2_demucs` — vocal isolation
- `stage3_transcribe` — Whisper word alignment
- `lyrics_hint.py` — spelling hints (sections parsed separately)
- `stage5_upload` — optional; adds `#Shorts` tags

## Experiment source (`xpt/`)

| xpt | → pipeline |
|-----|------------|
| `background.py` | `shorts_background.py` |
| `player_ui.py` | `shorts_player_ui.py` |
| `audio_sync.py` | `shorts_visualizer.py` |
| `lyric_layout.py` | `shorts_lyrics.py` |
| `shorts_render.py` | `stage4_shorts.py` |