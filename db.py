import json
import sqlite3
import threading
from pathlib import Path
from config import BASE_DIR

DB_PATH = BASE_DIR / "jobs.db"
_lock = threading.Lock()

CANCELLABLE_STATUSES = (
    "PENDING_LYRICS",
    "PENDING_LYRICS_INPUT",
    "FETCHING_PREVIEW",
    "PENDING_BG_APPROVAL",
    "PENDING_STYLE",
)

_JOB_COLUMNS = {
    "color_overlay": "TEXT",
    "telegram_chat_id": "TEXT",
    "telegram_message_id": "INTEGER",
    "preview_message_id": "INTEGER",
    "youtube_video_id": "TEXT",
    "youtube_status": "TEXT",
    "preview_image_path": "TEXT",
    "background_source_path": "TEXT",
    "audio_path": "TEXT",
    "lyrics_path": "TEXT",
    "lyrics_mode": "TEXT",
    "background_retry_count": "INTEGER DEFAULT 0",
    "background_seen_urls": "TEXT",
    "shorts_paths": "TEXT",
    "post_status": "TEXT DEFAULT 'to_post'",
    "posted_at": "TIMESTAMP",
}


def get_seen_background_urls(job: dict) -> list[str]:
    raw = job.get("background_seen_urls")
    if not raw:
        return []
    try:
        urls = json.loads(raw)
        return urls if isinstance(urls, list) else []
    except json.JSONDecodeError:
        return []


def init_db():
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    title TEXT,
                    artist TEXT,
                    color_overlay TEXT,
                    telegram_chat_id TEXT,
                    telegram_message_id INTEGER,
                    preview_message_id INTEGER,
                    youtube_video_id TEXT,
                    youtube_status TEXT,
                    preview_image_path TEXT,
                    background_source_path TEXT,
                    audio_path TEXT,
                    lyrics_path TEXT,
                    lyrics_mode TEXT,
                    status TEXT DEFAULT 'PENDING_LYRICS',
                    error TEXT,
                    video_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for col, col_type in _JOB_COLUMNS.items():
                try:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()


def add_job(
    url: str,
    title: str = None,
    artist: str = None,
    color_overlay: str = None,
    status: str = "PENDING_LYRICS",
    telegram_chat_id: str = None,
    telegram_message_id: int = None,
) -> int:
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                """
                INSERT INTO jobs (
                    url, title, artist, color_overlay, status,
                    telegram_chat_id, telegram_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (url, title, artist, color_overlay, status, telegram_chat_id, telegram_message_id),
            )
            return cursor.lastrowid


def get_job(job_id: int) -> dict | None:
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row else None


def set_job_telegram(job_id: int, chat_id: str, message_id: int):
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET telegram_chat_id = ?, telegram_message_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (str(chat_id), message_id, job_id),
            )


def skip_lyrics(job_id: int) -> bool:
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET lyrics_mode = 'none', status = 'FETCHING_PREVIEW', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'PENDING_LYRICS'
                """,
                (job_id,),
            )
            return cursor.rowcount > 0


def request_lyrics_input(job_id: int) -> bool:
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'PENDING_LYRICS_INPUT', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'PENDING_LYRICS'
                """,
                (job_id,),
            )
            return cursor.rowcount > 0


def save_lyrics(job_id: int, lyrics_path: str) -> bool:
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET lyrics_path = ?, lyrics_mode = 'hint', status = 'FETCHING_PREVIEW',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'PENDING_LYRICS_INPUT'
                """,
                (lyrics_path, job_id),
            )
            return cursor.rowcount > 0


def get_pending_lyrics_input_job(chat_id: str) -> dict | None:
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM jobs
                WHERE telegram_chat_id = ? AND status = 'PENDING_LYRICS_INPUT'
                ORDER BY id DESC LIMIT 1
                """,
                (str(chat_id),),
            ).fetchone()
            return dict(row) if row else None


def set_preview_message(job_id: int, preview_message_id: int):
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET preview_message_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (preview_message_id, job_id),
            )


def save_preview_assets(
    job_id: int,
    title: str,
    artist: str,
    audio_path: str,
    background_source_path: str | None,
    preview_image_path: str,
    seen_urls: list[str] | None = None,
):
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                UPDATE jobs SET
                    title = ?,
                    artist = ?,
                    audio_path = ?,
                    background_source_path = ?,
                    preview_image_path = ?,
                    background_seen_urls = ?,
                    status = 'PENDING_BG_APPROVAL',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    title,
                    artist,
                    audio_path,
                    background_source_path,
                    preview_image_path,
                    json.dumps(seen_urls) if seen_urls else None,  # background_seen_urls
                    job_id,
                ),
            )


def approve_background(job_id: int) -> bool:
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'PENDING_STYLE', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'PENDING_BG_APPROVAL'
                """,
                (job_id,),
            )
            return cursor.rowcount > 0


def request_new_background(job_id: int) -> bool:
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'FETCHING_PREVIEW',
                    background_retry_count = COALESCE(background_retry_count, 0) + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'PENDING_BG_APPROVAL'
                """,
                (job_id,),
            )
            return cursor.rowcount > 0


def confirm_job_overlay(job_id: int, overlay: str) -> bool:
    overlay_val = None if overlay == "none" else overlay
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET color_overlay = ?, status = 'QUEUED', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'PENDING_STYLE'
                """,
                (overlay_val, job_id),
            )
            return cursor.rowcount > 0


def cancel_job(job_id: int) -> bool:
    placeholders = ",".join("?" for _ in CANCELLABLE_STATUSES)
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                f"""
                UPDATE jobs
                SET status = 'CANCELLED', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status IN ({placeholders})
                """,
                (job_id, *CANCELLABLE_STATUSES),
            )
            return cursor.rowcount > 0


def update_job_status(
    job_id: int,
    status: str,
    error: str = None,
    video_path: str = None,
    title: str = None,
    artist: str = None,
    youtube_video_id: str = None,
    youtube_status: str = None,
):
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                UPDATE jobs SET
                    status = ?,
                    error = ?,
                    video_path = ?,
                    title = COALESCE(?, title),
                    artist = COALESCE(?, artist),
                    youtube_video_id = COALESCE(?, youtube_video_id),
                    youtube_status = COALESCE(?, youtube_status),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, error, video_path, title, artist, youtube_video_id, youtube_status, job_id),
            )


def get_next_job():
    """Preview fetch jobs take priority over full render jobs."""
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = 'FETCHING_PREVIEW' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row:
                return dict(row)
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = 'QUEUED' ORDER BY created_at LIMIT 1"
            ).fetchone()
            return dict(row) if row else None


def save_shorts_paths(job_id: int, paths: list[str]) -> None:
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET shorts_paths = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (json.dumps(paths), job_id),
            )


def mark_job_posted(job_id: int, video_path: str | None, shorts_paths: list[str] | None) -> None:
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET video_path = COALESCE(?, video_path),
                    shorts_paths = COALESCE(?, shorts_paths),
                    post_status = 'posted',
                    posted_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    video_path,
                    json.dumps(shorts_paths) if shorts_paths is not None else None,
                    job_id,
                ),
            )


def get_recent_jobs(chat_id: str = None, limit: int = 8) -> list[dict]:
    with _lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            if chat_id:
                rows = conn.execute(
                    """
                    SELECT * FROM jobs
                    WHERE telegram_chat_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (str(chat_id), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]