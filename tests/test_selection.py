"""Unit tests for utils/selection.py.

Cluster lifecycle tests (creation/growth/merge/split, color pool, drag-stroke
composability) land here as Step 2 is implemented — see
CLUSTER_ANNOTATIONS_IMPLEMENTATION_STEPS.md Step 5 for the full scenario
list. This file is scaffolding until then.
"""

from utils.selection import (
    PALETTE,
    all_selected_tiles,
    cluster_state_from_clusters,
    create_cluster_state,
    deselect_tile,
    select_tile,
)


def test_create_cluster_state_is_empty():
    state = create_cluster_state()
    assert state["clusters"] == {}
    assert state["tile_to_cluster"] == {}
    assert state["next_id"] == 0
    assert state["color_pool"] == list(PALETTE)


def test_palette_has_at_least_100_distinct_colors():
    assert len(PALETTE) >= 100
    assert len(set(PALETTE)) == len(PALETTE)


def test_select_creates_new_cluster_for_isolated_tile():
    state = create_cluster_state()
    select_tile(state, 5, 5)

    assert len(state["clusters"]) == 1
    cluster_id, cluster = next(iter(state["clusters"].items()))
    assert cluster["tiles"] == {(5, 5)}
    assert cluster["color"] == PALETTE[0]
    assert cluster["note"] == ""
    assert state["tile_to_cluster"] == {(5, 5): cluster_id}
    assert state["color_pool"] == list(PALETTE[1:])


def test_select_grows_existing_cluster_for_adjacent_tile():
    state = create_cluster_state()
    select_tile(state, 5, 5)
    select_tile(state, 5, 6)

    assert len(state["clusters"]) == 1
    cluster = next(iter(state["clusters"].values()))
    assert cluster["tiles"] == {(5, 5), (5, 6)}
    assert cluster["color"] == PALETTE[0]  # unchanged by growth
    assert cluster["note"] == ""
    assert state["color_pool"] == list(PALETTE[1:])  # no new color drawn


def test_select_diagonal_tile_creates_separate_cluster():
    state = create_cluster_state()
    select_tile(state, 5, 5)
    select_tile(state, 6, 6)  # corner-only touch, not edge-adjacent

    assert len(state["clusters"]) == 2


def test_select_is_noop_if_already_selected():
    state = create_cluster_state()
    select_tile(state, 5, 5)
    before = {cid: dict(c, tiles=set(c["tiles"])) for cid, c in state["clusters"].items()}

    select_tile(state, 5, 5)

    assert state["clusters"] == before


def test_select_merges_two_clusters_winner_takes_color():
    state = create_cluster_state()
    select_tile(state, 0, 0)
    select_tile(state, 0, 1)  # cluster A: 2 tiles
    a_id = state["tile_to_cluster"][(0, 0)]
    state["clusters"][a_id]["note"] = "cluster a note"

    select_tile(state, 0, 3)  # cluster B: 1 tile
    b_id = state["tile_to_cluster"][(0, 3)]
    state["clusters"][b_id]["note"] = "cluster b note"

    select_tile(state, 0, 2)  # bridge -- adjacent to both A and B

    assert len(state["clusters"]) == 1
    merged = state["clusters"][a_id]  # A was larger, so A's id/color survive
    assert merged["tiles"] == {(0, 0), (0, 1), (0, 2), (0, 3)}
    assert merged["color"] == PALETTE[0]
    assert merged["note"] == "cluster a note\ncluster b note"
    assert b_id not in state["clusters"]
    assert state["tile_to_cluster"][(0, 3)] == a_id
    assert state["tile_to_cluster"][(0, 2)] == a_id


def test_select_merge_skips_empty_notes():
    state = create_cluster_state()
    select_tile(state, 0, 0)
    select_tile(state, 0, 1)  # cluster A: 2 tiles, note stays ""
    a_id = state["tile_to_cluster"][(0, 0)]

    select_tile(state, 0, 3)  # cluster B: 1 tile
    b_id = state["tile_to_cluster"][(0, 3)]
    state["clusters"][b_id]["note"] = "cluster b note"

    select_tile(state, 0, 2)  # bridge

    assert state["clusters"][a_id]["note"] == "cluster b note"  # no blank line


def test_select_merge_all_empty_notes_stays_empty():
    state = create_cluster_state()
    select_tile(state, 0, 0)
    select_tile(state, 0, 3)
    select_tile(state, 0, 2)  # bridges 2 single-tile clusters, both notes ""

    merged = next(iter(state["clusters"].values()))
    assert merged["note"] == ""


def test_select_merge_tie_break_uses_lowest_cluster_id():
    state = create_cluster_state()
    select_tile(state, 0, 0)  # cluster id 0, 1 tile
    a_id = state["tile_to_cluster"][(0, 0)]
    select_tile(state, 0, 2)  # cluster id 1, 1 tile -- same size, tied
    b_id = state["tile_to_cluster"][(0, 2)]
    assert a_id < b_id  # sanity: ids assigned in creation order

    select_tile(state, 0, 1)  # bridge, adjacent to both -- tied on size

    assert len(state["clusters"]) == 1
    assert state["tile_to_cluster"][(0, 1)] == a_id  # lower id wins the tie
    assert state["clusters"][a_id]["color"] == PALETTE[0]  # a's original color kept


