"""OpenSlide wrapper: open, metadata, thumbnail, lazy region reads (SPEC §3, §9)."""

import io

import openslide
from PIL import Image


def open_slide(slide_path: str) -> openslide.OpenSlide:
    """Open a whole-slide image (.svs, .ndpi, .tiff, ...)."""
    return openslide.OpenSlide(str(slide_path))


def get_slide_metadata(slide: openslide.OpenSlide) -> dict:
    """Extract level-0 dimensions, pyramid info, objective power, and mpp."""
    props = slide.properties
    mpp_x = props.get(openslide.PROPERTY_NAME_MPP_X)
    mpp_y = props.get(openslide.PROPERTY_NAME_MPP_Y)
    mpp = (float(mpp_x) + float(mpp_y)) / 2.0 if mpp_x and mpp_y else None

    objective = props.get(openslide.PROPERTY_NAME_OBJECTIVE_POWER)

    return {
        "dimensions": slide.dimensions,
        "mpp": mpp,
        "objective": float(objective) if objective else None,
        "level_count": slide.level_count,
        "level_dimensions": slide.level_dimensions,
        "downsamples": slide.level_downsamples,
    }


def generate_thumbnail(slide: openslide.OpenSlide, max_size: int = 800) -> Image.Image:
    """Render a downsampled thumbnail of the entire slide, aspect ratio preserved."""
    full_w, full_h = slide.dimensions
    if full_w >= full_h:
        thumb_w = max_size
        thumb_h = round(full_h * max_size / full_w)
    else:
        thumb_h = max_size
        thumb_w = round(full_w * max_size / full_h)

    return slide.get_thumbnail((thumb_w, thumb_h)).convert("RGB")


def read_region_at_size(
    slide: openslide.OpenSlide,
    x0: int,
    y0: int,
    downsample: float,
    width: int,
    height: int,
) -> Image.Image:
    """
    Read the level-0 region with upper-left corner (x0, y0) covering
    (width * downsample, height * downsample) level-0 pixels, from the
    nearest available pyramid level, and resize it to exactly (width, height).
    """
    level = slide.get_best_level_for_downsample(downsample)
    level_downsample = slide.level_downsamples[level]

    read_w = max(1, round(width * downsample / level_downsample))
    read_h = max(1, round(height * downsample / level_downsample))

    region = slide.read_region((x0, y0), level, (read_w, read_h)).convert("RGB")
    if region.size != (width, height):
        region = region.resize((width, height), Image.LANCZOS)

    return region


def pil_to_png_bytes(img: Image.Image) -> bytes:
    """Convert a PIL Image to PNG bytes, for display in an ipywidgets Image widget."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
