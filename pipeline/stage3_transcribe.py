import json
import subprocess
import tempfile
from pathlib import Path
from openai import OpenAI
from config import OPENAI_API_KEY, TEMP_DIR
from pipeline.lyrics_hint import (
    build_continuation_prompt,
    build_whisper_prompt,
    filter_section_labels,
)

def _split_audio(audio_path: Path) -> list[Path]:
    out_dir = Path(tempfile.mkdtemp(prefix="whisper_chunks_"))
    pattern = str(out_dir / "chunk_%03d.mp3")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(audio_path),
        "-f", "segment",
        "-segment_time", "300",
        "-ac", "1", "-ar", "16000", "-b:a", "64k",
        pattern,
    ], check=True)
    return sorted(out_dir.glob("chunk_*.mp3"))

def run(vocals_path: Path, title: str, artist: str, lyrics_path: Path | str | None = None) -> list[dict]:
    """Transcribe vocals via OpenAI Whisper API."""
    alignment_path = TEMP_DIR / f"{vocals_path.stem}_alignment.json"
    if alignment_path.exists():
        try:
            return filter_section_labels(json.loads(alignment_path.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"Error reading cached alignment, re-transcribing: {e}")

    lyrics_file = Path(lyrics_path) if lyrics_path else None
    chunk_prompt = build_whisper_prompt(title, artist, lyrics_file)
    if lyrics_file and lyrics_file.exists():
        print(f"Whisper using spelling hints from: {lyrics_file.name}")

    client = OpenAI(api_key=OPENAI_API_KEY)
    chunks = _split_audio(vocals_path)
    words = []
    time_offset = 0.0
    transcript_tail = ""

    for chunk_index, chunk in enumerate(chunks):
        if chunk_index > 0:
            chunk_prompt = build_continuation_prompt(transcript_tail) or chunk_prompt

        with open(chunk, "rb") as f:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["word"],
                prompt=chunk_prompt,
            )

        if response.words:
            chunk_words = []
            for w in response.words:
                entry = {
                    "word": w.word.strip(),
                    "start_ms": int((w.start + time_offset) * 1000),
                    "end_ms": int((w.end + time_offset) * 1000),
                }
                chunk_words.append(entry)
                words.append(entry)
            transcript_tail = " ".join(w["word"] for w in chunk_words)
        
        duration_proc = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(chunk)
        ], capture_output=True, text=True)
        time_offset += float(duration_proc.stdout.strip())
        chunk.unlink()

    words = filter_section_labels(words)
    alignment_path.write_text(json.dumps(words, indent=2), encoding="utf-8")
    return words