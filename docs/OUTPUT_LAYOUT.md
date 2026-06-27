# Output Folder Layout — Plan

> **Goal:** Split `outputs/` into **posted** (already published) and **to_post** (fresh from pipeline, awaiting publish).  
> New renders land in `to_post/video/` or `to_post/shorts/` depending on format.

---

## 1. Target directory tree

```
outputs/
├── posted/                          # Already published (manual or after upload)
│   ├── video/                       # Full 16:9 lyric videos
│   │   └── {title}_7clouds.mp4
│   └── shorts/                      # YouTube Shorts clips
│       ├── {title}_shorts_1_chorus.mp4
│       ├── {title}_shorts_2_improv.mp4
│       └── {title}_shorts_3_improv.mp4
│
└── to_post/                         # Pipeline writes here; not yet posted
    ├── video/
    │   └── {title}_7clouds.mp4
    └── shorts/
        ├── {title}_shorts_1_chorus.mp4
        ├── {title}_shorts_2_improv.mp4
        └── {title}_shorts_3_improv.mp4
```

**Rule:** Nothing new is written directly to `outputs/` root. Root only contains `posted/` and `to_post/`.

---

## 2. One-time migration (existing files)

Current flat files in `outputs/` (8 × `_7clouds.mp4`) → move to **`posted/video/`** because they were already delivered/posted.

| Current | After migration |
|---------|-----------------|
| `outputs/My Body Isn't Ready_7clouds.mp4` | `outputs/posted/video/My Body Isn't Ready_7clouds.mp4` |
| `outputs/*.mp4` (all full videos) | `outputs/posted/video/*.mp4` |

**Script:** `scripts/migrate_outputs_layout.py` (one-time)

```bash
python scripts/migrate_outputs_layout.py --dry-run
python scripts/migrate_outputs_layout.py
```

- Creates all subfolders
- Moves `outputs/*_7clouds.mp4` → `posted/video/`
- Moves `outputs/*_shorts*.mp4` → `posted/shorts/` (if any)
- Updates `jobs.db` `video_path` for COMPLETED jobs (path rewrite)

---

## 3. Pipeline write paths

| Format | Renderer | Output path |
|--------|----------|-------------|
| Full video | `stage4_render.py` | `outputs/to_post/video/{title}_7clouds.mp4` |
| Short #1 | `stage4_shorts.py` | `outputs/to_post/shorts/{title}_shorts_1_chorus.mp4` |
| Short #2 | `stage4_shorts.py` | `outputs/to_post/shorts/{title}_shorts_2_improv.mp4` |
| Short #3 | `stage4_shorts.py` | `outputs/to_post/shorts/{title}_shorts_3_improv.mp4` |

**xpt experiment** (until promoted): same paths under `outputs/to_post/shorts/` instead of `xpt/`.

---

## 4. Config helpers (`config.py`)

```python
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_POSTED = OUTPUT_DIR / "posted"
OUTPUT_TO_POST = OUTPUT_DIR / "to_post"
OUTPUT_POSTED_VIDEO = OUTPUT_POSTED / "video"
OUTPUT_POSTED_SHORTS = OUTPUT_POSTED / "shorts"
OUTPUT_TO_POST_VIDEO = OUTPUT_TO_POST / "video"
OUTPUT_TO_POST_SHORTS = OUTPUT_TO_POST / "shorts"

def ensure_output_dirs():
    for d in (
        OUTPUT_POSTED_VIDEO, OUTPUT_POSTED_SHORTS,
        OUTPUT_TO_POST_VIDEO, OUTPUT_TO_POST_SHORTS,
    ):
        d.mkdir(parents=True, exist_ok=True)

def to_post_video_path(title: str) -> Path:
    return OUTPUT_TO_POST_VIDEO / f"{sanitize(title)}_7clouds.mp4"

def to_post_shorts_path(title: str, slot: str) -> Path:
    # slot: 1_chorus | 2_improv | 3_improv
    return OUTPUT_TO_POST_SHORTS / f"{sanitize(title)}_shorts_{slot}.mp4"

def posted_video_path(title: str) -> Path:
    return OUTPUT_POSTED_VIDEO / f"{sanitize(title)}_7clouds.mp4"
```

Call `ensure_output_dirs()` at worker startup and in `config.py` import (like `OUTPUT_DIR.mkdir` today).

---

## 5. Lifecycle: `to_post` → `posted`

```
Pipeline render complete
    → files in outputs/to_post/{video|shorts}/
    → job status COMPLETED
    → Telegram delivers from to_post path

User posts to YouTube / TikTok manually OR stage5 auto-upload succeeds
    → move file(s) to outputs/posted/{video|shorts}/
    → update DB posted_at / youtube_video_id
```

### 5.1 Move triggers (pick one or combine)

| Trigger | When |
|---------|------|
| **A. Auto** | `stage5_upload` returns `ok=True` → move that file to `posted/` |
| **B. Manual** | Telegram `/posted {job_id}` or button "Mark posted" |
| **C. Batch** | CLI `python -m pipeline.mark_posted --job 14` |

**Recommended v1:** **B + A** — auto-move on YouTube upload; manual command for off-platform posts.

### 5.2 Move implementation

