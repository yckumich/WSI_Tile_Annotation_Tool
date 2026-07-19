"""JSON export on Complete (Cluster Annotations §3).

Only selected tiles are written, grouped into the `clusters` list -- each
cluster's own `tiles` are always selected. The unselected background set the
enrichment analysis needs isn't lost, though: slide-level metadata carries
`grid_origin`, `tile_size_level0`, `n_rows`, and `n_cols`, which are enough
to regenerate every `(row, col)` in the grid and its pixel coordinates — any
tile not present in any cluster's `tiles` is unambiguously unselected. This
file is also meant to be re-loadable later to resume an in-progress slide,
so the slide-level metadata block carries everything needed to reopen the
exact slide and rebuild the exact grid, not just what's needed for
downstream analysis.
"""

import json
from pathlib import Path

from utils.grid import compute_grid_dimensions, tile_to_pixel

EXPORT_SCHEMA_VERSION = 2


def build_slide_metadata(
    slide_path: str,
    slide_width: int,
    slide_height: int,
    tile_size_level0: int,
    tile_size_native: int,
    native_magnification: float | None,
    mpp: float | None,
    grid_origin: tuple[int, int],
    annotator_id: str,
    tool_version: str,
    timestamp: str,
) -> dict:
    """
    Assemble the slide-level metadata block (SPEC §7). `n_rows`/`n_cols` are
    derived here (via `grid.py`'s `compute_grid_dimensions`) rather than
    taken as params, so they can never drift out of sync with the other grid
    parameters in the same metadata block. `timestamp` is a caller-supplied
    ISO string (rather than generated internally via `datetime.now()`) so the
    same instant can also drive the export filename without the two drifting
    apart by a few milliseconds.
    """
    n_rows, n_cols = compute_grid_dimensions(
        slide_width, slide_height, grid_origin[0], grid_origin[1], tile_size_level0
    )
    return {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "tool_version": tool_version,
        "timestamp": timestamp,
        "annotator_id": annotator_id,
        # No anonymize.py yet (SPEC §2) — storing the real filename/path is
        # a known gap to close before real annotation collection.
        "slide_filename": Path(slide_path).name,
        "slide_path": str(slide_path),
        "slide_width": slide_width,
        "slide_height": slide_height,
        "native_magnification": native_magnification,
        "mpp": mpp,
        "tile_size_level0": tile_size_level0,
        "tile_size_native": tile_size_native,
        "tile_downsample": tile_size_level0 / tile_size_native,
        "grid_origin": list(grid_origin),
        "n_rows": n_rows,
        "n_cols": n_cols,
    }


def _tile_record(row: int, col: int, grid_origin: tuple[int, int], tile_size_level0: int) -> dict:
    """A single tile's row/col/x/y/width/height/selected record (SPEC §7)."""
    x, y = tile_to_pixel(row, col, grid_origin, tile_size_level0)
    return {
        "row": row,
        "col": col,
        "x": x,
        "y": y,
        "width": tile_size_level0,
        "height": tile_size_level0,
        "selected": True,
    }


def build_cluster_records(
    grid_origin: tuple[int, int],
    tile_size_level0: int,
    cluster_state: dict,
) -> list[dict]:
    """
    One record per cluster (Cluster Annotations §3.2): its tiles (row, col,
    x, y, width, height, selected -- always selected: true, since only
    selected tiles are ever written, see module docstring), `tile_count`,
    `color`, and `note`. Clusters are ordered by ascending cluster_id --
    stable across a session even as clusters grow/shrink/merge/split -- and
    each cluster's own tiles are ordered by (row, col).
    """
    records = []
    for cluster_id in sorted(cluster_state["clusters"]):
        cluster = cluster_state["clusters"][cluster_id]
        records.append(
            {
                "tiles": [
                    _tile_record(row, col, grid_origin, tile_size_level0)
                    for row, col in sorted(cluster["tiles"])
                ],
                "tile_count": len(cluster["tiles"]),
                "color": cluster["color"],
                "note": cluster["note"],
            }
        )
    return records


def export_annotations(output_path: str, slide_metadata: dict, cluster_records: list[dict]) -> None:
    """Write the combined metadata + cluster records to a single JSON file (Cluster Annotations §3.1)."""
    payload = {"slide_metadata": slide_metadata, "clusters": cluster_records}
    with open(output_path, "w") as f:
        json.dump(payload, f, indent=2)


# ──────────────────────────────────────────────
# Import (resuming an in-progress slide from a previously exported file)
# ──────────────────────────────────────────────

# Only the fields resuming a slide actually depends on -- not every field
# build_slide_metadata writes (e.g. mpp/annotator_id are informational, not
# load-bearing for reopening the slide and rebuilding the grid).
REQUIRED_SLIDE_METADATA_FIELDS = (
    "export_schema_version",
    "slide_path",
    "slide_filename",
    "slide_width",
    "slide_height",
    "tile_size_level0",
    "grid_origin",
    "n_rows",
    "n_cols",
)


def validate_annotation_payload(data: dict) -> list[str]:
    """
    Structural validation of a loaded annotation JSON, before anything tries
    to reopen the slide it points to or rebuild the grid from it. Returns a
    list of human-readable problems -- empty if the file is well-formed.
    Does *not* check the referenced slide file exists or matches (that
    requires opening it, which is the caller's job once this passes).
    """
    problems = []

    if "slide_metadata" not in data:
        problems.append("missing top-level 'slide_metadata' key")
        slide_metadata = {}
    else:
        slide_metadata = data["slide_metadata"]

    for field in REQUIRED_SLIDE_METADATA_FIELDS:
        if field not in slide_metadata:
            problems.append(f"missing required field 'slide_metadata.{field}'")

    if "export_schema_version" in slide_metadata:
        version = slide_metadata["export_schema_version"]
        if version != EXPORT_SCHEMA_VERSION:
            problems.append(
                f"unsupported export_schema_version {version!r} "
                f"(this tool writes/reads version {EXPORT_SCHEMA_VERSION})"
            )

    if "clusters" not in data:
        problems.append("missing top-level 'clusters' key")
    elif not isinstance(data["clusters"], list):
        problems.append("'clusters' is not a list")
    else:
        for i, cluster in enumerate(data["clusters"]):
            for field in ("tiles", "tile_count", "color", "note"):
                if field not in cluster:
                    problems.append(f"cluster record {i} is missing '{field}'")
            if "tiles" in cluster:
                for tile in cluster["tiles"]:
                    if "row" not in tile or "col" not in tile:
                        problems.append(f"cluster record {i} has a tile missing 'row' or 'col'")
                        break

    return problems
