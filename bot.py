import logging
import re
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS, TEMP_DIR, ensure_output_dirs
from pipeline.output_paths import move_job_outputs_to_posted, rewrite_path_to_posted
from pipeline.color_overlay import PRODUCTION_OVERLAYS
from pipeline.lyrics_hint import save_lyrics_md
from pipeline.overlay_preview import build_overlay_comparison_grid, render_overlay_preview
import db
import telegram_ui as ui

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

YT_RE = re.compile(
    r"(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[\w\-]+"
)
OVERLAY_CALLBACK_RE = re.compile(r"^ovl:(\d+):([a-z_]+)$")
OVERLAY_USE_RE = re.compile(r"^ovluse:(\d+):([a-z_]+)$")
OVERLAY_BACK_RE = re.compile(r"^ovlback:(\d+)$")
CANCEL_CALLBACK_RE = re.compile(r"^cancel:(\d+)$")
BG_APPROVE_RE = re.compile(r"^bgapprove:(\d+)$")
BG_RETRY_RE = re.compile(r"^bgretry:(\d+)$")
LYR_HAVE_RE = re.compile(r"^lyrhave:(\d+)$")
LYR_SKIP_RE = re.compile(r"^lyrskip:(\d+)$")


def _authorized(chat_id: int | str) -> bool:
    return str(chat_id) in TELEGRAM_CHAT_IDS


def _job_background_paths(job: dict) -> tuple[Path | None, Path | None]:
    bg = Path(job["background_source_path"]) if job.get("background_source_path") else None
    preview = Path(job["preview_image_path"]) if job.get("preview_image_path") else None
    return bg, preview


async def _send_overlay_comparison_grid(query, job: dict):
    job_id = job["id"]
    grid_path = TEMP_DIR / f"job_{job_id}_overlay_grid.jpg"
    bg, preview = _job_background_paths(job)
    try:
        build_overlay_comparison_grid(bg, preview, grid_path)
        with open(grid_path, "rb") as f:
            await query.message.reply_photo(
                photo=f,
                caption=ui.format_overlay_grid_caption(job_id, job["title"], job["artist"]),
                parse_mode="HTML",
            )
    except Exception as e:
        logger.warning(f"Could not send overlay grid for job #{job_id}: {e}")


def _cancel_only_keyboard(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Cancel", callback_data=f"cancel:{job_id}")],
    ])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "<b>SongToTube</b> — lyric video generator\n\n"
        "1. Paste a YouTube link\n"
        "2. Optionally paste lyrics (hints for accuracy)\n"
        "3. Approve the background image\n"
        "4. Choose a mood overlay\n"
        "5. Receive your video (~15–30 min)\n\n"
        "Try /help or /status",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "<b>How to use</b>\n"
        "1. Paste any YouTube song link\n"
        "2. Tap <b>I have lyrics</b> or <b>Skip</b>\n"
        "   • Lyrics inspire spelling only — <b>audio wins</b> on words & timing\n"
        "   • Spelling doesn't need to be perfect\n"
        "3. Wait for the background preview photo\n"
        "4. Tap <b>Approve</b> or <b>New Image</b>\n"
        "5. Choose a mood overlay\n"
        "6. Receive the finished video\n"
        "7. After publishing: <code>/posted &lt;job_id&gt;</code>\n\n"
        "/status — your recent jobs",
        parse_mode="HTML",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update.effective_chat.id):
        return
    jobs = db.get_recent_jobs(chat_id=str(update.effective_chat.id))
    await update.message.reply_text(ui.format_status_list(jobs), parse_mode="HTML")


