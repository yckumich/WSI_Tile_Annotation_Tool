"""Toggle state and union-outline geometry (SPEC §5).

The behavioral core of the tool. Selection is a set of (row, col); rendering
is a pure function of that set (§6.5).
"""

from utils.grid import tile_to_pixel

_SIDES = ("top", "bottom", "left", "right")


def set_tile(
    selected_tiles: set[tuple[int, int]], row: int, col: int, selected: bool
) -> set[tuple[int, int]]:
    """
    Set a tile's membership explicitly, in place.

    A plain click still behaves like a toggle (SPEC §5.1) — the caller
    decides `selected` from the tile's state at the start of the
    click/drag — but a drag-to-paint stroke needs to *set* every tile it
    passes over to that same value rather than flip each one individually,
    or a tile would flicker on/off if the cursor re-entered it mid-drag
    (SPEC §8).
    """
    tile = (row, col)
    if selected:
        selected_tiles.add(tile)
    else:
        selected_tiles.discard(tile)
    return selected_tiles


def compute_boundary_edges(
    selected_tiles: set[tuple[int, int]],
) -> set[tuple[int, int, str]]:
    """
    The set of grid edges that separate a selected tile from an
    unselected/outside one (SPEC §5.3 formal definition). Shared edges
    between two adjacent selected tiles are never included, so adjacent
    selections merge into one outline instead of a lattice of boxes.

    Each edge is (row, col, side) — one side ("top"/"bottom"/"left"/"right")
    of tile (row, col). 4-connected adjacency (edge-sharing only): a tile
    touching only at a corner does not cancel an edge.
    """
    neighbor_offset = {"top": (-1, 0), "bottom": (1, 0), "left": (0, -1), "right": (0, 1)}
    edges = set()
    for row, col in selected_tiles:
        for side in _SIDES:
            dr, dc = neighbor_offset[side]
            if (row + dr, col + dc) not in selected_tiles:
                edges.add((row, col, side))
    return edges


def compute_selection_outlines(
    selected_tiles: set[tuple[int, int]],
    grid_origin: tuple[int, int],
    tile_size_level0: int,
) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """
    Selection set -> outline geometry in level-0 pixel coordinates, ready to
    hand to the renderer (SPEC §5.3-§5.4). Returns a flat list of line
    segments ((x0, y0), (x1, y1)) rather than stitched closed polygons —
    since annotation is drawn as outlines only, never fills, a segment list
    is sufficient to render correctly (including holes, which fall out
    automatically: a deselected interior tile's edges are boundary edges too).
    """
    segments = []
    for row, col, side in compute_boundary_edges(selected_tiles):
        x, y = tile_to_pixel(row, col, grid_origin, tile_size_level0)
        size = tile_size_level0
        if side == "top":
            segments.append(((x, y), (x + size, y)))
        elif side == "bottom":
            segments.append(((x, y + size), (x + size, y + size)))
        elif side == "left":
            segments.append(((x, y), (x, y + size)))
        elif side == "right":
            segments.append(((x + size, y), (x + size, y + size)))
    return segments
