# Shorts Debugging Playbook

**Start every Shorts investigation with:**

```
xpt/job_{id}_shorts_bundle.json     # experiment (3 shorts)
temp/job_{id}_shorts_debug.json     # production (single short, post-integration)
```

If missing, check `SHORTS_DEBUG=true` in `.env` and that `output_format=short`.

---

## 1. Debug artifact checklist

| File | What it tells you |
|------|-------------------|
| `temp/job_{id}_shorts_debug.json` | Clip window, lines, render stats, warnings |
| `temp/job_{id}_sections.json` | Parsed `[Chorus]` / `[Short: …]` from user lyrics |
| `temp/{stem}_vocals_alignment.json` | Full Whisper alignment (before slice) |
| `temp/job_{id}_clip.wav` | Audio actually rendered |
| `temp/job_{id}_shorts_frames/` | Sample PNGs (if `SHORTS_DEBUG_SAVE_FRAMES=true`) |
| `outputs/{title}_shorts.mp4` | Final output |

---

## 2. Debug JSON schema

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
    {
      "text": "Like you but my body",
      "start_s": 0.0,
      "end_s": 2.1,
      "center_y": 457
    }
  ],
  "render": {
    "fps": 30,
    "frames": 629,
    "resolution": "720x1280",
    "output": "outputs/My_Body_Isnt_Ready_shorts.mp4",
    "elapsed_s": 72.4
  },
  "warnings": [
    "clip_shorter_than_target_35s"
  ]
}
```

### `clip.source` values

| Value | Meaning |
|-------|---------|
| `user_tag` | `[Short: …]` in lyrics |
| `user_time_range` | `[Short: 0:45-1:30]` |
| `chorus_label` | Whisper/user `Chorus` section |
| `chorus_match` | Lyric text matched to alignment |
| `repeat_hook` | Repeated phrase detection |
| `energy_peak` | RMS peak on vocals |
| `fallback` | Middle-third window |

---

## 3. Common failures

### Wrong clip section

**Symptoms:** Short uses verse instead of chorus.

**Check:**
1. `job_{id}_sections.json` — did user tag `[Short: Chorus]`?
2. `shorts_debug.json` → `clip.source` and `clip.start_ms`
3. Grep alignment for `Chorus`:

```bash
rg -i "chorus" "temp/*_vocals_alignment.json"
```

**Fix:** User adds `[Short: Chorus]` or manual range. Re-queue job.

---

### Lyrics out of sync

**Symptoms:** Blue highlight early/late vs audio.

**Check:**
1. `lines[].start_s` / `end_s` in debug JSON
2. Compare clip start to alignment slice — timestamps must be **rebased to 0**
3. Full song offset: `clip.start_ms` in debug vs first word `start_ms` in sliced alignment

**Commands:**

```bash
ffprobe -show_entries format=duration temp/job_14_clip.wav
python -m pipeline.shorts_debug --job 14 --dump-lines
```

**Fix:** Tune `gap_break_s` / `max_words_per_line` in `shorts_lyrics.py`.

---

### Visualizer solid white block

**Symptoms:** Top strip is a flat white rectangle, not bars.

**Check:**
1. `shorts_debug.json` → `render.warnings` for `visualizer_saturated`
2. Inspect saved frame: `temp/job_{id}_shorts_frames/frame_000150.png`
3. Global peak in visualizer — may need lower `SHORTS_VIZ_PEAK_PERCENTILE`

**Fix:** Adjust `shorts_visualizer.py` bar count / sqrt scaling (see xpt `audio_sync.py`).

---

### Clip too short / too long

**Symptoms:** &lt; 15 s or &gt; 55 s.

**Check:** `clip.duration_s` in debug JSON.

**Fix:** `segment_picker` expand/shrink at gap breaks. Tune `SHORTS_MIN_S` / `SHORTS_MAX_S`.

---

### Render slow

**Symptoms:** &gt; 3 min for 30 s clip.

**Check:** `render.elapsed_s` and `render.frames` in debug JSON.

**Mitigations:**
- Lower `SHORTS_FPS` to 24 for dev
- Parallel PNG write (future)
- FFmpeg direct drawtext path (future)

---

### Telegram video not sent

**Symptoms:** User gets path message only.

**Check:** File size &gt; 50 MB (unlikely for Shorts). `render.output` path exists.

```bash
ls -la outputs/*_shorts.mp4
```

---

## 4. Reproduce from xpt experiment

Compare pipeline output to known-good experiment:

```bash
python xpt/shorts_render.py --pipeline --job 14
# → xpt/My_Body_Isnt_Ready_shorts.mp4

python -m pipeline.stage4_shorts --job 14
# → outputs/My_Body_Isnt_Ready_shorts.mp4
```

Extract frames at same timestamps:

```bash
ffmpeg -ss 6 -i xpt/My_Body_Isnt_Ready_shorts.mp4 -frames:v 1 /tmp/xpt_t6.png
ffmpeg -ss 6 -i outputs/My_Body_Isnt_Ready_shorts.mp4 -frames:v 1 /tmp/pipe_t6.png
```

---

## 5. Enable verbose frame dumps

```env
SHORTS_DEBUG=true
SHORTS_DEBUG_SAVE_FRAMES=true
```

Re-run job. Inspect:

```
temp/job_{id}_shorts_frames/
  frame_000000.png   # t=0
  frame_000315.png   # ~middle
  frame_last.png
```

---

## 6. Log grep patterns

```bash
rg "\[pipeline\]|\[sync\]|\[render\]|\[encode\]|shorts_debug" worker.log
rg "clip_start|clip_end|segment_picker" worker.log
```

---

## 7. Escalation template

When filing an issue, attach:

1. `job_{id}_shorts_debug.json`
2. `job_{id}_sections.json` (if lyrics provided)
3. First 50 lines of `{stem}_vocals_alignment.json` around clip window
4. `ffprobe` output of `job_{id}_clip.wav`
5. One screenshot at t=50% duration

---

## 8. Related docs

- [SEGMENT_PICKER.md](SEGMENT_PICKER.md) — clip selection deep dive
- [RENDER_SPEC.md](RENDER_SPEC.md) — layout coordinates
- [../xpt/INTEGRATION_PLAN.md](../../xpt/INTEGRATION_PLAN.md) — implementation PR order