async def cmd_posted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark a completed job's outputs as posted (moves to_post/ → posted/)."""
    if not _authorized(update.effective_chat.id):
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: <code>/posted &lt;job_id&gt;</code>\n"
            "Moves video(s) from <code>to_post/</code> to <code>posted/</code>.",
            parse_mode="HTML",
        )
        return

    try:
        job_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Job ID must be a number.", parse_mode="HTML")
        return

    job = db.get_job(job_id)
    if not job:
        await update.message.reply_text(f"Job #{job_id} not found.", parse_mode="HTML")
        return

    if job.get("status") != "COMPLETED":
        await update.message.reply_text(
            f"Job #{job_id} is <b>{job['status']}</b> — only COMPLETED jobs can be marked posted.",
            parse_mode="HTML",
        )
        return

    if job.get("post_status") == "posted":
        await update.message.reply_text(f"Job #{job_id} is already marked posted.", parse_mode="HTML")
        return

    try:
        moved = move_job_outputs_to_posted(job)
        if not moved:
            await update.message.reply_text(
                f"No files in <code>to_post/</code> for job #{job_id}.",
                parse_mode="HTML",
            )
            return

        new_video = rewrite_path_to_posted(job.get("video_path") or "")
        shorts_raw = job.get("shorts_paths")
        new_shorts = None
        if shorts_raw:
            import json
            try:
                new_shorts = [rewrite_path_to_posted(p) for p in json.loads(shorts_raw)]
            except json.JSONDecodeError:
                new_shorts = None

        db.mark_job_posted(job_id, new_video if new_video else None, new_shorts)
        names = "\n".join(f"• <code>{p}</code>" for p in moved)
        await update.message.reply_text(
            f"✅ Job #{job_id} marked <b>posted</b> ({len(moved)} file(s)):\n{names}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception(f"mark posted failed for job #{job_id}")
        await update.message.reply_text(f"Failed: <code>{e}</code>", parse_mode="HTML")


async def _start_fetching_preview_message(query_or_update, job: dict, lyrics_mode: str | None = None):
    """Edit or send the fetching-preview status message."""
    text = ui.format_fetching_preview(job["id"], job["url"], lyrics_mode)
    if hasattr(query_or_update, "edit_message_text"):
        await query_or_update.edit_message_text(text, parse_mode="HTML", reply_markup=None)
        chat_id = query_or_update.message.chat_id
        message_id = query_or_update.message.message_id
    else:
        msg = await query_or_update.message.reply_text(text, parse_mode="HTML")
        chat_id = query_or_update.effective_chat.id
        message_id = msg.message_id
    db.set_job_telegram(job["id"], chat_id, message_id)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update.effective_chat.id):
        return

    text = (update.message.text or "").strip()
    chat_id = str(update.effective_chat.id)

    pending = db.get_pending_lyrics_input_job(chat_id)
    if pending and not YT_RE.search(text):
        if len(text) < 10:
            await update.message.reply_text(
                "Lyrics look too short. Paste the full lyrics text, or tap Cancel on the previous message.",
                parse_mode="HTML",
            )
            return

        lyrics_path = save_lyrics_md(
            pending["id"],
            pending.get("title") or "Unknown Title",
            pending.get("artist") or "Unknown Artist",
            text,
            TEMP_DIR,
        )
        if not db.save_lyrics(pending["id"], str(lyrics_path)):
            await update.message.reply_text("Could not save lyrics for this job.", parse_mode="HTML")
            return

        msg = await update.message.reply_text(
            ui.format_fetching_preview(pending["id"], pending["url"], "hint"),
            parse_mode="HTML",
        )
        db.set_job_telegram(pending["id"], chat_id, msg.message_id)
        return

    match = YT_RE.search(text)
    if not match:
        await update.message.reply_text(
            "Send a <b>YouTube link</b> to create a lyric video.\n"
            "Type /help for more info.",
            parse_mode="HTML",
        )
        return

    url = match.group(0)
    if not url.startswith("http"):
        url = "https://" + url.lstrip("/")

    job_id = db.add_job(url, telegram_chat_id=chat_id)
    msg = await update.message.reply_text(
        ui.format_lyrics_question(job_id, url),
        parse_mode="HTML",
        reply_markup=ui.lyrics_keyboard(job_id),
    )
    db.set_job_telegram(job_id, update.effective_chat.id, msg.message_id)


