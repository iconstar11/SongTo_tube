# Shorts Render Spec

Visual spec for `stage4_shorts` — derived from `xpt/` reference (`#lyrics.mp4`) and validated experiment.

## Canvas

| Property | Value |
|----------|-------|
| Width | 720 px |
| Height | 1280 px |
| FPS | 30 |
| Codec | H.264 High, yuv420p, CRF 20 |
| Audio | AAC 128k, 44.1 kHz stereo |

## Colors

| Role | Hex | RGB |
|------|-----|-----|
| Background | `#000000` | 0,0,0 |
| Primary text | `#FAFAFA` | 250,250,250 |
| Active lyric | `#74A7D1` | 116,167,209 |
| UI white | `#FFFFFF` | 255,255,255 |
| Control ring | `#C8C8C8` | 200,200,200 |
| Progress track | `#D2D2D2` | 210,210,210 |

## Layout zones (Y coordinates)

```
y=0
  Header      flower @ (108,142), title @ (176,118)
  Waveform    box (60,198)–(660,308)
  Progress    y=318, x=96–624
  Controls    y=352, centers at cx±70
  Lyrics      y=457–1022 (dynamic line count)
  Stars       upper 82% of frame
  Smoke       bottom third
y=1280
```

## Typography

| Element | Font | Size |
|---------|------|------|
| Title | Segoe UI Bold | 34 px |
| Lyrics | Segoe UI | 27 px |

Sentence case: first character uppercased per line.

## Player chrome (decorative)

### Control circles

- Radius: **18 px**
- Spacing: **70 px** between centers
- Icons centered in circle:
  - **Prev:** double left chevron
  - **Pause:** two bars, 14 px tall, 6 px gap
  - **Next:** double right chevron

### Progress bar

- Playhead: 7 px radius white dot
- Position: `x = 96 + (t/duration) × 528`

## Synced visualizer

- **Not static** — one bar strip per frame
- Window: **4.0 s** centered on playhead
- **~120 bars**, width 2 px, sqrt amplitude scaling
- Global peak normalization across clip (prevents solid white saturation)

Implementation: `pipeline/shorts_visualizer.py` (from `xpt/audio_sync.py`).

## Karaoke lyrics

- **All lines visible** simultaneously
- **One line blue** at a time: `line.start_s <= t < line.end_s`
- Last line stays highlighted after `end_s`

### Line grouping

| Parameter | Value |
|-----------|-------|
| `max_words_per_line` | 5 |
| `gap_break_s` | 0.45 |
| `max_width_px` | 560 |

## Background

- Seed: `job_id` (reproducible stars)
- ~110 star particles, opacity 18–90
- 5 smoke blobs, Gaussian blur 28 px

## Output naming

```
outputs/{sanitized_title}_shorts.mp4
```

## Render performance (reference)

| Clip length | Frames | Time (approx) |
|-------------|--------|---------------|
| 21 s | 629 | ~70 s |
| 35 s | 1050 | ~120 s |

## Frame debug export

When `SHORTS_DEBUG_SAVE_FRAMES=true`:

```
temp/job_{id}_shorts_frames/frame_%06d.png
```

Compare to xpt reference:

```
xpt/frames/frame_003.png
xpt/static_test.png
```