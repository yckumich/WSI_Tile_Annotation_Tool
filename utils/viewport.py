"""Navigation and pan/zoom viewport math (SPEC §3, §4.1, §4.2).

Pure coordinate math, no slide I/O and no widget imports — the level-0 region
a viewport covers, where it clamps to slide bounds, and how it maps onto the
overview-map thumbnail.
"""

import math

from utils.grid import pixel_to_tile


def compute_viewport(
    center_x: int,
    center_y: int,
    downsample: float,
    canvas_width: int,
    canvas_height: int,
) -> tuple[int, int, int, int]:
    """
    Level-0 region (x, y, width, height) to render, centered on
    (center_x, center_y) at the given downsample and canvas size.
    """
    width = round(canvas_width * downsample)
    height = round(canvas_height * downsample)
    x = round(center_x - width / 2)
    y = round(center_y - height / 2)
    return (x, y, width, height)


def clamp_viewport(
    viewport: tuple[int, int, int, int],
    slide_width: int,
    slide_height: int,
) -> tuple[int, int, int, int]:
    """Keep the viewport's upper-left corner within slide bounds."""
    x, y, width, height = viewport
    max_x = max(0, slide_width - width)
    max_y = max(0, slide_height - height)
    x = min(max(x, 0), max_x)
    y = min(max(y, 0), max_y)
    return (x, y, width, height)


def compute_viewport_rect_on_map(
    viewport: tuple[int, int, int, int],
    slide_width: int,
    slide_height: int,
    thumbnail_width: int,
    thumbnail_height: int,
) -> tuple[float, float, float, float]:
    """
    Map the current viewport (level-0 coords) onto thumbnail pixel coords,
    returning (x0, y0, x1, y1) for drawing the viewport rectangle.
    """
    x, y, width, height = viewport
    scale_x = thumbnail_width / slide_width
    scale_y = thumbnail_height / slide_height
    return (x * scale_x, y * scale_y, (x + width) * scale_x, (y + height) * scale_y)


def map_click_to_center(
    map_x: float,
    map_y: float,
    thumbnail_width: int,
    thumbnail_height: int,
    slide_width: int,
    slide_height: int,
) -> tuple[int, int]:
    """Convert a click on the overview map into the level-0 point to recenter on."""
    scale_x = thumbnail_width / slide_width
    scale_y = thumbnail_height / slide_height

    center_x = max(0, min(map_x / scale_x, slide_width))
    center_y = max(0, min(map_y / scale_y, slide_height))
    return (round(center_x), round(center_y))


def canvas_to_slide_coords(
    canvas_x: float,
    canvas_y: float,
    viewport: tuple[int, int, int, int],
    canvas_width: int,
    canvas_height: int,
) -> tuple[int, int]:
    """
    Click chain step 1-2: canvas pixel -> level-0 slide coordinate (SPEC §5.2).

    `canvas_width`/`canvas_height` is the on-screen pixel size the viewport is
    rendered into (e.g. VIEW_SIZE) — needed alongside the level-0-sized
    viewport to recover the downsample factor.
    """
    x, y, width, height = viewport
    scale_x = width / canvas_width
    scale_y = height / canvas_height
    slide_x = x + canvas_x * scale_x
    slide_y = y + canvas_y * scale_y
    return (round(slide_x), round(slide_y))


def resolve_click_to_tile(
    canvas_x: float,
    canvas_y: float,
    viewport: tuple[int, int, int, int],
    canvas_width: int,
    canvas_height: int,
    grid_origin: tuple[int, int],
    tile_size_level0: int,
) -> tuple[int, int]:
    """
    Full click chain: canvas pixel -> level-0 coordinate -> (row, col) (SPEC
    §5.2). Zoom-invariant: resolves through level-0 coordinates first, so the
    same tissue always resolves to the same tile regardless of zoom.
    """
    slide_x, slide_y = canvas_to_slide_coords(canvas_x, canvas_y, viewport, canvas_width, canvas_height)
    return pixel_to_tile(slide_x, slide_y, grid_origin, tile_size_level0)


def visible_grid_lines(
    viewport: tuple[int, int, int, int],
    grid_origin: tuple[int, int],
    tile_size_level0: int,
) -> tuple[list[int], list[int]]:
    """
    Level-0 x/y positions of the grid lines that fall within the viewport,
    for drawing the tile grid overlay on the fly without walking the whole
    grid (SPEC §4.3). The grid is zoom-invariant: these are always level-0
    coordinates, regardless of the viewport's downsample.

    Returns (vertical_lines, horizontal_lines).
    """
    x, y, width, height = viewport
    origin_x, origin_y = grid_origin

    first_col = math.floor((x - origin_x) / tile_size_level0)
    last_col = math.ceil((x + width - origin_x) / tile_size_level0)
    vertical_lines = [origin_x + col * tile_size_level0 for col in range(first_col, last_col + 1)]

    first_row = math.floor((y - origin_y) / tile_size_level0)
    last_row = math.ceil((y + height - origin_y) / tile_size_level0)
    horizontal_lines = [origin_y + row * tile_size_level0 for row in range(first_row, last_row + 1)]

    return (vertical_lines, horizontal_lines)