def test_select_three_way_merge_in_one_click():
    state = create_cluster_state()
    select_tile(state, 4, 5)  # north cluster, id 0
    select_tile(state, 6, 5)  # south cluster, id 1
    select_tile(state, 5, 4)  # west cluster, id 2
    # (5, 6), east, deliberately left unselected.

    select_tile(state, 5, 5)  # center bridges all three at once

    assert len(state["clusters"]) == 1
    merged = next(iter(state["clusters"].values()))
    assert merged["tiles"] == {(4, 5), (6, 5), (5, 4), (5, 5)}
    assert merged["color"] == PALETTE[0]  # all tied at 1 tile -> lowest id (north) wins


def test_select_merge_frees_losers_colors_ahead_of_unused_entries():
    state = create_cluster_state()
    select_tile(state, 4, 5)  # PALETTE[0]
    select_tile(state, 6, 5)  # PALETTE[1]
    select_tile(state, 5, 4)  # PALETTE[2]

    select_tile(state, 5, 5)  # 3-way merge -- frees PALETTE[1] and PALETTE[2]

    select_tile(state, 20, 20)  # new cluster should reuse a freed color
    new_cluster = state["clusters"][state["tile_to_cluster"][(20, 20)]]
    assert new_cluster["color"] in (PALETTE[1], PALETTE[2])


def test_deselect_removes_tile_but_keeps_cluster_when_still_connected():
    state = create_cluster_state()
    select_tile(state, 5, 5)
    select_tile(state, 5, 6)

    deselect_tile(state, 5, 6)

    assert len(state["clusters"]) == 1
    cluster = next(iter(state["clusters"].values()))
    assert cluster["tiles"] == {(5, 5)}
    assert cluster["color"] == PALETTE[0]  # unchanged
    assert (5, 6) not in state["tile_to_cluster"]
    assert state["color_pool"] == list(PALETTE[1:])  # not freed -- cluster still alive


def test_deselect_last_tile_deletes_cluster_and_frees_color():
    state = create_cluster_state()
    select_tile(state, 5, 5)

    deselect_tile(state, 5, 5)

    assert state["clusters"] == {}
    assert state["tile_to_cluster"] == {}
    assert state["color_pool"] == list(PALETTE)  # color returned


def test_deselect_is_noop_if_not_selected():
    state = create_cluster_state()
    select_tile(state, 5, 5)
    before_clusters = {cid: dict(c, tiles=set(c["tiles"])) for cid, c in state["clusters"].items()}
    before_pool = list(state["color_pool"])

    deselect_tile(state, 9, 9)  # never selected

    assert state["clusters"] == before_clusters
    assert state["color_pool"] == before_pool


def test_deselect_interior_tile_of_ring_stays_connected():
    # Build a solid 3x3 block as one cluster, in an order where each new
    # tile only ever touches the single already-growing cluster (so this
    # never trips select_tile's not-yet-implemented merge branch).
    state = create_cluster_state()
    for row, col in [
        (0, 0), (0, 1), (0, 2),
        (1, 0), (1, 1), (1, 2),
        (2, 0), (2, 1), (2, 2),
    ]:
        select_tile(state, row, col)
    assert len(state["clusters"]) == 1

    deselect_tile(state, 1, 1)  # remove the center -- leaves a connected ring

    assert len(state["clusters"]) == 1
    cluster = next(iter(state["clusters"].values()))
    assert len(cluster["tiles"]) == 8
    assert (1, 1) not in cluster["tiles"]


def test_deselect_frees_color_ahead_of_never_used_palette_entries():
    state = create_cluster_state()
    select_tile(state, 0, 0)   # PALETTE[0]
    select_tile(state, 5, 5)   # PALETTE[1]
    select_tile(state, 9, 9)   # PALETTE[2]

    deselect_tile(state, 0, 0)  # frees PALETTE[0]

    select_tile(state, 20, 20)  # new cluster -- should reuse the freed color
    new_cluster = state["clusters"][state["tile_to_cluster"][(20, 20)]]
    assert new_cluster["color"] == PALETTE[0]
    # PALETTE[3] (never used yet) is still untouched at the front of the pool
    assert state["color_pool"][0] == PALETTE[3]


def test_deselect_bridge_tile_splits_into_two_equal_pieces():
    state = create_cluster_state()
    select_tile(state, 0, 0)
    select_tile(state, 0, 1)
    select_tile(state, 0, 2)
    original_id = state["tile_to_cluster"][(0, 0)]
    state["clusters"][original_id]["note"] = "original note"

    deselect_tile(state, 0, 1)  # middle tile is the only bridge

    assert len(state["clusters"]) == 2
    # Tied at 1 tile each -- tie-break is lexicographically smallest tile,
    # so the piece containing (0, 0) (not (0, 2)) keeps the original id.
    assert state["clusters"][original_id]["tiles"] == {(0, 0)}
    assert state["clusters"][original_id]["color"] == PALETTE[0]
    assert state["clusters"][original_id]["note"] == "original note"

    other_id = state["tile_to_cluster"][(0, 2)]
    assert other_id != original_id
    assert state["clusters"][other_id]["tiles"] == {(0, 2)}
    assert state["clusters"][other_id]["color"] != PALETTE[0]  # fresh color
    assert state["clusters"][other_id]["note"] == "original note"  # copied, not divided


