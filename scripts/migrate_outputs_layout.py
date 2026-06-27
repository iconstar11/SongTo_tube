"""
One-time migration: flat outputs/*.mp4 → posted/{video|shorts}/ and rewrite jobs.db paths.

Usage:
    python scripts/migrate_outputs_layout.py --dry-run
    python scripts/migrate_outputs_layout.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import config
import db
from config import ensure_output_dirs


def _is_flat_output(path: Path) -> bool:
    try:
        return path.parent.resolve() == config.OUTPUT_DIR.resolve()
    except OSError:
        return path.parent == config.OUTPUT_DIR


def _posted_dest_for_name(name: str) -> Path:
    if "_shorts_" in name:
        return config.OUTPUT_POSTED_SHORTS / name
    if name.endswith("_7clouds.mp4"):
        return config.OUTPUT_POSTED_VIDEO / name
    raise ValueError(f"Unrecognized output filename: {name}")


def rewrite_flat_path(old_path: str) -> str | None:
    if not old_path:
        return None
    p = Path(old_path)
    if not _is_flat_output(p):
        return old_path
    try:
        return str(_posted_dest_for_name(p.name))
    except ValueError:
        return old_path


def collect_flat_mp4s() -> list[Path]:
    if not config.OUTPUT_DIR.exists():
        return []
    return sorted(
        p for p in config.OUTPUT_DIR.iterdir()
        if p.is_file() and p.suffix.lower() == ".mp4"
    )


def migrate_files(dry_run: bool) -> list[tuple[Path, Path]]:
    ensure_output_dirs()
    moves: list[tuple[Path, Path]] = []
    for src in collect_flat_mp4s():
        dest = _posted_dest_for_name(src.name)
        moves.append((src, dest))

    for src, dest in moves:
        if dest.exists():
            print(f"[skip] destination exists: {dest}")
            continue
        if dry_run:
            print(f"[dry-run] {src} -> {dest}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            print(f"[moved] {src.name} -> {dest.parent.name}/")
    return moves


def migrate_db(dry_run: bool) -> int:
    db.init_db()
    updated = 0
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, status, video_path, shorts_paths, post_status FROM jobs"
        ).fetchall()

        for row in rows:
            job_id = row["id"]
            new_video = rewrite_flat_path(row["video_path"] or "")
            new_shorts = None
            if row["shorts_paths"]:
                try:
                    paths = json.loads(row["shorts_paths"])
                    if isinstance(paths, list):
                        new_shorts = [rewrite_flat_path(p) for p in paths]
                except json.JSONDecodeError:
                    new_shorts = None

            changed = (
                (new_video and new_video != row["video_path"])
                or (new_shorts is not None and new_shorts != json.loads(row["shorts_paths"] or "[]"))
            )
            mark_posted = row["status"] == "COMPLETED" and (
                (new_video and new_video != (row["video_path"] or ""))
                or row["post_status"] in (None, "to_post")
            )

            if not changed and not (mark_posted and row["post_status"] != "posted"):
                continue

            if dry_run:
                if new_video and new_video != row["video_path"]:
                    print(f"[dry-run] job #{job_id} video_path: {row['video_path']} -> {new_video}")
                if mark_posted and row["post_status"] != "posted":
                    print(f"[dry-run] job #{job_id} post_status -> posted")
            else:
                conn.execute(
                    """
                    UPDATE jobs SET
                        video_path = COALESCE(?, video_path),
                        shorts_paths = COALESCE(?, shorts_paths),
                        post_status = CASE
                            WHEN ? THEN 'posted'
                            ELSE post_status
                        END,
                        posted_at = CASE
                            WHEN ? AND posted_at IS NULL THEN CURRENT_TIMESTAMP
                            ELSE posted_at
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        new_video if new_video != row["video_path"] else None,
                        json.dumps(new_shorts) if new_shorts is not None else None,
                        mark_posted,
                        mark_posted,
                        job_id,
                    ),
                )
                updated += 1

        if not dry_run:
            conn.commit()
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate flat outputs/ to posted/ layout")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without applying")
    args = parser.parse_args()

    flat = collect_flat_mp4s()
    print(f"Found {len(flat)} flat MP4(s) in {config.OUTPUT_DIR}")
    migrate_files(args.dry_run)
    n = migrate_db(args.dry_run)
    if args.dry_run:
        print("Dry run complete — no changes written.")
    else:
        print(f"Done. Updated {n} job row(s) in {db.DB_PATH}")


if __name__ == "__main__":
    main()