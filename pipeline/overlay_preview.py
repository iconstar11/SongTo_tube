"""Build overlay preview images for Telegram overlay picker."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pipeline.color_overlay import PRODUCTION_OVERLAYS, apply_overlay, resolve_overlay
from pipeline.preview_background import build_preview


def _load_background_image(background_source: Path | None, preview_image: Path | None) -> Image.Image:
    if preview_image and preview_image.exists():
        return Image.open(preview_image).convert("RGB")
    if background_source and background_source.exists():
        tmp = preview_image or (background_source.parent / f"_ovl_base_{background_source.stem}.png")
        build_preview(background_source, Path(tmp))
        return Image.open(tmp).convert("RGB")
    return Image.new("RGB", (1920, 1080), color=(15, 15, 15))


def render_overlay_preview(
    background_source: Path | None,
    preview_image: Path | None,
    overlay_key: str | None,
    output_path: Path,
    max_width: int = 1280,
) -> Path:
    """Render one overlay option on the approved background."""
    img = _load_background_image(background_source, preview_image)
    if overlay_key:
        img = apply_overlay(img, overlay_key)

    if img.width > max_width:
        h = int(img.height * max_width / img.width)
        img = img.resize((max_width, h), Image.Resampling.LANCZOS)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "JPEG", quality=88, optimize=True)
    return output_path


def build_overlay_comparison_grid(
    background_source: Path | None,
    preview_image: Path | None,
    output_path: Path,
    thumb_w: int = 640,
    thumb_h: int = 360,
) -> Path:
    """2x3 grid: original + each production overlay with labels."""
    base = _load_background_image(background_source, preview_image)
    base = base.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)

    options: list[tuple[str, Image.Image]] = [("Original", base.copy())]
    for key, preset in PRODUCTION_OVERLAYS.items():
        tinted = apply_overlay(base.copy(), key)
        options.append((preset["label"], tinted))

    cols, rows = 3, 2
    pad = 16
    label_h = 36
    cell_w = thumb_w + pad * 2
    cell_h = thumb_h + label_h + pad * 2
    grid = Image.new("RGB", (cols * cell_w, rows * cell_h), (24, 24, 24))
    draw = ImageDraw.Draw(grid)

    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()

    for idx, (label, thumb) in enumerate(options):
        col = idx % cols
        row = idx // cols
        x0 = col * cell_w + pad
        y0 = row * cell_h + pad
        grid.paste(thumb, (x0, y0))
        draw.text((x0, y0 + thumb_h + 6), label, fill=(240, 240, 240), font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    grid.save(output_path, "JPEG", quality=90, optimize=True)
    return output_path