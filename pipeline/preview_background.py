"""Build a 1920x1080 background preview image (no color overlay)."""

from pathlib import Path

from PIL import Image

from pipeline.stage4_render import _crop_and_resize


def build_preview(source_path: Path | None, output_path: Path) -> Path:
    if source_path and source_path.exists():
        img = Image.open(source_path)
        img = _crop_and_resize(img)
    else:
        img = Image.new("RGB", (1920, 1080), color=(15, 15, 15))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG")
    return output_path