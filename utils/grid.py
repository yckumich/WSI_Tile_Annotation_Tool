"""Virtual tile grid math (SPEC §6.1-6.4).

The grid is a coordinate lattice, never materialized to disk. Grid indices
(row, col) map deterministically to level-0 pixel coordinates and back. This
is the most bug-prone part of the tool (SPEC §6.4): a pathologist tile
(row, col) must land on exactly the same tissue as the model's tile
(row, col), so these formulas must stay pure and simple to keep them
unit-testable.
"""

import math


def compute_tile_size_level0(tile_size_native: int, downsample: float) -> int:
    """Convert the model's tile size (e.g. 224px @ 20x) into level-0 pixels (SPEC §6.3)."""
    return round(tile_size_native * downsample)


def compute_grid_dimensions(
    slide_width: int,
    slide_height: int,
    origin_x: int,
    origin_y: int,
    tile_size_level0: int,
) -> tuple[int, int]:
    """Number of (n_rows, n_cols) needed to cover the full slide from the grid origin."""
    n_cols = math.ceil((slide_width - origin_x) / tile_size_level0)
    n_rows = math.ceil((slide_height - origin_y) / tile_size_level0)
    return (n_rows, n_cols)


def tile_to_pixel(
    row: int, col: int, grid_origin: tuple[int, int], tile_size_level0: int
) -> tuple[int, int]:
    """Tile index -> level-0 upper-left pixel coordinate (SPEC §6.2 forward formula)."""
    origin_x, origin_y = grid_origin
    x = origin_x + col * tile_size_level0
    y = origin_y + row * tile_size_level0
    return (x, y)


def pixel_to_tile(
    x: int, y: int, grid_origin: tuple[int, int], tile_size_level0: int
) -> tuple[int, int]:
    """Level-0 pixel coordinate -> (row, col) (SPEC §6.2 inverse formula)."""
    origin_x, origin_y = grid_origin
    col = (x - origin_x) // tile_size_level0
    row = (y - origin_y) // tile_size_level0
    return (int(row), int(col))


def is_tile_in_grid(row: int, col: int, n_rows: int, n_cols: int) -> bool:
    """Bounds check."""
    return 0 <= row < n_rows and 0 <= col < n_cols
