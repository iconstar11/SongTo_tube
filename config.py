import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Dynamic detection of winget-installed shared FFmpeg DLLs for torchcodec on Windows
import platform
if platform.system() == "Windows":
    _local_app_data = os.getenv("LOCALAPPDATA")
    if _local_app_data:
        _winget_dir = Path(_local_app_data) / "Microsoft/WinGet/Packages"
        if _winget_dir.exists():
            for _path in _winget_dir.glob("Gyan.FFmpeg.Shared_*/**/bin"):
                if _path.exists():
                    os.environ["PATH"] = str(_path) + os.pathsep + os.environ.get("PATH", "")
                    try:
                        os.add_dll_directory(str(_path))
                    except AttributeError:
                        pass
                    break

# Project Paths
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
BG_DIR = ASSETS_DIR / "backgrounds"
PARTICLES_DIR = ASSETS_DIR / "particles"
OUTPUT_DIR = BASE_DIR / os.getenv("OUTPUT_DIR", "outputs")
TEMP_DIR = BASE_DIR / os.getenv("TEMP_DIR", "temp")

OUTPUT_POSTED = OUTPUT_DIR / "posted"
OUTPUT_TO_POST = OUTPUT_DIR / "to_post"
OUTPUT_POSTED_VIDEO = OUTPUT_POSTED / "video"
OUTPUT_POSTED_SHORTS = OUTPUT_POSTED / "shorts"
OUTPUT_TO_POST_VIDEO = OUTPUT_TO_POST / "video"
OUTPUT_TO_POST_SHORTS = OUTPUT_TO_POST / "shorts"


def ensure_output_dirs() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    for folder in (
        OUTPUT_POSTED_VIDEO,
        OUTPUT_POSTED_SHORTS,
        OUTPUT_TO_POST_VIDEO,
        OUTPUT_TO_POST_SHORTS,
    ):
        folder.mkdir(parents=True, exist_ok=True)


# Ensure directories exist
ensure_output_dirs()
TEMP_DIR.mkdir(exist_ok=True)
PARTICLES_DIR.mkdir(exist_ok=True)

# API Keys
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Expecting a comma-separated list in .env
_chat_ids = os.getenv("TELEGRAM_CHAT_IDS", "")
TELEGRAM_CHAT_IDS = [cid.strip() for cid in _chat_ids.split(",") if cid.strip()]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
YOUTUBE_CLIENT_SECRETS = os.getenv("YOUTUBE_CLIENT_SECRETS", "client_secrets.json")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY")

# Demucs Settings
DEMUCS_MODEL = os.getenv("DEMUCS_MODEL", "htdemucs_ft")

# 7clouds Style Defaults
FONT_FILE = FONTS_DIR / "edosz.ttf"
FONT_SIZE = 110
ACCENT_COLOR = "#FFD700"  # Gold
TEXT_COLOR = "#FFFFFF"
SHADOW_COLOR = "black"
SHADOW_ALPHA = "0.75"
SHADOWX = 6
SHADOWY = 6
CURRENT_LINE_Y = 440  # legacy fallback
NEXT_LINE_Y_DELTA = 130  # legacy alias for LINE_SPACING
LYRIC_CENTER_Y = 540
LINE_SPACING = 130
PAGE_MAX_LINES = 3
PAGE_TARGET_MAX_S = 10.0
PAGE_HOLD_MAX_S = 1.0
GAP_STRONG_BREAK_S = 1.2
GAP_MEDIUM_BREAK_S = 0.6
GAP_TIGHT_S = 0.4
ANIM_MIN_GAP_S = 0.55
PAGE_LEAD_SLIDE_PX = 18
PAGE_LEAD_ANIM_MIN_S = 0.3
PAGE_LEAD_ANIM_MAX_S = 1.0
PAGE_LEAD_ANIM_RATIO = 1.0
PAGE_FADE_ANIM_MIN_S = 0.25
PAGE_FADE_ANIM_MAX_S = 1.0
PAGE_FADE_ANIM_RATIO = 1.0
PAGE_MIN_LEAD_MS = 700
PAGE_MIN_LEAD_TIGHT_MS = 120
MAX_WIDTH_PX = 1800
FADE_MS = 200
LEAD_IN_MS = 1500
HIGHLIGHT_OFFSET_MS = 150
MAX_LINE_DURATION_MS = 6000
FAST_WORD_DURATION_MS = 350
INTRO_DURATION_SEC = 5.0
HIGHLIGHT_WORDS = os.getenv("HIGHLIGHT_WORDS", "False").lower() == "true"
ENABLE_VISUALIZER = os.getenv("ENABLE_VISUALIZER", "True").lower() == "true"

# Mood background overlays (empty = disabled). See pipeline/color_overlay.py
COLOR_OVERLAY = os.getenv("COLOR_OVERLAY", "").strip().lower()

# YouTube upload (requires client_secrets.json + OAuth token.pickle)
ENABLE_YOUTUBE_UPLOAD = os.getenv("ENABLE_YOUTUBE_UPLOAD", "False").lower() == "true"
