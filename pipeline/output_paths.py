"""Output directory layout: posted/ vs to_post/ with video and shorts subfolders."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import config

SHORTS_SLOT_SUFFIX = {
    "chorus": "1_chorus",
    "improv_a": "2_improv",
    "improv_b": "3_improv",
}


def video_filename(title: str) -> str:
    return f"{title}_7clouds.mp4"


def shorts_filename(title: str, slot_suffix: str) -> str:
    return f"{title}_shorts_{slot_suffix}.mp4"


def to_post_video_path(title: str) -> Path:
    config.ensure_output_dirs()
    return config.OUTPUT_TO_POST_VIDEO / video_filename(title)


def to_post_shorts_path(title: str, slot_suffix: str) -> Path:
    config.ensure_output_dirs()
    return config.OUTPUT_TO_POST_SHORTS / shorts_filename(title, slot_suffix)


def _posted_dest(path: Path) -> Path:
    path = Path(path)
    parts = path.as_posix().replace("\\", "/")
    if "/to_post/video/" in parts or parts.endswith("/to_post/video/" + path.name):
        return config.OUTPUT_POSTED_VIDEO / path.name
    if "/to_post/shorts/" in parts:
        return config.OUTPUT_POSTED_SHORTS / path.name
    if path.parent.name == "video" and "to_post" in str(path.parent.parent):
        return config.OUTPUT_POSTED_VIDEO / path.name
    if path.parent.name == "shorts" and "to_post" in str(path.parent.parent):
        return config.OUTPUT_POSTED_SHORTS / path.name
    raise ValueError(f"Path is not under to_post: {path}")


def is_to_post_path(path: Path | str) -> bool:
    return "to_post" in Path(path).as_posix().replace("\\", "/")


def mark_posted(path: Path | str) -> Path:
    """Move a single file from to_post/ to posted/."""
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(f"Missing output file: {src}")
    dest = _posted_dest(src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    shutil.move(str(src), str(dest))
    return dest


def get_job_output_paths(job: dict) -> list[Path]:
    paths: list[Path] = []
    if job.get("video_path"):
        paths.append(Path(job["video_path"]))
    raw = job.get("shorts_paths")
    if raw:
        try:
            for p in json.loads(raw):
                paths.append(Path(p))
        except (json.JSONDecodeError, TypeError):
            pass
    return [p for p in paths if p.exists()]


def move_job_outputs_to_posted(job: dict) -> list[Path]:
    """Move all job outputs from to_post/ to posted/."""
    moved: list[Path] = []
    for src in get_job_output_paths(job):
        if is_to_post_path(src):
            moved.append(mark_posted(src))
    return moved


def rewrite_path_to_posted(old_path: str) -> str | None:
    """Rewrite a DB path from to_post/ → posted/ (after filesystem move)."""
    if not old_path:
        return None
    p = Path(old_path)
    try:
        return str(_posted_dest(p))
    except ValueError:
        return old_path