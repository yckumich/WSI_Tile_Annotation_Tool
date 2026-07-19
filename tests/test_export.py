"""Unit tests for utils/export.py."""

import json

from utils.export import (
    EXPORT_SCHEMA_VERSION,
    build_cluster_records,
    export_annotations,
    validate_annotation_payload,
)
from utils.selection import create_cluster_state, select_tile

GRID_ORIGIN = (0, 0)
TILE_SIZE = 448


def _valid_slide_metadata(version=EXPORT_SCHEMA_VERSION):
    return {
        "export_schema_version": version,
        "slide_path": "/slides/foo.svs",
        "slide_filename": "foo.svs",
        "slide_width": 1000,
        "slide_height": 1000,
        "tile_size_level0": 448,
        "grid_origin": [0, 0],
        "n_rows": 10,
        "n_cols": 10,
    }


def test_build_cluster_records_shape_and_pixel_coords():
    state = create_cluster_state()
    select_tile(state, 0, 0)
    select_tile(state, 0, 1)
    cluster_id = state["tile_to_cluster"][(0, 0)]
    state["clusters"][cluster_id]["note"] = "a note"

    records = build_cluster_records(GRID_ORIGIN, TILE_SIZE, state)

    assert len(records) == 1
    record = records[0]
    assert record["tile_count"] == 2
    assert record["color"] == state["clusters"][cluster_id]["color"]
    assert record["note"] == "a note"
    assert record["tiles"] == [
        {"row": 0, "col": 0, "x": 0, "y": 0, "width": TILE_SIZE, "height": TILE_SIZE, "selected": True},
        {"row": 0, "col": 1, "x": TILE_SIZE, "y": 0, "width": TILE_SIZE, "height": TILE_SIZE, "selected": True},
    ]


def test_build_cluster_records_orders_by_cluster_id_and_tiles_by_row_col():
    state = create_cluster_state()
    select_tile(state, 5, 5)  # cluster id 0
    select_tile(state, 0, 0)  # cluster id 1
    select_tile(state, 0, 2)  # cluster id 2 -- not adjacent to (0, 0)

    records = build_cluster_records(GRID_ORIGIN, TILE_SIZE, state)

    assert len(records) == 3
    # Records follow cluster_id creation order (0, 1, 2), not tile position.
    assert [r["tiles"][0]["row"] for r in records] == [5, 0, 0]
    assert [r["tiles"][0]["col"] for r in records] == [5, 0, 2]


def test_build_cluster_records_empty_state():
    state = create_cluster_state()
    assert build_cluster_records(GRID_ORIGIN, TILE_SIZE, state) == []


def test_export_annotations_writes_clusters_key(tmp_path):
    output_path = tmp_path / "test.json"
    slide_metadata = {"some": "metadata"}
    cluster_records = [{"tiles": [], "tile_count": 0, "color": "#000000", "note": ""}]

    export_annotations(str(output_path), slide_metadata, cluster_records)

    with open(output_path) as f:
        payload = json.load(f)

    assert payload["slide_metadata"] == slide_metadata
    assert payload["clusters"] == cluster_records
    assert "tiles" not in payload


def test_validate_accepts_well_formed_v2_payload():
    payload = {
        "slide_metadata": _valid_slide_metadata(),
        "clusters": [
            {"tiles": [{"row": 0, "col": 0}], "tile_count": 1, "color": "#e62222", "note": ""},
        ],
    }
    assert validate_annotation_payload(payload) == []


def test_validate_rejects_v1_shaped_payload():
    # v1 payloads have export_schema_version 1 and a top-level "tiles" list
    # instead of "clusters" -- both should be flagged, not silently misread.
    payload = {
        "slide_metadata": _valid_slide_metadata(version=1),
        "tiles": [{"row": 0, "col": 0}],
    }

    problems = validate_annotation_payload(payload)

    assert any("export_schema_version" in p for p in problems)
    assert any("clusters" in p for p in problems)


def test_validate_reports_missing_cluster_fields():
    payload = {
        "slide_metadata": _valid_slide_metadata(),
        "clusters": [{"tiles": []}],  # missing tile_count, color, note
    }

    problems = validate_annotation_payload(payload)

    assert any("tile_count" in p for p in problems)
    assert any("color" in p for p in problems)
    assert any("note" in p for p in problems)


def test_validate_reports_tile_missing_row_or_col():
    payload = {
        "slide_metadata": _valid_slide_metadata(),
        "clusters": [{"tiles": [{"col": 0}], "tile_count": 1, "color": "#e62222", "note": ""}],
    }

    problems = validate_annotation_payload(payload)

    assert any("missing 'row' or 'col'" in p for p in problems)


def test_validate_reports_missing_clusters_key():
    payload = {"slide_metadata": _valid_slide_metadata()}

    problems = validate_annotation_payload(payload)

    assert "missing top-level 'clusters' key" in problems
