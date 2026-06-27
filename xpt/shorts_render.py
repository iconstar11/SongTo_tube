"""
Shorts experiment renderer.

Usage:
    python shorts_render.py --reference-frame [--compare]
    python shorts_render.py --animate [--audio audio.wav]
    python shorts_render.py --pipeline --job 14    # 3 shorts per song
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageChops

from background import build_background, WIDTH, HEIGHT
from player_ui import (
    draw_player_ui,
    draw_player_base,
    paste_waveform,
    draw_progress_bar,
    playhead_x_at,
    waveform_paste_box,
    PLAYHEAD_X,
)
from lyric_layout import (
    reference_lines,
    draw_lyrics,
    active_line_index,
    clip_duration_s,
    group_words_into_lines,
    slice_alignment,
    ACTIVE_LINE_INDEX,
    LyricLine,
)
from audio_sync import precompute_waveform_frames, load_clip_samples
from segment_picker import ClipWindow, pick_three_clips, clips_to_debug_dict

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
import db  # noqa: E402
from config import OUTPUT_TO_POST_SHORTS  # noqa: E402
from pipeline.lyrics_hint import filter_section_labels  # noqa: E402
from pipeline.output_paths import SHORTS_SLOT_SUFFIX, to_post_shorts_path  # noqa: E402

XPT_DIR = Path(__file__).resolve().parent
REFERENCE_FRAME = XPT_DIR / "frames" / "frame_003.png"
AUDIO_PATH = XPT_DIR / "audio.wav"
OUTPUT_PATH = XPT_DIR / "static_test.png"
COMPARE_PATH = XPT_DIR / "static_compare.png"
VIDEO_OUTPUT = XPT_DIR / "love_is_gone_shorts.mp4"
FRAMES_DIR = XPT_DIR / "render_frames"

FPS = 30

SHORTS_PER_SONG = 3
SLOT_SUFFIX = SHORTS_SLOT_SUFFIX

FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\segoeui.ttf"),
    Path(r"C:\Windows\Fonts\arial.ttf"),
]


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if bold:
        bold_candidates = [
            Path(r"C:\Windows\Fonts\segoeuib.ttf"),
            Path(r"C:\Windows\Fonts\arialbd.ttf"),
        ] + FONT_CANDIDATES
        candidates = bold_candidates
    else:
        candidates = FONT_CANDIDATES
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def probe_duration(audio_path: Path) -> float:
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


def extract_audio_clip(source: Path, start_s: float, end_s: float, dest: Path) -> Path:
    duration_s = end_s - start_s
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{start_s:.3f}",
            "-i", str(source),
            "-t", f"{duration_s:.3f}",
            "-ac", "2", "-ar", "44100",
            "-c:a", "pcm_s16le",
            str(dest),
        ],
        check=True,
    )
    return dest


def load_pipeline_job(job_id: int | None = None) -> dict:
    import sqlite3
    from config import BASE_DIR

    conn = sqlite3.connect(BASE_DIR / "jobs.db")
    conn.row_factory = sqlite3.Row
    if job_id is None:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='COMPLETED' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    if not row:
        raise RuntimeError("No completed job found")
    job = dict(row)
    stem = Path(job["audio_path"]).stem
    alignment = _root / "temp" / f"{stem}_vocals_alignment.json"
    vocals = _root / "temp" / f"{stem}_vocals.wav"
    job["alignment_path"] = str(alignment) if alignment.exists() else None
    job["vocals_path"] = str(vocals) if vocals.exists() else None
    return job


def lines_from_alignment(alignment_path: Path, clip_start_ms: int, clip_end_ms: int, lyric_font) -> list[LyricLine]:
    words = filter_section_labels(json.loads(alignment_path.read_text(encoding="utf-8")))
    sliced = slice_alignment(words, clip_start_ms, clip_end_ms)

    def measure(text: str) -> int:
        bbox = lyric_font.getbbox(text)
        return bbox[2] - bbox[0]

    return group_words_into_lines(sliced, measure_width=measure)


def render_frame_at_time(
    *,
    t_s: float,
    duration_s: float,
    chrome_base: Image.Image,
    waveform: Image.Image,
    lines,
    lyric_font,
) -> Image.Image:
    img = paste_waveform(chrome_base, waveform)
    img = draw_progress_bar(img, playhead_x_at(t_s, duration_s))
    active = active_line_index(lines, t_s)
    draw = ImageDraw.Draw(img)
    draw_lyrics(img, lines, lyric_font=lyric_font, active_index=active, draw=draw)
    return img


def render_reference_frame(
    *,
    audio_path: Path | None = None,
    playhead_x: int = PLAYHEAD_X,
    active_line: int = ACTIVE_LINE_INDEX,
    title: str = "Love Is Gone",
) -> Image.Image:
    title_font = _load_font(34, bold=True)
    lyric_font = _load_font(27)
    base = build_background(WIDTH, HEIGHT, seed=42)
    img = draw_player_ui(
        base,
        title=title,
        title_font=title_font,
        audio_path=audio_path,
        playhead_x=playhead_x,
    )
    lines = reference_lines()
    draw = ImageDraw.Draw(img)
    draw_lyrics(img, lines, lyric_font=lyric_font, active_index=active_line, draw=draw)
    return img


def render_animated_video(
    *,
    audio_path: Path,
    output_path: Path,
    title: str = "Love Is Gone",
    lines: list[LyricLine] | None = None,
    clip_start_s: float = 0.0,
    frames_dir: Path = FRAMES_DIR,
    keep_frames: bool = False,
    synced_visualizer: bool = True,
    background_seed: int = 42,
) -> Path:
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    lines = lines or reference_lines()
    duration_s = probe_duration(audio_path)
    lyric_duration = clip_duration_s(lines, duration_s)
    duration_s = min(duration_s, lyric_duration)

    n_frames = max(1, int(round(duration_s * FPS)))
    title_font = _load_font(34, bold=True)
    lyric_font = _load_font(27)

    base = build_background(WIDTH, HEIGHT, seed=background_seed)
    chrome_base = draw_player_base(base, title=title, title_font=title_font)

    _, _, wave_w, wave_h = waveform_paste_box()
    wave_frames: list[Image.Image]
    if synced_visualizer:
        print("[sync] precomputing visualizer frames...")
        samples = load_clip_samples(audio_path, clip_start_s, clip_start_s + duration_s)
        wave_frames = precompute_waveform_frames(
            samples, 22050, fps=FPS, duration_s=duration_s, width=wave_w, height=wave_h
        )
    else:
        from player_ui import build_waveform_strip
        static = build_waveform_strip(audio_path)
        wave_frames = [static] * n_frames

    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    print(f"[render] {n_frames} frames @ {FPS}fps ({duration_s:.2f}s)")
    for i in range(n_frames):
        t_s = i / FPS
        wf = wave_frames[min(i, len(wave_frames) - 1)]
        frame = render_frame_at_time(
            t_s=t_s,
            duration_s=duration_s,
            chrome_base=chrome_base,
            waveform=wf,
            lines=lines,
            lyric_font=lyric_font,
        )
        frame.convert("RGB").save(frames_dir / f"frame_{i:06d}.png")
        if i % 90 == 0:
            print(f"[render] frame {i}/{n_frames}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    encode_cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", str(FPS),
        "-i", str(frames_dir / "frame_%06d.png"),
        "-i", str(audio_path),
        "-t", f"{duration_s:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-shortest",
        str(output_path),
    ]
    print(f"[encode] {' '.join(encode_cmd)}")
    subprocess.run(encode_cmd, check=True)

    if not keep_frames:
        shutil.rmtree(frames_dir, ignore_errors=True)

    print(f"[done] video saved {output_path}")
    return output_path


def _write_bundle_debug(job_id: int, job: dict, clips: list[ClipWindow], outputs: list[dict]) -> Path:
    report = {
        "job_id": job_id,
        "title": job.get("title"),
        "artist": job.get("artist"),
        "shorts_count": len(clips),
        "clips": clips_to_debug_dict(clips),
        "outputs": outputs,
    }
    OUTPUT_TO_POST_SHORTS.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_TO_POST_SHORTS / f"job_{job_id}_shorts_bundle.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[debug] bundle saved {path}")
    return path


def render_one_clip(
    *,
    job: dict,
    clip: ClipWindow,
    audio_src: Path,
    alignment_path: Path,
    lyric_font,
    display_title: str,
) -> dict:
    clip_start_s = clip.start_ms / 1000.0
    clip_end_s = clip.end_ms / 1000.0
    suffix = SLOT_SUFFIX.get(clip.slot, clip.slot)
    clip_audio = XPT_DIR / f"clip_{job['id']}_{suffix}.wav"
    out = to_post_shorts_path(display_title, suffix)

    print(
        f"[clip {suffix}] {clip.label} ({clip.source}) "
        f"{clip_start_s:.1f}s–{clip_end_s:.1f}s ({clip.duration_s:.1f}s)"
    )
    extract_audio_clip(audio_src, clip_start_s, clip_end_s, clip_audio)
    lines = lines_from_alignment(alignment_path, clip.start_ms, clip.end_ms, lyric_font)
    print(f"[clip {suffix}] {len(lines)} lyric lines")

    seed = int(job["id"]) * 10 + {"chorus": 1, "improv_a": 2, "improv_b": 3}.get(clip.slot, 0)
    render_animated_video(
        audio_path=clip_audio,
        output_path=out,
        title=display_title,
        lines=lines,
        clip_start_s=0.0,
        synced_visualizer=True,
        background_seed=seed,
        frames_dir=XPT_DIR / f"render_frames_{suffix}",
    )
    return {
        "slot": clip.slot,
        "suffix": suffix,
        "label": clip.label,
        "source": clip.source,
        "path": str(out),
        "lines": len(lines),
    }


def render_pipeline_short(job_id: int | None = None, output_path: Path | None = None) -> list[Path]:
    job = load_pipeline_job(job_id)
    audio_src = Path(job["audio_path"])
    alignment_path = Path(job["alignment_path"])
    if not alignment_path.exists():
        raise FileNotFoundError(f"Missing alignment: {alignment_path}")

    title = job.get("title") or "Unknown Title"
    artist = job.get("artist") or ""
    display_title = title
    vocals_path = Path(job["vocals_path"]) if job.get("vocals_path") else None

    raw_words = json.loads(alignment_path.read_text(encoding="utf-8"))
    filtered = filter_section_labels(raw_words)
    duration_s = probe_duration(audio_src)

    print(f"[pipeline] job #{job['id']}: {artist} — {title}")
    clips = pick_three_clips(raw_words, filtered, duration_s, vocals_path)
    print(f"[pipeline] picked {len(clips)} shorts:")
    for c in clips:
        print(f"  • {c.slot}: {c.label} [{c.source}] {c.duration_s:.1f}s")

    lyric_font = _load_font(27)
    outputs_meta: list[dict] = []
    paths: list[Path] = []

    for clip in clips:
        meta = render_one_clip(
            job=job,
            clip=clip,
            audio_src=audio_src,
            alignment_path=alignment_path,
            lyric_font=lyric_font,
            display_title=display_title,
        )
        outputs_meta.append(meta)
        paths.append(Path(meta["path"]))

    _write_bundle_debug(job["id"], job, clips, outputs_meta)
    db.save_shorts_paths(job["id"], [str(p) for p in paths])
    print(f"[pipeline] saved {len(paths)} shorts path(s) to jobs.db")

    if output_path:
        print(f"[note] --output ignored for multi-short mode; see bundle JSON")
    return paths


def _difference_score(a: Image.Image, b: Image.Image) -> float:
    if a.size != b.size:
        b = b.resize(a.size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    hist = diff.histogram()
    total_pixels = a.size[0] * a.size[1]
    return sum(i * (hist[i] + hist[256 + i] + hist[512 + i]) for i in range(256)) / (total_pixels * 3)


def main():
    parser = argparse.ArgumentParser(description="Shorts experiment renderer")
    parser.add_argument("--reference-frame", action="store_true")
    parser.add_argument("--animate", action="store_true")
    parser.add_argument("--pipeline", action="store_true", help="Render 3 shorts from pipeline job")
    parser.add_argument("--job", type=int, default=None)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--keep-frames", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--audio", type=Path, default=AUDIO_PATH)
    args = parser.parse_args()

    if args.pipeline:
        paths = render_pipeline_short(job_id=args.job, output_path=args.output)
        print(f"[done] {len(paths)} shorts:")
        for p in paths:
            print(f"  {p}")
        return

    if args.animate:
        out = args.output or VIDEO_OUTPUT
        render_animated_video(
            audio_path=args.audio,
            output_path=out,
            keep_frames=args.keep_frames,
            clip_start_s=0.0,
        )
        return

    if not args.reference_frame:
        parser.print_help()
        sys.exit(1)

    out = args.output or OUTPUT_PATH
    audio = args.audio if args.audio.exists() else None
    img = render_reference_frame(audio_path=audio)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"[done] saved {out}")

    if args.compare and REFERENCE_FRAME.exists():
        ref = Image.open(REFERENCE_FRAME).convert("RGBA")
        score = _difference_score(img, ref)
        combo = Image.new("RGB", (WIDTH * 2, HEIGHT), (32, 32, 32))
        combo.paste(ref.convert("RGB"), (0, 0))
        combo.paste(img.convert("RGB"), (WIDTH, 0))
        combo.save(COMPARE_PATH)
        print(f"[compare] mean pixel diff vs frame_003: {score:.2f}")
        print(f"[compare] side-by-side saved {COMPARE_PATH}")


if __name__ == "__main__":
    main()