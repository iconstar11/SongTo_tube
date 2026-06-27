# Segment Picker — Clip Selection

Chooses **3 clips (15–55 s each)** per full song:

| Slot | Role |
|------|------|
| `chorus` | Short 1 — chorus section or strongest hook |
| `improv_a` | Short 2 — improvised (energy peak / opening moment) |
| `improv_b` | Short 3 — improvised (bridge / late moment / 2nd chorus) |

Outputs: `{title}_shorts_1_chorus.mp4`, `_2_improv.mp4`, `_3_improv.mp4`

## Priority order

```
1. [Short: …] user tag
2. [Short: M:SS-M:SS] manual range
3. [Chorus] lyric section → text match
4. Whisper "Chorus" label in alignment
5. Repeated hook (2nd/3rd occurrence)
6. Energy peak on vocals stem
7. Fallback: middle third, gap-snapped
```

## Inputs

| Input | Source |
|-------|--------|
| `alignment` | `stage3_transcribe` full song |
| `vocals_path` | Demucs output |
| `sections` | `job_{id}_sections.json` |
| `duration_s` | Full audio length |

## Output: `ClipWindow`

```python
@dataclass
class ClipWindow:
    start_ms: int
    end_ms: int
    duration_s: float
    source: str
    label: str
```

Saved to DB: `clip_start_ms`, `clip_end_ms`, `clip_source`, `clip_label`.

## Duration rules

```python
SHORTS_MIN_S = 15.0
SHORTS_MAX_S = 55.0
SHORTS_TARGET_S = 35.0
SHORTS_PAD_S = 0.5
```

After anchor match:
- **Too short** → extend to next `GAP_STRONG_BREAK_S` (1.2 s) up to `SHORTS_MAX_S`
- **Too long** → shrink from ends at strong gaps down to `SHORTS_MAX_S`

## Example: job #14 (experiment)

Song: **sombr — My Body Isn't Ready**

| Field | Value |
|-------|-------|
| Source | `chorus_label` (Whisper) |
| start_ms | 54259 |
| end_ms | 75220 |
| duration_s | 20.96 |
| Label | Chorus |

Lyrics in clip:

```
I like you but my body isn't ready
I want you but the mirror won't let me
…
I'm not ready
```

## User override examples

### Tag chorus explicitly

```
[Verse 1]
…

[Chorus]
I like you but my body isn't ready
…

[Short: Chorus]
```

### Manual time range

```
[Short: 0:54-1:15]
```

Parsed to `start_ms=54000`, `end_ms=75000`, snapped to word boundaries.

## Text matching algorithm

1. Tokenize section lines → word list
2. Tokenize alignment → word list
3. Sliding window best match (fuzzy apostrophes)
4. `start_ms` = first matched word − `SHORTS_PAD_S`
5. `end_ms` = last matched word + `SHORTS_PAD_S`

## Repeated hook detection

- Scan 4–8 word n-grams
- Score: `repeat_count × length`
- Pick **2nd or 3rd** occurrence (often better mix)

## Energy fallback

```bash
ffmpeg -i vocals.wav -ac 1 -ar 22050 -f f32le pipe:1
```

- RMS in 0.5 s hops, smoothed
- Center `SHORTS_TARGET_S` window on peak
- Snap to nearest word boundaries

## Debug

```bash
python -m pipeline.segment_picker --job 14 --print
```

Expected output:

```
source=chorus_label
label=Chorus
start=54.259s end=75.220s duration=20.961s
```

See [DEBUG.md](DEBUG.md) if pick looks wrong.