def test_deselect_split_uneven_sizes_larger_piece_keeps_color():
    state = create_cluster_state()
    for col in range(7):  # a 7-tile strip: (0,0)..(0,6), one cluster
        select_tile(state, 0, col)
    original_id = state["tile_to_cluster"][(0, 0)]

    deselect_tile(state, 0, 5)  # splits into a 5-tile piece and a 1-tile piece

    assert len(state["clusters"]) == 2
    larger = state["clusters"][original_id]
    assert larger["tiles"] == {(0, 0), (0, 1), (0, 2), (0, 3), (0, 4)}
    assert larger["color"] == PALETTE[0]  # larger piece keeps original color

    smaller_id = state["tile_to_cluster"][(0, 6)]
    assert state["clusters"][smaller_id]["tiles"] == {(0, 6)}
    assert state["clusters"][smaller_id]["color"] != PALETTE[0]


def test_deselect_center_tile_splits_into_four():
    state = create_cluster_state()
    select_tile(state, 5, 5)  # center
    select_tile(state, 4, 5)  # north
    select_tile(state, 6, 5)  # south
    select_tile(state, 5, 4)  # west
    select_tile(state, 5, 6)  # east
    assert len(state["clusters"]) == 1

    deselect_tile(state, 5, 5)  # removing the center leaves 4 isolated arms

    assert len(state["clusters"]) == 4
    for tile in [(4, 5), (6, 5), (5, 4), (5, 6)]:
        cluster_id = state["tile_to_cluster"][tile]
        assert state["clusters"][cluster_id]["tiles"] == {tile}


def test_all_selected_tiles_is_union_of_all_clusters():
    state = create_cluster_state()
    select_tile(state, 0, 0)
    select_tile(state, 0, 1)  # same cluster as (0, 0)
    select_tile(state, 5, 5)  # separate cluster

    assert all_selected_tiles(state) == {(0, 0), (0, 1), (5, 5)}


def test_all_selected_tiles_empty_state():
    assert all_selected_tiles(create_cluster_state()) == set()


def test_cluster_state_from_clusters_rebuilds_tiles_and_metadata():
    clusters_data = [
        {
            "tiles": [{"row": 0, "col": 0}, {"row": 0, "col": 1}],
            "tile_count": 2,
            "color": "#e62222",
            "note": "cluster a",
        },
        {
            "tiles": [{"row": 5, "col": 5}],
            "tile_count": 1,
            "color": "#bf6a56",
            "note": "",
        },
    ]

    state = cluster_state_from_clusters(clusters_data)

    assert len(state["clusters"]) == 2
    assert state["clusters"][0]["tiles"] == {(0, 0), (0, 1)}
    assert state["clusters"][0]["color"] == "#e62222"
    assert state["clusters"][0]["note"] == "cluster a"
    assert state["clusters"][1]["tiles"] == {(5, 5)}
    assert state["clusters"][1]["note"] == ""
    assert state["tile_to_cluster"] == {(0, 0): 0, (0, 1): 0, (5, 5): 1}
    assert state["next_id"] == 2


def test_cluster_state_from_clusters_excludes_used_colors_from_pool():
    clusters_data = [
        {"tiles": [{"row": 0, "col": 0}], "tile_count": 1, "color": PALETTE[0], "note": ""},
        {"tiles": [{"row": 5, "col": 5}], "tile_count": 1, "color": PALETTE[2], "note": ""},
    ]

    state = cluster_state_from_clusters(clusters_data)

    assert PALETTE[0] not in state["color_pool"]
    assert PALETTE[2] not in state["color_pool"]
    assert PALETTE[1] in state["color_pool"]
    assert len(state["color_pool"]) == len(PALETTE) - 2


def test_cluster_state_from_clusters_empty_list():
    state = cluster_state_from_clusters([])

    assert state["clusters"] == {}
    assert state["tile_to_cluster"] == {}
    assert state["next_id"] == 0
    assert state["color_pool"] == list(PALETTE)


def test_cluster_state_from_clusters_round_trips_with_select_tile():
    # Rebuilt state should behave exactly like a live one -- e.g. a new
    # selection adjacent to a resumed cluster should grow it, not create a
    # separate one.
    clusters_data = [
        {"tiles": [{"row": 0, "col": 0}], "tile_count": 1, "color": PALETTE[0], "note": "note"},
    ]
    state = cluster_state_from_clusters(clusters_data)

    select_tile(state, 0, 1)  # adjacent to the resumed cluster

    assert len(state["clusters"]) == 1
    assert state["clusters"][0]["tiles"] == {(0, 0), (0, 1)}
    assert state["clusters"][0]["note"] == "note"