async def handle_lyrics_have(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data or not _authorized(query.message.chat_id):
        await query.answer("Unauthorized.", show_alert=True)
        return

    match = LYR_HAVE_RE.match(query.data)
    if not match:
        await query.answer()
        return

    job_id = int(match.group(1))
    job = db.get_job(job_id)
    if not job or job["status"] != "PENDING_LYRICS":
        await query.answer("Job not available.", show_alert=True)
        return
    if not db.request_lyrics_input(job_id):
        await query.answer("Could not update job.", show_alert=True)
        return

    await query.answer("Paste lyrics in your next message")
    await query.edit_message_text(
        ui.format_lyrics_paste_prompt(job_id),
        parse_mode="HTML",
        reply_markup=_cancel_only_keyboard(job_id),
    )


async def handle_lyrics_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data or not _authorized(query.message.chat_id):
        await query.answer("Unauthorized.", show_alert=True)
        return

    match = LYR_SKIP_RE.match(query.data)
    if not match:
        await query.answer()
        return

    job_id = int(match.group(1))
    job = db.get_job(job_id)
    if not job or job["status"] != "PENDING_LYRICS":
        await query.answer("Job not available.", show_alert=True)
        return
    if not db.skip_lyrics(job_id):
        await query.answer("Could not update job.", show_alert=True)
        return

    await query.answer("Auto-transcribe")
    await _start_fetching_preview_message(query, job, lyrics_mode="none")


async def handle_bg_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    if not _authorized(query.message.chat_id):
        await query.answer("Unauthorized.", show_alert=True)
        return

    match = BG_APPROVE_RE.match(query.data)
    if not match:
        await query.answer()
        return

    job_id = int(match.group(1))
    job = db.get_job(job_id)
    if not job:
        await query.answer("Job not found.", show_alert=True)
        return
    if job["status"] != "PENDING_BG_APPROVAL":
        await query.answer("Background already handled.", show_alert=True)
        return
    if not db.approve_background(job_id):
        await query.answer("Could not approve.", show_alert=True)
        return

    await query.answer("Background approved!")
    try:
        await query.edit_message_caption(
            caption=(
                f"<b>Job #{job_id}</b> — background approved ✅\n"
                f"🎵 {job['artist']} — {job['title']}"
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception:
        pass

    job = db.get_job(job_id) or job
    await _send_overlay_comparison_grid(query, job)

    overlay_msg = await query.message.reply_text(
        ui.format_picker(job_id, job["url"], job.get("title"), job.get("artist")),
        parse_mode="HTML",
        reply_markup=ui.overlay_keyboard(job_id),
    )
    db.set_job_telegram(job_id, query.message.chat_id, overlay_msg.message_id)


async def handle_bg_retry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    if not _authorized(query.message.chat_id):
        await query.answer("Unauthorized.", show_alert=True)
        return

    match = BG_RETRY_RE.match(query.data)
    if not match:
        await query.answer()
        return

    job_id = int(match.group(1))
    job = db.get_job(job_id)
    if not job:
        await query.answer("Job not found.", show_alert=True)
        return
    if job["status"] != "PENDING_BG_APPROVAL":
        await query.answer("Cannot retry now.", show_alert=True)
        return
    if not db.request_new_background(job_id):
        await query.answer("Could not retry.", show_alert=True)
        return

    await query.answer("Fetching new background…")
    try:
        await query.edit_message_caption(
            caption=f"<b>Job #{job_id}</b> — fetching new background…",
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception:
        pass

    status_msg = await query.message.reply_text(
        ui.format_fetching_preview(job_id, job["url"], job.get("lyrics_mode")),
        parse_mode="HTML",
    )
    db.set_job_telegram(job_id, query.message.chat_id, status_msg.message_id)


async def handle_overlay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    if not _authorized(query.message.chat_id):
        await query.answer("Unauthorized.", show_alert=True)
        return

    match = OVERLAY_CALLBACK_RE.match(query.data)
    if not match:
        await query.answer()
        return

    job_id = int(match.group(1))
    overlay_key = match.group(2)
    valid_keys = set(PRODUCTION_OVERLAYS.keys()) | {"none"}
    if overlay_key not in valid_keys:
        await query.answer("Unknown overlay.", show_alert=True)
        return

    job = db.get_job(job_id)
    if not job:
        await query.answer("Job not found.", show_alert=True)
        return
    if job["status"] != "PENDING_STYLE":
        await query.answer("This job was already configured.", show_alert=True)
        return

    await query.answer("Generating preview…")
    overlay_val = None if overlay_key == "none" else overlay_key
    preview_path = TEMP_DIR / f"job_{job_id}_overlay_{overlay_key}.jpg"
    bg, preview = _job_background_paths(job)

    try:
        render_overlay_preview(bg, preview, overlay_val, preview_path)
        with open(preview_path, "rb") as f:
            await query.message.reply_photo(
                photo=f,
                caption=ui.format_overlay_preview_caption(job_id, overlay_val),
                parse_mode="HTML",
                reply_markup=ui.overlay_confirm_keyboard(job_id, overlay_key),
            )
    except Exception as e:
        logger.exception(f"Overlay preview failed for job #{job_id}")
        await query.message.reply_text(
            f"Could not build overlay preview: <code>{e}</code>",
            parse_mode="HTML",
            reply_markup=ui.overlay_keyboard(job_id),
        )


async def handle_overlay_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    if not _authorized(query.message.chat_id):
        await query.answer("Unauthorized.", show_alert=True)
        return

    match = OVERLAY_USE_RE.match(query.data)
    if not match:
        await query.answer()
        return

    job_id = int(match.group(1))
    overlay_key = match.group(2)
    valid_keys = set(PRODUCTION_OVERLAYS.keys()) | {"none"}
    if overlay_key not in valid_keys:
        await query.answer("Unknown overlay.", show_alert=True)
        return

    job = db.get_job(job_id)
    if not job:
        await query.answer("Job not found.", show_alert=True)
        return
    if job["status"] != "PENDING_STYLE":
        await query.answer("This job was already configured.", show_alert=True)
        return
    if not db.confirm_job_overlay(job_id, overlay_key):
        await query.answer("Could not update job.", show_alert=True)
        return

    overlay_val = None if overlay_key == "none" else overlay_key
    await query.answer(f"Queued: {ui.overlay_label(overlay_val)}")

    try:
        await query.edit_message_caption(
            caption=(
                f"<b>Job #{job_id} queued</b>\n"
                f"🎨 {ui.overlay_label(overlay_val)}"
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception:
        pass

    status_msg = await query.message.reply_text(
        ui.format_queued(job_id, job["url"], overlay_val),
        parse_mode="HTML",
    )
    db.set_job_telegram(job_id, query.message.chat_id, status_msg.message_id)


async def handle_overlay_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    if not _authorized(query.message.chat_id):
        await query.answer("Unauthorized.", show_alert=True)
        return

    match = OVERLAY_BACK_RE.match(query.data)
    if not match:
        await query.answer()
        return

    job_id = int(match.group(1))
    job = db.get_job(job_id)
    if not job:
        await query.answer("Job not found.", show_alert=True)
        return
    if job["status"] != "PENDING_STYLE":
        await query.answer("This job was already configured.", show_alert=True)
        return

    await query.answer()
    try:
        await query.edit_message_caption(
            caption=ui.format_overlay_preview_caption(job_id, job.get("color_overlay")),
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception:
        pass

    picker_msg = await query.message.reply_text(
        ui.format_picker(job_id, job["url"], job.get("title"), job.get("artist")),
        parse_mode="HTML",
        reply_markup=ui.overlay_keyboard(job_id),
    )
    db.set_job_telegram(job_id, query.message.chat_id, picker_msg.message_id)


async def handle_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    if not _authorized(query.message.chat_id):
        await query.answer("Unauthorized.", show_alert=True)
        return

    match = CANCEL_CALLBACK_RE.match(query.data)
    if not match:
        await query.answer()
        return

    job_id = int(match.group(1))
    job = db.get_job(job_id)
    if not job:
        await query.answer("Job not found.", show_alert=True)
        return
    if not db.cancel_job(job_id):
        await query.answer("This job can no longer be cancelled.", show_alert=True)
        return

    await query.answer("Cancelled")
    try:
        if query.message.photo:
            await query.edit_message_caption(
                caption=f"<b>Job #{job_id} cancelled</b>",
                parse_mode="HTML",
                reply_markup=None,
            )
        else:
            await query.edit_message_text(
                f"<b>Job #{job_id} cancelled</b>\nURL: {job['url']}",
                parse_mode="HTML",
                reply_markup=None,
            )
    except Exception:
        pass


def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not found in .env")
        return
    if not TELEGRAM_CHAT_IDS:
        logger.error("TELEGRAM_CHAT_IDS not found in .env")
        return

    db.init_db()
    ensure_output_dirs()

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("posted", cmd_posted))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_lyrics_have, pattern=r"^lyrhave:"))
    application.add_handler(CallbackQueryHandler(handle_lyrics_skip, pattern=r"^lyrskip:"))
    application.add_handler(CallbackQueryHandler(handle_bg_approve, pattern=r"^bgapprove:"))
    application.add_handler(CallbackQueryHandler(handle_bg_retry, pattern=r"^bgretry:"))
    application.add_handler(CallbackQueryHandler(handle_overlay_confirm, pattern=r"^ovluse:"))
    application.add_handler(CallbackQueryHandler(handle_overlay_back, pattern=r"^ovlback:"))
    application.add_handler(CallbackQueryHandler(handle_overlay_callback, pattern=r"^ovl:"))
    application.add_handler(CallbackQueryHandler(handle_cancel_callback, pattern=r"^cancel:"))

    logger.info("Bot started. Monitoring chats...")
    application.run_polling()


if __name__ == "__main__":
    main()