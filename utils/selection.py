"""Toggle state and union-outline geometry (SPEC §5).

The behavioral core of the tool. Selection is a set of (row, col); rendering
is a pure function of that set (§6.5).
"""

from utils.grid import tile_to_pixel

_SIDES = ("top", "bottom", "left", "right")

# Fixed, cycling color palette for clusters (Cluster Annotations §5) — not
# randomly generated, so colors stay visually distinguishable at 50% fill
# opacity, even with many clusters on screen at once. 100 colors: hues
# advance by the golden angle (~137.5 degrees) each step rather than by a
# fixed 360/N slice, so that any *prefix* of the palette -- not just the
# full 100 -- stays well spread around the wheel. That matters because
# clusters draw colors from the front of this list in order, and most
# sessions only ever use the first several: evenly slicing 360/100 would
# make the first few colors drawn (~3.6 degrees apart) nearly identical,
# even though the full palette covers the wheel fine. Saturation/value
# additionally cycle through 3 tiers as hue advances, so even
# similar-hued entries differ in vividness. Generated once via colorsys
# (golden-angle hue-stepping, duplicate hex values skipped), not computed
# at runtime, so the values are a stable, readable literal here.
PALETTE = (
    "#225be6",
    "#93bf56",
    "#a62195",
    "#22e6c5",
    "#bf8f56",
    "#4221a6",
    "#2be622",
    "#bf5679",
    "#2174a6",
    "#d5e622",
    "#a956bf",
    "#21a663",
    "#e64b22",
    "#565fbf",
    "#53a621",
    "#e622a5",
    "#56bbbf",
    "#a68421",
    "#7b22e6",
    "#56bf67",
    "#a62132",
    "#2274e6",
    "#a1bf56",
    "#a521a6",
    "#22e6ac",
    "#bf8256",
    "#3121a6",
    "#44e622",
    "#bf5687",
    "#2185a6",
    "#e6dd22",
    "#9c56bf",
    "#21a652",
    "#e63222",
    "#566cbf",
    "#64a621",
    "#e622be",
    "#56bfb6",
    "#a67321",
    "#6222e6",
    "#56bf5a",
    "#a62143",
    "#228de6",
    "#aebf56",
    "#9421a6",
    "#22e693",
    "#bf7456",
    "#2122a6",
    "#5de622",
    "#bf5694",
    "#2196a6",
    "#e6c422",
    "#8e56bf",
    "#21a641",
    "#e6222c",
    "#567abf",
    "#75a621",
    "#e622d7",
    "#56bfa9",
    "#a66221",
    "#4a22e6",
    "#60bf56",
    "#a62154",
    "#22a6e6",
    "#bcbf56",
    "#8321a6",
    "#22e67a",
    "#bf6756",
    "#2133a6",
    "#75e622",
    "#bf56a2",
    "#21a6a5",
    "#e6ab22",
    "#8156bf",
    "#21a630",
    "#e62245",
    "#5687bf",
    "#86a621",
    "#db22e6",
    "#56bf9b",
    "#a65121",
    "#3122e6",
    "#6dbf56",
    "#a62165",
    "#22bfe6",
    "#bfb556",
    "#7321a6",
    "#22e661",
    "#bf5956",
    "#2144a6",
    "#8ee622",
    "#bf56af",
    "#21a694",
    "#e69222",
    "#7356bf",
    "#23a621",
    "#e6225e",
    "#5695bf",
    "#97a621",
    "#c222e6",
)


def create_cluster_state() -> dict:
    """
    A fresh, empty cluster state (Cluster Annotations §2, §4). `color_pool`
    starts as a copy of the full palette in order, so the first N clusters
    created just draw palette entries 0..N-1 in sequence -- there's nothing
    to special-case for "never used yet" vs "freed": a color is only ever
    freed after the pool has already been drawn from, so by construction
    anything back in the pool is fair game to hand out next (Cluster
    Annotations §5's reuse policy falls out of plain list order).
    """
    return {
        "clusters": {},
        "tile_to_cluster": {},
        "next_id": 0,
        "color_pool": list(PALETTE),
    }


def _adjacent_tiles(row: int, col: int) -> tuple[tuple[int, int], ...]:
    """The 4 edge-sharing neighbors of (row, col) (4-connected, SPEC §5.3)."""
    return ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1))