```python
# pipeline/output_paths.py

def mark_posted(path: Path) -> Path:
    """Move to_post → posted preserving video vs shorts subfolder."""
    path = Path(path)
    if "to_post/video" in path.as_posix():
        dest = OUTPUT_POSTED_VIDEO / path.name
    elif "to_post/shorts" in path.as_posix():
        dest = OUTPUT_POSTED_SHORTS / path.name
    else:
        raise ValueError(f"Not under to_post: {path}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(path, dest)
    return dest
```

For a Shorts job with 3 files: move all 3 when job marked posted.

---

## 6. Database changes

```sql
ALTER TABLE jobs ADD COLUMN posted_at TIMESTAMP;
ALTER TABLE jobs ADD COLUMN shorts_paths TEXT;  -- JSON array of 3 paths
ALTER TABLE jobs ADD COLUMN post_status TEXT DEFAULT 'to_post';
-- post_status: 'to_post' | 'posted' | 'partial'
```

| Column | Full video job | Shorts job |
|--------|----------------|------------|
| `video_path` | Primary deliverable (`to_post/video/...`) | First short or null |
| `shorts_paths` | null | `["...1_chorus.mp4", "...2_improv.mp4", "...3_improv.mp4"]` |
| `post_status` | `to_post` until moved | `to_post` until all 3 moved |
| `posted_at` | set on mark posted | set when bundle complete |

**`db.mark_job_posted(job_id)`** — sets `post_status='posted'`, `posted_at=now()`, rewrites paths in DB.

---

## 7. Files to change

| File | Change |
|------|--------|
| `config.py` | Path constants + `ensure_output_dirs()` + `sanitize_title()` |
| `pipeline/output_paths.py` | **New** — path builders, `mark_posted()`, migration helper |
| `pipeline/stage4_render.py` | `to_post_video_path(title)` instead of `OUTPUT_DIR / ...` |
| `pipeline/stage4_shorts.py` | `to_post_shorts_path()` (when integrated) |
| `xpt/shorts_render.py` | Write to `outputs/to_post/shorts/` during experiment |
| `worker.py` | Ensure dirs on start; deliver from `to_post`; call `mark_posted` after upload |
| `pipeline/stage5_upload.py` | On success → `mark_posted(video_path)` |
| `bot.py` | `/posted` command or callback; list `to_post` vs `posted` in `/status` |
| `db.py` | New columns + `shorts_paths` + `mark_job_posted()` |
| `scripts/migrate_outputs_layout.py` | **New** — one-time move + DB path fix |
| `docs/shorts/README.md` | Update output paths |
| `docs/shorts/DEBUG.md` | Update artifact paths |

---

## 8. Worker flow (full video)

```
stage4_render.run()
  → outputs/to_post/video/{title}_7clouds.mp4

send_video_to_chat(path)

if youtube upload ok:
  mark_posted(path)
  db.mark_job_posted(job_id)

else:
  post_status stays 'to_post' until manual /posted
```

## 9. Worker flow (shorts — 3 files)

```
stage4_shorts.run() × 3
  → outputs/to_post/shorts/{title}_shorts_{1,2,3}.mp4

send 3 videos to Telegram (or zip if > 50 MB)

on mark posted:
  move all 3 → posted/shorts/
  db.shorts_paths updated to new locations
```

---

## 9. Telegram UX

### `/status` addition

```
To post (2):
  #14 sombr — My Body Isn't Ready
    video: to_post/video/My Body Isn't Ready_7clouds.mp4
  #15 … (shorts bundle, 3 files)

Posted (8):
  … in posted/video/
```

### Mark posted

```
/posted 14
```

Moves all outputs for job #14 from `to_post/` → `posted/`.

---

## 10. Implementation order

| Step | Task |
|------|------|
| **1** | `config.py` + `pipeline/output_paths.py` + `ensure_output_dirs()` |
| **2** | `scripts/migrate_outputs_layout.py` — move existing 8 files to `posted/video/` |
| **3** | `stage4_render.py` → `to_post/video/` |
| **4** | `xpt/shorts_render.py` → `to_post/shorts/` |
| **5** | `db.py` columns + `mark_job_posted()` |
| **6** | `worker.py` + `stage5_upload` auto-move |
| **7** | `bot.py` `/posted` + status listing |
| **8** | Integrate `stage4_shorts` with same paths when promoted |

---

## 11. `.gitignore` (unchanged)

`outputs/` stays gitignored. Only folder structure is documented; videos are never committed.

---

## 12. Edge cases

| Case | Handling |
|------|----------|
| Re-render same title | Overwrite in `to_post/` (same filename) or add `_v2` suffix (config flag) |
| Job cancelled mid-render | Partial files in `to_post/` — cleanup on CANCELLED |
| Upload 1 of 3 shorts | `post_status='partial'` until all moved or user confirms |
| File missing on mark posted | Warning in debug JSON; don't fail silently |

---

## 13. Success criteria

- [ ] No new files in `outputs/` root
- [ ] Existing 8 videos live under `posted/video/`
- [ ] New full render → `to_post/video/`
- [ ] New shorts bundle → `to_post/shorts/` (3 files)
- [ ] `/posted` or YouTube upload moves files to `posted/`
- [ ] `jobs.db` paths match filesystem after move

---

*Related: [shorts/README.md](shorts/README.md), [xpt/INTEGRATION_PLAN.md](../xpt/INTEGRATION_PLAN.md)*