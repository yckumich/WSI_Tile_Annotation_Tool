"""Unit tests for utils/grid.py."""

from utils.grid import compute_tiles_centroid

GRID_ORIGIN = (0, 0)
TILE_SIZE = 448


def test_compute_tiles_centroid_single_tile():
    x, y = compute_tiles_centroid({(0, 0)}, GRID_ORIGIN, TILE_SIZE)
    assert (x, y) == (TILE_SIZE / 2, TILE_SIZE / 2)


def test_compute_tiles_centroid_averages_multiple_tiles():
    # Two tiles side by side: (0, 0) centered at (224, 224), (0, 1) at (672, 224).
    x, y = compute_tiles_centroid({(0, 0), (0, 1)}, GRID_ORIGIN, TILE_SIZE)
    assert x == (224 + 672) / 2
    assert y == 224


def test_compute_tiles_centroid_respects_grid_origin():
    origin = (100, 200)
    x, y = compute_tiles_centroid({(0, 0)}, origin, TILE_SIZE)
    assert (x, y) == (100 + TILE_SIZE / 2, 200 + TILE_SIZE / 2)
