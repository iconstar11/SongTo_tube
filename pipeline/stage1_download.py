import logging
import re
import subprocess
import time
from pathlib import Path

import yt_dlp

from config import TEMP_DIR

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_ATTEMPTS = 4
RETRY_BACKOFF_S = (2, 5, 10)
PLAYER_CLIENT_ATTEMPTS = (
    None,
    ["web"],
    ["ios"],
    ["android", "web"],
)


def clean_youtube_title(title: str) -> str:
    suffix_pattern = r"\s*[\(\[]\s*(?:lyrics?|lyric\s+video|official\s+video|official\s+audio|audio|video|official|hd|4k)\s*[\)\]]"
    cleaned = re.sub(suffix_pattern, "", title, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def clean_uploader_name(uploader: str) -> str:
    cleaned = re.sub(
        r"(?:VEVO|Official|Music|Studio|Records|Productions|Channel|Tv|Lyrics?)$",
        "",
        uploader,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"([a-z])([A-Z])", r"\1 \2", cleaned)
    return cleaned.strip()


def _probe_duration(audio_path: Path) -> float:
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(proc.stdout.strip())
    except Exception as e:
        print(f"Could not read duration of cached file: {e}")
        return 180.0


def _build_ydl_opts(player_clients: list[str] | None = None) -> dict:
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(TEMP_DIR / "%(title)s.%(ext)s"),
        "noplaylist": True,
        "js_runtimes": {"node": {}},
        "remote_components": {"ejs:github"},
        "retries": 3,
        "fragment_retries": 3,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }
    if player_clients:
        opts["extractor_args"] = {"youtube": {"player_client": player_clients}}
    return opts


def _is_retryable_download_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(token in msg for token in ("403", "forbidden", "429", "too many requests"))


def _download_info(url: str) -> dict:
    last_error = None
    for attempt, player_clients in enumerate(PLAYER_CLIENT_ATTEMPTS):
        if attempt > 0:
            wait_s = RETRY_BACKOFF_S[min(attempt - 1, len(RETRY_BACKOFF_S) - 1)]
            logger.warning(
                "yt-dlp download retry %s/%s after %ss (player_client=%s)",
                attempt + 1,
                MAX_DOWNLOAD_ATTEMPTS,
                wait_s,
                player_clients or "default",
            )
            time.sleep(wait_s)

        opts = _build_ydl_opts(player_clients)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as exc:
            last_error = exc
            if _is_retryable_download_error(exc) and attempt < MAX_DOWNLOAD_ATTEMPTS - 1:
                continue
            raise

    if last_error:
        raise last_error
    raise RuntimeError(f"Failed to download {url}")


def run(url: str, title: str = None, artist: str = None) -> dict:
    """Download audio and video info from YouTube."""
    if title:
        full_title = f"{artist} - {title}" if artist else title
        audio_path_full = TEMP_DIR / f"{full_title}.mp3"
        audio_path_title = TEMP_DIR / f"{title}.mp3"

        target_path = None
        if audio_path_full.exists():
            target_path = audio_path_full
        elif audio_path_title.exists():
            target_path = audio_path_title

        if target_path:
            print(f"Found cached audio file at {target_path}, skipping download.")
            return {
                "url": url,
                "title": title,
                "artist": artist or "Unknown Artist",
                "audio_path": target_path,
                "duration": _probe_duration(target_path),
                "thumbnail": None,
            }

    info = _download_info(url)
    raw_title = info.get("title", "Unknown Title")
    uploader = info.get("uploader", "Unknown Artist")

    title = clean_youtube_title(raw_title)
    artist = clean_uploader_name(uploader)
    if " - " in title:
        artist_part, title_part = title.split(" - ", 1)
        artist = artist_part.strip()
        title = title_part.strip()

    audio_path = TEMP_DIR / f"{info['title']}.mp3"

    return {
        "url": url,
        "title": title,
        "artist": artist,
        "audio_path": audio_path,
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
    }