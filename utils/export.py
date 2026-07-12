"""JSON export on Complete (SPEC §7).

Only selected tiles are written to the `tiles` list. The unselected
background set the enrichment analysis needs isn't lost, though: slide-level
metadata carries `grid_origin`, `tile_size_level0`, `n_rows`, and `n_cols`,
which are enough to regenerate every `(row, col)` in the grid and its pixel
coordinates — any tile not present in `tiles` is unambiguously unselected.
This file is also meant to be re-loadable later to resume an in-progress
slide, so the slide-level metadata block carries everything needed to
reopen the exact slide and rebuild the exact grid, not just what SPEC §7
lists for downstream analysis.
"""

import json
from pathlib import Path

from utils.grid import compute_grid_dimensions, tile_to_pixel

EXPORT_SCHEMA_VERSION = 1


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


def build_tile_records(
    grid_origin: tuple[int, int],
    tile_size_level0: int,
    selected_tiles: set[tuple[int, int]],
) -> list[dict]:
    """
    One record per *selected* tile — row, col, x, y, width, height, selected
    (SPEC §7, revised: only selected tiles are written, not the full grid —
    see module docstring for how the unselected background is still
    reconstructable from slide-level metadata).
    """
    records = []
    for row, col in sorted(selected_tiles):
        x, y = tile_to_pixel(row, col, grid_origin, tile_size_level0)
        records.append(
            {
                "row": row,
                "col": col,
                "x": x,
                "y": y,
                "width": tile_size_level0,
                "height": tile_size_level0,
                "selected": True,
            }
        )
    return records


def export_annotations(output_path: str, slide_metadata: dict, tile_records: list[dict]) -> None:
    """Write the combined metadata + tile records to a single JSON file (SPEC §7)."""
    payload = {"slide_metadata": slide_metadata, "tiles": tile_records}
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

    if "tiles" not in data:
        problems.append("missing top-level 'tiles' key")
    elif not isinstance(data["tiles"], list):
        problems.append("'tiles' is not a list")
    else:
        for i, tile in enumerate(data["tiles"]):
            if "row" not in tile or "col" not in tile:
                problems.append(f"tile record {i} is missing 'row' or 'col'")
                break

    return problems
