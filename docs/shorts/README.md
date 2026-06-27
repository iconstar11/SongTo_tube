# YouTube Shorts — Operator Guide

Shorts mode produces **9:16 player-style lyric clips** (15–55 s), usually from the chorus. Full 16:9 videos are unchanged.

## Quick start (after integration)

1. Paste YouTube URL in Telegram
2. Tap **YouTube Short**
3. Paste lyrics (optional) — use `[Short: Chorus]` or `[Chorus]`
4. Wait for render (~1–2 min for a 30 s clip)
5. Receive **3 shorts** per song:
   - `{title}_shorts_1_chorus.mp4`
   - `{title}_shorts_2_improv.mp4`
   - `{title}_shorts_3_improv.mp4`

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_SHORTS` | `true` | Show Shorts button in bot |
| `SHORTS_DEBUG` | `true` | Write `job_{id}_shorts_debug.json` |
| `SHORTS_DEBUG_SAVE_FRAMES` | `false` | Keep PNG frames in `temp/job_{id}_shorts_frames/` |
| `SHORTS_MIN_S` | `15` | Minimum clip length |
| `SHORTS_MAX_S` | `55` | Maximum clip length |
| `SHORTS_TARGET_S` | `35` | Preferred clip length |
| `SHORTS_WIDTH` | `720` | Output width |
| `SHORTS_HEIGHT` | `1280` | Output height |
| `SHORTS_FPS` | `30` | Frame rate |

## Lyrics markers

| Marker | Effect |
|--------|--------|
| `[Short: Chorus]` | Use this section (highest priority) |
| `[Short: 0:45-1:30]` | Manual time range |
| `[Chorus]` | Auto-pick chorus if no Short tag |
| `[Verse 2]` | Stored for matching; not auto-selected |

Audio timing always wins over pasted lyrics.

## Output files

| Path | Description |
|------|-------------|
| `outputs/{title}_shorts.mp4` | Final video |
| `temp/job_{id}_clip.wav` | Trimmed audio clip |
| `temp/job_{id}_shorts_debug.json` | **Debug report** — start here |
| `temp/job_{id}_sections.json` | Parsed lyric sections |

## Offline re-render

```bash
python -m pipeline.stage4_shorts --job 14
python -m pipeline.segment_picker --job 14 --print
```

## More docs

- [ARCHITECTURE.md](ARCHITECTURE.md) — how modules connect
- [DEBUG.md](DEBUG.md) — troubleshooting playbook
- [SEGMENT_PICKER.md](SEGMENT_PICKER.md) — clip selection logic
- [RENDER_SPEC.md](RENDER_SPEC.md) — visual layout spec

## Experiment reference

Pre-integration prototype lives in `xpt/`. Compare:

```bash
python xpt/shorts_render.py --pipeline --job 14
```