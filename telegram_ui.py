"""Shared Telegram message formatting and keyboards for bot + worker."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from pipeline.color_overlay import PRODUCTION_OVERLAYS

STAGE_PROGRESS = {
    "DOWNLOADING": ("Downloading audio", 15),
    "BACKGROUND": ("Fetching background", 25),
    "DEMUCS": ("Isolating vocals", 40),
    "TRANSCRIBING": ("Transcribing lyrics", 55),
    "RENDERING": ("Rendering video", 75),
    "DELIVERING": ("Sending video", 90),
    "UPLOADING": ("Uploading to YouTube", 95),
}


def overlay_label(key: str | None) -> str:
    if not key:
        return "No Overlay"
    preset = PRODUCTION_OVERLAYS.get(key)
    if preset:
        return preset["label"]
    return key


def progress_bar(pct: int, width: int = 12) -> str:
    filled = max(0, min(width, int(width * pct / 100)))
    return "█" * filled + "░" * (width - filled)


def lyrics_keyboard(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("I have lyrics", callback_data=f"lyrhave:{job_id}"),
            InlineKeyboardButton("Skip — auto transcribe", callback_data=f"lyrskip:{job_id}"),
        ],
        [InlineKeyboardButton("Cancel", callback_data=f"cancel:{job_id}")],
    ])


def bg_approval_keyboard(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"bgapprove:{job_id}"),
            InlineKeyboardButton("🔄 New Image", callback_data=f"bgretry:{job_id}"),
        ],
        [InlineKeyboardButton("Cancel", callback_data=f"cancel:{job_id}")],
    ])


def overlay_keyboard(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Black", callback_data=f"ovl:{job_id}:black"),
            InlineKeyboardButton("Royal Purple", callback_data=f"ovl:{job_id}:royal_purple"),
        ],
        [
            InlineKeyboardButton("Noir Velvet", callback_data=f"ovl:{job_id}:noir_velvet"),
            InlineKeyboardButton("Wine Burgundy", callback_data=f"ovl:{job_id}:wine_burgundy"),
        ],
        [InlineKeyboardButton("No Overlay", callback_data=f"ovl:{job_id}:none")],
        [InlineKeyboardButton("Cancel", callback_data=f"cancel:{job_id}")],
    ])


def overlay_confirm_keyboard(job_id: int, overlay_key: str) -> InlineKeyboardMarkup:
    label = overlay_label(None if overlay_key == "none" else overlay_key)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Use {label}", callback_data=f"ovluse:{job_id}:{overlay_key}")],
        [InlineKeyboardButton("◀️ Back to overlay options", callback_data=f"ovlback:{job_id}")],
        [InlineKeyboardButton("Cancel", callback_data=f"cancel:{job_id}")],
    ])


def format_lyrics_question(job_id: int, url: str) -> str:
    return (
        f"<b>Job #{job_id} created</b>\n"
        f"URL: {url}\n\n"
        f"Do you have lyrics for this song?\n"
        f"They inspire spelling only — <b>audio always wins</b> on words and timing.\n"
        f"Spelling doesn't need to be perfect."
    )


def format_lyrics_paste_prompt(job_id: int) -> str:
    return (
        f"<b>Job #{job_id}</b> — paste your lyrics\n\n"
        f"Send them in your <b>next message</b> as plain text.\n"
        f"Rough spelling is fine — audio always wins on timing.\n\n"
        f"<i>Or tap Cancel below.</i>"
    )


def format_fetching_preview(job_id: int, url: str, lyrics_mode: str | None = None) -> str:
    hint = ""
    if lyrics_mode == "hint":
        hint = "\n📝 Lyrics saved as spelling inspiration — audio wins on timing."
    elif lyrics_mode == "none":
        hint = "\n📝 Auto-transcribe (no lyrics hint)."
    return (
        f"<b>Job #{job_id}</b> — fetching preview\n"
        f"URL: {url}\n\n"
        f"⏳ Downloading song info and background image…{hint}\n"
        f"<i>You'll receive the background photo to approve first.</i>"
    )


def format_overlay_grid_caption(job_id: int, title: str, artist: str) -> str:
    return (
        f"<b>Job #{job_id}</b> — overlay previews on your background\n"
        f"🎵 {artist} — {title}\n\n"
        f"Compare options above, then tap a button below to preview one full-size."
    )


def format_overlay_preview_caption(job_id: int, overlay_key: str | None) -> str:
    return (
        f"<b>Job #{job_id}</b> — overlay preview\n"
        f"🎨 <b>{overlay_label(overlay_key)}</b>\n\n"
        f"Use this overlay, or go back to try another."
    )


def format_preview_caption(job_id: int, title: str, artist: str) -> str:
    return (
        f"<b>Job #{job_id}</b> — background preview\n"
        f"🎵 {artist} — {title}\n\n"
        f"Approve this image, or tap <b>New Image</b> for a different one.\n"
        f"<i>Each retry searches new photos and never repeats one you've already seen.</i>"
    )


def format_picker(job_id: int, url: str, title: str = None, artist: str = None) -> str:
    song = f"🎵 {artist} — {title}\n" if title and artist else ""
    lines = [
        f"<b>Job #{job_id}</b> — background approved ✅",
        song + f"URL: {url}".strip(),
        "",
        "<b>Now choose a mood overlay</b>",
        "Tap an option below — you'll get a <b>preview image</b> on your background before rendering.",
        "• Black — heavy dramatic wash",
        "• Royal Purple — cool spiritual tone",
        "• Noir Velvet — cinematic velvet + cream",
        "• Wine Burgundy — warm emotional red",
        "• No Overlay — keep background as-is",
        "",
        "Est. render time after this: <b>15–30 min</b>.",
    ]
    return "\n".join(lines)


def format_queued(job_id: int, url: str, overlay_key: str | None) -> str:
    return (
        f"<b>Job #{job_id} queued</b>\n"
        f"URL: {url}\n"
        f"Overlay: <b>{overlay_label(overlay_key)}</b>\n\n"
        f"{progress_bar(5)} 5%\n"
        f"⏳ Waiting for worker…\n"
        f"<i>Est. 15–30 minutes</i>"
    )


def format_progress(
    job_id: int,
    title: str | None,
    artist: str | None,
    overlay_key: str | None,
    stage_key: str,
) -> str:
    label, pct = STAGE_PROGRESS.get(stage_key, ("Working", 50))
    song = "—"
    if title and artist:
        song = f"{artist} — {title}"
    elif title:
        song = title

    return (
        f"<b>Job #{job_id}</b> — in progress\n"
        f"🎵 {song}\n"
        f"🎨 {overlay_label(overlay_key)}\n\n"
        f"{progress_bar(pct)} {pct}%\n"
        f"⏳ {label}…"
    )


def format_complete(
    job_id: int,
    title: str,
    artist: str,
    overlay_key: str | None,
    telegram_sent: bool,
    youtube_result: dict,
) -> str:
    lines = [
        f"✅ <b>Job #{job_id} complete</b>",
        f"🎵 {artist} — {title}",
        f"🎨 {overlay_label(overlay_key)}",
    ]

    if telegram_sent:
        lines.append("📱 Video sent below")
    else:
        lines.append("📱 Video: not delivered to Telegram")

    if youtube_result.get("ok"):
        vid = youtube_result["video_id"]
        lines.append(f'⬆️ YouTube: <a href="https://youtu.be/{vid}">youtu.be/{vid}</a>')
    elif youtube_result.get("skipped"):
        reason = youtube_result.get("reason", "unknown")
        if reason == "disabled":
            lines.append("⬆️ YouTube: off (enable later in .env)")
        elif reason == "missing_credentials":
            lines.append("⬆️ YouTube: not configured yet (add client_secrets.json)")
        else:
            lines.append(f"⬆️ YouTube: skipped ({reason})")
    else:
        lines.append(f"⬆️ YouTube: failed — {youtube_result.get('reason', 'unknown error')}")

    return "\n".join(lines)


def format_failed(job_id: int, stage: str, error: str) -> str:
    return (
        f"❌ <b>Job #{job_id} failed</b>\n"
        f"Stage: {stage}\n"
        f"<code>{error}</code>"
    )


def format_status_list(jobs: list[dict]) -> str:
    if not jobs:
        return "No recent jobs found."

    lines = ["<b>Recent jobs</b>", ""]
    for job in jobs:
        overlay = overlay_label(job.get("color_overlay"))
        title = job.get("title") or "—"
        artist = job.get("artist") or ""
        song = f"{artist} — {title}".strip(" —") if artist or title != "—" else "—"
        post = job.get("post_status") or "—"
        lines.append(f"#{job['id']} · <b>{job['status']}</b> · {song}")
        lines.append(f"   Overlay: {overlay} · Post: {post}")
    return "\n".join(lines)