def select_tile(cluster_state: dict, row: int, col: int) -> dict:
    """
    Select tile (row, col): creates a new 1-tile cluster, grows an adjacent
    one, or merges 2+ adjacent clusters (Cluster Annotations §4.1-4.3).

    No-op if the tile is already selected -- clicking an already-selected
    tile again is a *deselect*, handled by `deselect_tile`, not here.
    """
    tile = (row, col)
    if tile in cluster_state["tile_to_cluster"]:
        return cluster_state

    neighbor_cluster_ids = {
        cluster_state["tile_to_cluster"][neighbor]
        for neighbor in _adjacent_tiles(row, col)
        if neighbor in cluster_state["tile_to_cluster"]
    }

    if len(neighbor_cluster_ids) == 0:
        cluster_id = cluster_state["next_id"]
        cluster_state["next_id"] += 1
        color = cluster_state["color_pool"].pop(0)
        cluster_state["clusters"][cluster_id] = {"tiles": {tile}, "color": color, "note": ""}
        cluster_state["tile_to_cluster"][tile] = cluster_id
    elif len(neighbor_cluster_ids) == 1:
        cluster_id = next(iter(neighbor_cluster_ids))
        cluster_state["clusters"][cluster_id]["tiles"].add(tile)
        cluster_state["tile_to_cluster"][tile] = cluster_id
    else:
        # Merge (Cluster Annotations §4.3): one ordering -- descending
        # tile_count, ties broken by ascending cluster_id -- drives the
        # winner, the note-join order, and the freed-color insertion order,
        # so those three decisions can never disagree with each other.
        merge_order = sorted(
            neighbor_cluster_ids,
            key=lambda cid: (-len(cluster_state["clusters"][cid]["tiles"]), cid),
        )
        winner_id = merge_order[0]
        winner = cluster_state["clusters"][winner_id]

        merged_tiles = set(winner["tiles"])
        notes = [winner["note"]] if winner["note"] else []
        for loser_id in merge_order[1:]:
            loser = cluster_state["clusters"].pop(loser_id)
            merged_tiles |= loser["tiles"]
            if loser["note"]:
                notes.append(loser["note"])
            for loser_tile in loser["tiles"]:
                cluster_state["tile_to_cluster"][loser_tile] = winner_id
            # Front-insert, same as deselect_tile's free -- preferred over
            # never-used palette entries on the next allocation.
            cluster_state["color_pool"].insert(0, loser["color"])

        merged_tiles.add(tile)
        winner["tiles"] = merged_tiles
        winner["note"] = "\n".join(notes)
        cluster_state["tile_to_cluster"][tile] = winner_id

    return cluster_state


def _connected_components(tiles: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """All 4-connected components within `tiles`, as a list of disjoint sets."""
    remaining = set(tiles)
    components = []
    while remaining:
        start = next(iter(remaining))
        visited = {start}
        stack = [start]
        while stack:
            current = stack.pop()
            for neighbor in _adjacent_tiles(*current):
                if neighbor in remaining and neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        components.append(visited)
        remaining -= visited
    return components


def deselect_tile(cluster_state: dict, row: int, col: int) -> dict:
    """
    Deselect tile (row, col): removes it from its cluster, deleting the
    cluster (and freeing its color) if it was the last tile, or splitting it
    into independent clusters if the removal disconnected it (Cluster
    Annotations §4.4).

    No-op if the tile isn't currently selected.
    """
    tile = (row, col)
    if tile not in cluster_state["tile_to_cluster"]:
        return cluster_state

    cluster_id = cluster_state["tile_to_cluster"].pop(tile)
    cluster = cluster_state["clusters"][cluster_id]
    cluster["tiles"].discard(tile)

    if not cluster["tiles"]:
        del cluster_state["clusters"][cluster_id]
        # Insert at the front, matching select_tile's pop(0): a freed color
        # is picked before any never-used palette entry (Cluster
        # Annotations §5's reuse policy), not after.
        cluster_state["color_pool"].insert(0, cluster["color"])
        return cluster_state

    components = _connected_components(cluster["tiles"])
    if len(components) == 1:
        return cluster_state

    # Split: largest piece keeps the original cluster_id/color/note (ties
    # broken by which piece contains the lexicographically smallest tile --
    # deterministic, mirroring merge's tie-break). Every other piece becomes
    # a new cluster: fresh color off the pool, an identical copy of the
    # original note (not divided).
    components.sort(key=lambda component: (-len(component), min(component)))
    largest = components[0]
    original_note = cluster["note"]

    cluster["tiles"] = largest
    # `largest`'s tiles already point at cluster_id in tile_to_cluster --
    # only the split-off pieces below need repointing.

    for piece in components[1:]:
        new_id = cluster_state["next_id"]
        cluster_state["next_id"] += 1
        color = cluster_state["color_pool"].pop(0)
        cluster_state["clusters"][new_id] = {
            "tiles": piece,
            "color": color,
            "note": original_note,
        }
        for piece_tile in piece:
            cluster_state["tile_to_cluster"][piece_tile] = new_id

    return cluster_state


def all_selected_tiles(cluster_state: dict) -> set[tuple[int, int]]:
    """
    Union of every cluster's tiles (Cluster Annotations §6). Outline
    rendering (`compute_boundary_edges` / `compute_selection_outlines`
    below) is unchanged by clustering -- it still operates on one flat set
    of selected tiles regardless of which cluster each belongs to.
    """
    tiles = set()
    for cluster in cluster_state["clusters"].values():
        tiles |= cluster["tiles"]
    return tiles


def cluster_state_from_clusters(clusters_data: list[dict]) -> dict:
    """
    Rebuild a cluster_state from a previously exported "clusters" array
    (Cluster Annotations §3), for resuming a slide from a saved JSON.
    Cluster ids are freshly assigned in list order -- the original
    session's ids aren't preserved across save/reload. `color_pool` starts
    as the palette minus whatever colors the loaded clusters are already
    using, so a cluster created right after resuming doesn't grab a color
    that's already on screen.
    """
    clusters = {}
    tile_to_cluster = {}
    used_colors = set()

    for cluster_id, record in enumerate(clusters_data):
        tiles = {(tile["row"], tile["col"]) for tile in record["tiles"]}
        clusters[cluster_id] = {
            "tiles": tiles,
            "color": record["color"],
            "note": record["note"],
        }
        used_colors.add(record["color"])
        for tile in tiles:
            tile_to_cluster[tile] = cluster_id

    return {
        "clusters": clusters,
        "tile_to_cluster": tile_to_cluster,
        "next_id": len(clusters_data),
        "color_pool": [color for color in PALETTE if color not in used_colors],
    }


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
