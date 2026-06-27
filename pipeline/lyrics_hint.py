"""Save user lyrics as markdown and build soft Whisper prompts."""

import re
from pathlib import Path

# OpenAI Whisper only considers the last 224 tokens of the prompt.
WHISPER_PROMPT_MAX_CHARS = 224
MAX_SPELLING_HINTS = 18

_SECTION_TAG = (
    r"intro|outro|verse|chorus|bridge|pre[- ]?chorus|hook|interlude|"
    r"instrumental|refrain|break|drop|solo|spoken|ad[- ]?lib|"
    r"post[- ]?chorus|tag|ending|opening"
)
_SECTION_LINE_RE = re.compile(rf"^\s*\[(?:{_SECTION_TAG})(?:\s*\d+)?[^\]]*\]\s*$", re.IGNORECASE)
_INLINE_SECTION_RE = re.compile(rf"\[(?:{_SECTION_TAG})(?:\s*\d+)?[^\]]*\]", re.IGNORECASE)
_GENIUS_TITLE_LINE_RE = re.compile(r"^\s*.+\s+lyrics?\s*$", re.IGNORECASE)

_SECTION_LABEL_WORDS = frozenset({
    "chorus", "verse", "bridge", "intro", "outro", "hook", "interlude",
    "instrumental", "refrain", "pre-chorus", "prechorus", "pre", "lyrics",
    "post-chorus", "postchorus", "tag", "ending", "opening", "break", "drop",
    "solo", "spoken", "adlib", "ad-lib",
})

_COMMON_WORDS = frozenset("""
a an the and or but in on at to for of is are was were be been being am
i you he she it we they me my your his her its our their this that these those
what when where who how why not don't won't can't shouldn't wouldn't couldn't
with from into about like just yeah oh uh um so if as by up down all out
have has had do does did will would could should may might must can
go goes went going come comes came take takes took make makes made
get gets got see saw know knew think thought want wanted need needed
say says said tell told look looked feel felt stay stayed let left
one two three four five six seven eight nine ten back been still only
""".split())


def clean_user_lyrics(raw_text: str) -> str:
    """
    Strip section markers, site headers, and extra whitespace from pasted lyrics.

    Removes lines like [Chorus], [Verse 1], and Genius-style "Song Title Lyrics" headers.
    Keeps sung ad-libs in parentheses, e.g. (Yeah).
    """
    lines = raw_text.replace("\r\n", "\n").split("\n")
    cleaned: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue

        if _SECTION_LINE_RE.match(stripped):
            continue

        stripped = _INLINE_SECTION_RE.sub("", stripped).strip()
        if not stripped:
            continue

        if _GENIUS_TITLE_LINE_RE.match(stripped):
            continue

        stripped = re.sub(r"\s+", " ", stripped)
        cleaned.append(stripped)

    while cleaned and cleaned[-1] == "":
        cleaned.pop()

    return "\n".join(cleaned)


def _word_key(word: str) -> str:
    return re.sub(r"[^\w'-]", "", word.strip()).lower()


def filter_section_labels(words: list[dict]) -> list[dict]:
    """Remove section-marker words Whisper may hallucinate from lyrics hints."""
    filtered: list[dict] = []
    i = 0
    while i < len(words):
        normalized = _word_key(words[i]["word"])

        if normalized in _SECTION_LABEL_WORDS:
            i += 1
            if i < len(words) and re.fullmatch(r"\d+", _word_key(words[i]["word"])):
                i += 1
            continue

        if normalized == "verse" and i + 1 < len(words):
            if re.fullmatch(r"\d+", _word_key(words[i + 1]["word"])):
                i += 2
                continue

        filtered.append(words[i])
        i += 1

    return filtered


def _extract_spelling_hints(lyrics_body: str, title: str, artist: str) -> list[str]:
    """
    Pull a small spelling guide from lyrics.

    Whisper uses prompts as style/spelling examples, not instructions — so we only
    pass tricky words, not lyric passages.
    """
    hints: list[str] = []
    seen: set[str] = set()

    def add(word: str) -> None:
        key = _word_key(word)
        if not key or key in seen or key in _COMMON_WORDS or key in _SECTION_LABEL_WORDS:
            return
        seen.add(key)
        hints.append(word)

    for token in re.findall(r"[A-Za-z']+", f"{artist} {title}"):
        add(token)

    tokens = re.findall(r"[A-Za-z']+", lyrics_body)
    for token in tokens:
        if "'" in token:
            add(token)

    for token in sorted(set(tokens), key=len, reverse=True):
        if len(token) >= 7:
            add(token)

    for token in tokens:
        if len(token) >= 4:
            add(token)

    return hints[:MAX_SPELLING_HINTS]


def save_lyrics_md(job_id: int, title: str, artist: str, raw_text: str, output_dir: Path) -> Path:
    """Write cleaned user lyrics to a markdown hint file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"job_{job_id}_lyrics.md"
    body = clean_user_lyrics(raw_text)
    content = (
        f"# {title} — {artist}\n"
        f"source: user\n"
        f"mode: hint_only\n\n"
        f"## Lyrics\n"
        f"{body}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def _extract_lyrics_body(md_text: str) -> str:
    if "## Lyrics" in md_text:
        return md_text.split("## Lyrics", 1)[1].strip()
    return md_text.strip()


def build_whisper_prompt(title: str, artist: str, lyrics_path: Path | None) -> str:
    """
    Build a light Whisper prompt for the first audio chunk.

    Per OpenAI's Whisper guide, prompts steer spelling/style — they are not
    instructions and should not contain long lyric passages.
    """
    title = (title or "Unknown Title").strip()
    artist = (artist or "Unknown Artist").strip()
    prompt = f"{artist}. {title}. Song vocals."

    if not lyrics_path or not lyrics_path.exists():
        return prompt[:WHISPER_PROMPT_MAX_CHARS]

    try:
        md_text = lyrics_path.read_text(encoding="utf-8")
    except OSError:
        return prompt[:WHISPER_PROMPT_MAX_CHARS]

    lyrics_body = clean_user_lyrics(_extract_lyrics_body(md_text))
    hints = _extract_spelling_hints(lyrics_body, title, artist)
    if hints:
        prompt = f"{prompt} Spellings: {', '.join(hints)}."

    return prompt[:WHISPER_PROMPT_MAX_CHARS]


def build_continuation_prompt(transcript_tail: str) -> str:
    """
    Build a prompt for later audio chunks using prior transcript text.

    OpenAI recommends submitting the previous segment's transcript so Whisper
    maintains style continuity without re-injecting lyric hints.
    """
    tail = re.sub(r"\s+", " ", transcript_tail.strip())
    if not tail:
        return ""
    return tail[-WHISPER_PROMPT_MAX_CHARS:]