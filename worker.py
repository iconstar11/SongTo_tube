import logging
import asyncio
import os
import subprocess
from pathlib import Path

import db
from pipeline import stage1_download, stage2_demucs, stage3_transcribe, stage4_render, stage5_upload, background_fetcher
from pipeline.preview_background import build_preview
from pipeline.stage5_upload import youtube_configured
from config import TELEGRAM_BOT_TOKEN, TEMP_DIR
import telegram_ui as ui
from telegram import Bot, InputMediaPhoto
from telegram.error import BadRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Worker")


def _bot() -> Bot | None:
    if not TELEGRAM_BOT_TOKEN:
        return None
    return Bot(token=TELEGRAM_BOT_TOKEN)


def _probe_audio_duration(audio_path: Path) -> float:
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
        logger.warning(f"Could not probe audio duration for {audio_path}: {e}")
        return 180.0


async def update_job_message(job: dict, text: str):
    chat_id = job.get("telegram_chat_id")
    message_id = job.get("telegram_message_id")
    bot = _bot()
    if not bot or not chat_id or not message_id:
        return
    try:
        await bot.edit_message_text(
            chat_id=int(chat_id),
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"Could not edit job message: {e}")
    except Exception as e:
        logger.error(f"Edit message error: {e}")


async def send_preview_photo(
    job: dict,
    preview_path: Path,
    title: str,
    artist: str,
    *,
    is_retry: bool = False,
) -> int | None:
    bot = _bot()
    chat_id = job.get("telegram_chat_id")
    if not bot or not chat_id:
        return None

    caption = ui.format_preview_caption(job["id"], title, artist)
    keyboard = ui.bg_approval_keyboard(job["id"])
    preview_msg_id = job.get("preview_message_id")

    if is_retry and preview_msg_id:
        try:
            with open(preview_path, "rb") as f:
                await bot.edit_message_media(
                    chat_id=int(chat_id),
                    message_id=int(preview_msg_id),
                    media=InputMediaPhoto(media=f, caption=caption, parse_mode="HTML"),
                    reply_markup=keyboard,
                )
            return int(preview_msg_id)
        except BadRequest as e:
            logger.warning(f"Could not edit preview photo, sending new: {e}")
        except Exception as e:
            logger.warning(f"Edit preview photo error, sending new: {e}")

    try:
        with open(preview_path, "rb") as f:
            msg = await bot.send_photo(
                chat_id=int(chat_id),
                photo=f,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        return msg.message_id
    except Exception as e:
        logger.error(f"Send preview photo error: {e}")
        return None


async def send_video_to_chat(chat_id: str, video_path: str, caption: str) -> bool:
    bot = _bot()
    if not bot or not chat_id:
        return False
    try:
        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        if file_size_mb > 50.0:
            await bot.send_message(
                chat_id=int(chat_id),
                text=(
                    f"Video ready ({file_size_mb:.1f} MB) but exceeds Telegram's 50 MB limit.\n"
                    f"Local path: <code>{video_path}</code>"
                ),
                parse_mode="HTML",
            )
            return False

        with open(video_path, "rb") as f:
            await bot.send_video(
                chat_id=int(chat_id),
                video=f,
                caption=caption,
                parse_mode="HTML",
                write_timeout=120,
                read_timeout=120,
                connect_timeout=120,
            )
        return True
    except Exception as e:
        logger.error(f"Send video error: {e}")
        try:
            await bot.send_message(
                chat_id=int(chat_id),
                text=f"Failed to send video: <code>{e}</code>\nLocal: <code>{video_path}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return False


async def _progress(job: dict, stage_key: str, title: str = None, artist: str = None):
    text = ui.format_progress(
        job["id"],
        title or job.get("title"),
        artist or job.get("artist"),
        job.get("color_overlay"),
        stage_key,
    )
    await update_job_message(job, text)


async def run_preview(job: dict):
    job_id = job["id"]
    url = job["url"]
    retry_count = int(job.get("background_retry_count") or 0)
    is_retry = retry_count > 0 and job.get("audio_path") and job.get("title")

    try:
        await update_job_message(
            job,
            ui.format_fetching_preview(job_id, url, job.get("lyrics_mode")),
        )

        if is_retry:
            info = {
                "url": url,
                "title": job["title"],
                "artist": job["artist"],
                "audio_path": Path(job["audio_path"]),
            }
            logger.info(f"Job #{job_id}: fetching new background (retry #{retry_count})")
        else:
            info = stage1_download.run(url, title=job.get("title"), artist=job.get("artist"))

        seen_urls = db.get_seen_background_urls(job)
        exclude_hashes: list[str] = []
        if is_retry:
            prev_bg = job.get("background_source_path")
            if prev_bg:
                prev_path = Path(prev_bg)
                if prev_path.exists():
                    exclude_hashes.append(background_fetcher.file_hash(prev_path))

        bg_path, bg_url = background_fetcher.run(
            info["title"],
            info["artist"],
            attempt=retry_count,
            exclude_urls=seen_urls,
            exclude_hashes=exclude_hashes,
            job_id=job_id,
        )
        bg_source = Path(bg_path) if bg_path else None

        if bg_url:
            norm_new = background_fetcher.normalize_url(bg_url)
            if not any(background_fetcher.normalize_url(u) == norm_new for u in seen_urls):
                seen_urls.append(bg_url)

        preview_path = TEMP_DIR / f"job_{job_id}_preview.png"
        build_preview(bg_source, preview_path)

        db.save_preview_assets(
            job_id,
            info["title"],
            info["artist"],
            str(info["audio_path"]),
            str(bg_source) if bg_source else None,
            str(preview_path),
            seen_urls=seen_urls,
        )
        job.update({
            "title": info["title"],
            "artist": info["artist"],
            "audio_path": str(info["audio_path"]),
            "background_source_path": str(bg_source) if bg_source else None,
        })

        preview_msg_id = await send_preview_photo(
            job, preview_path, info["title"], info["artist"], is_retry=is_retry,
        )
        if preview_msg_id:
            db.set_preview_message(job_id, preview_msg_id)

        await update_job_message(
            job,
            (
                f"<b>Job #{job_id}</b> — preview ready\n"
                f"🎵 {info['artist']} — {info['title']}\n\n"
                f"📷 Background image sent above.\n"
                f"Tap <b>Approve</b> or <b>New Image</b> on the photo."
            ),
        )

    except Exception as e:
        logger.exception(f"Preview failed for Job #{job_id}")
        db.update_job_status(job_id, "FAILED", error=str(e))
        await update_job_message(job, ui.format_failed(job_id, "preview", str(e)))


async def run_pipeline(job: dict):
    job_id = job["id"]
    current_stage = "starting"

    try:
        info = {
            "url": job["url"],
            "title": job["title"],
            "artist": job["artist"],
            "audio_path": Path(job["audio_path"]),
            "background_path": job.get("background_source_path"),
            "color_overlay": job.get("color_overlay"),
            "duration": _probe_audio_duration(Path(job["audio_path"])),
        }

        current_stage = "demucs"
        db.update_job_status(job_id, "DEMUCS")
        await _progress(job, "DEMUCS", info["title"], info["artist"])
        vocals_path = stage2_demucs.run(info["audio_path"])

        current_stage = "transcribe"
        db.update_job_status(job_id, "TRANSCRIBING")
        await _progress(job, "TRANSCRIBING", info["title"], info["artist"])
        alignment = stage3_transcribe.run(
            vocals_path,
            info["title"],
            info["artist"],
            lyrics_path=job.get("lyrics_path"),
        )

        current_stage = "render"
        db.update_job_status(job_id, "RENDERING")
        await _progress(job, "RENDERING", info["title"], info["artist"])
        video_path = stage4_render.run(info, alignment)

        current_stage = "deliver"
        await _progress(job, "DELIVERING", info["title"], info["artist"])
        caption = (
            f"🎥 <b>Job #{job_id}</b>\n"
            f"{info['artist']} — {info['title']}\n"
            f"🎨 {ui.overlay_label(job.get('color_overlay'))}"
        )
        telegram_sent = await send_video_to_chat(job.get("telegram_chat_id"), str(video_path), caption)

        youtube_result = {"ok": False, "skipped": True, "reason": "disabled"}
        if youtube_configured():
            current_stage = "youtube"
            db.update_job_status(job_id, "UPLOADING", video_path=str(video_path))
            await _progress(job, "UPLOADING", info["title"], info["artist"])
            youtube_result = stage5_upload.run(video_path, info)

            if youtube_result.get("ok"):
                db.update_job_status(
                    job_id, "UPLOADING",
                    youtube_video_id=youtube_result["video_id"],
                    youtube_status="uploaded",
                )
            elif youtube_result.get("skipped"):
                db.update_job_status(job_id, "UPLOADING", youtube_status=f"skipped:{youtube_result.get('reason')}")
            else:
                db.update_job_status(job_id, "UPLOADING", youtube_status=f"failed:{youtube_result.get('reason')}")

        db.update_job_status(job_id, "COMPLETED", video_path=str(video_path))
        summary = ui.format_complete(
            job_id, info["title"], info["artist"],
            job.get("color_overlay"), telegram_sent, youtube_result,
        )
        await update_job_message(job, summary)

    except Exception as e:
        logger.exception(f"Pipeline failed for Job #{job_id}")
        db.update_job_status(job_id, "FAILED", error=str(e))
        await update_job_message(job, ui.format_failed(job_id, current_stage, str(e)))


async def main():
    if youtube_configured():
        logger.info("YouTube upload is enabled.")
    else:
        logger.info("YouTube upload is disabled or not configured.")

    logger.info("Worker started. Checking for jobs...")
    while True:
        job = db.get_next_job()
        if job:
            if job["status"] == "FETCHING_PREVIEW":
                logger.info(f"Fetching preview for Job #{job['id']}")
                await run_preview(job)
            else:
                logger.info(f"Processing Job #{job['id']}")
                await run_pipeline(job)
        else:
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())