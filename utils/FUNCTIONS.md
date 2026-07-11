# Helper Function Plan

Function inventory for `utils/`, derived from SPEC.md. Grouped by the file each
function would live in.

---

## Progress

Implemented so far, step by step, each checked in the running Jupyter session
(`viewer_dev.ipynb`) before moving on:

1. **Slide loading + navigation.** File picker, overview map (click/drag to
   pan), zoom slider, main viewport reading via `openslide`. Lives in
   `widgets/viewer.py` (`create_viewer_ui`), built on `slide_io.py` and
   `viewport.py`.
2. **Zoom-invariant tile grid overlay.** Light-gray grid lines drawn on the
   main viewport, computed from `grid.py` + `viewport.py`. `TILE_SIZE_LEVEL0
   = 448` and `GRID_ORIGIN = (0, 0)` are hardcoded in `widgets/viewer.py`
   pending confirmation of the model's real tiling convention (SPEC §6.3) —
   **must revisit before real annotation collection.**

`grid.py` and `viewport.py`'s navigation functions are fully implemented.
Not started yet: click-to-select tiles (`canvas_to_slide_coords`,
`resolve_click_to_tile`), all of `selection.py`, `export.py`, `anonymize.py`,
and `zoom.py` (the slider still shows a raw downsample factor, not a
magnification). See per-file status notes below.

---

## `grid.py` — virtual tile grid math (SPEC §6.1–6.4)

**Status: done.** All five functions below are implemented in `utils/grid.py`
and round-trip-tested (`tile_to_pixel` → `pixel_to_tile` for arbitrary offsets
inside a tile).

The grid is a coordinate lattice, never materialized to disk. This file is the
most bug-prone part of the tool (§6.4) — pathologist tile `(row, col)` must
land on exactly the same tissue as the model's tile `(row, col)`.

- `compute_tile_size_level0(tile_size_native, downsample)` — convert the model's tile size (224px @ 20x) into level-0 pixels (§6.3).
- `compute_grid_dimensions(slide_width, slide_height, origin_x, origin_y, tile_size_level0)` — compute `(n_rows, n_cols)` needed to cover the full slide from the grid origin.
- `tile_to_pixel(row, col, grid_origin, tile_size_level0)` — tile index → level-0 upper-left pixel coordinate (§6.2 forward formula).
- `pixel_to_tile(x, y, grid_origin, tile_size_level0)` — level-0 pixel coordinate → `(row, col)` (§6.2 inverse formula, used for click resolution).
- `is_tile_in_grid(row, col, n_rows, n_cols)` — bounds check.

---

## `slide_io.py` — OpenSlide wrapper (SPEC §3, §9)

**Status: done**, with one naming deviation from the original plan:
`select_pyramid_level` was folded into `read_region_at_size` (it calls
`slide.get_best_level_for_downsample` internally) rather than being exposed
as its own function, and `read_viewport_region` became
`read_region_at_size(slide, x0, y0, downsample, width, height)`, which reads
*and* resizes to the exact requested pixel size in one call.

Opening a slide must be instant — no tiling or preprocessing. Only the visible
viewport is ever read.

- `open_slide(path)` — open a `.svs` file via `openslide.OpenSlide`. ✅
- `get_slide_metadata(slide)` — extract level-0 dimensions, level count/downsamples, objective power, mpp. ✅
- ~~`select_pyramid_level(slide, desired_downsample)`~~ — folded into `read_region_at_size`. ✅
- `generate_thumbnail(slide, max_size)` — render the overview-map thumbnail once, to be cached (§4.1). ✅
- ~~`read_viewport_region(slide, level0_x, level0_y, level, size)`~~ → `read_region_at_size(slide, x0, y0, downsample, width, height)` — lazily fetch and resize only the current viewport via `read_region` (§4.3, §9). ✅
- `pil_to_png_bytes(img)` — not in the original plan; added for handing rendered frames to `ipywidgets.Image`. ✅

---

## `zoom.py` — magnification ↔ downsample (SPEC §4.2)

**Status: not started.** The zoom slider in `widgets/viewer.py` currently
shows the raw downsample factor only (e.g. `32.0`), not an equivalent
magnification. Revisit if/when the "≈10×" display or standard-stop snapping
is wanted.

Pathologists reason in 10x/20x/40x steps; the viewport is driven by a
continuous downsample factor.

- `downsample_to_magnification(downsample, native_magnification)` — raw factor → displayed magnification (e.g. "≈10x").
- `magnification_to_downsample(magnification, native_magnification)` — inverse.
- `snap_to_standard_zoom(downsample, native_magnification, stops=(10, 20, 40))` — optional snapping to familiar stops while keeping continuous values available.

---

## `viewport.py` — navigation, panning, and click resolution (SPEC §3, §4.1, §5.2)

**Status: navigation done, tile-click resolution not started.**
`compute_viewport`, `clamp_viewport`, `compute_viewport_rect_on_map`,
`map_click_to_center`, and `visible_grid_lines` are implemented and driving
the current viewer. `canvas_to_slide_coords` and `resolve_click_to_tile` are
still pending — they're the next step (clicking a tile *in the main
viewport*, as opposed to clicking the overview map to pan, which is already
done via `map_click_to_center`).

Ties the overview map, the main viewport, and the click → tile chain together.

- `compute_viewport(center_x, center_y, downsample, canvas_width, canvas_height)` — level-0 region `(x, y, width, height)` to render, given a center point + zoom + canvas size. ✅
- `clamp_viewport(viewport, slide_width, slide_height)` — keep the viewport within slide bounds. ✅
- `canvas_to_slide_coords(canvas_x, canvas_y, viewport)` — click chain step 1–2: canvas pixel → level-0 slide coordinate (§5.2). — not started
- `resolve_click_to_tile(canvas_x, canvas_y, viewport, grid_origin, tile_size_level0)` — full click chain: canvas pixel → level-0 coordinate → `(row, col)` (§5.2). Composes `canvas_to_slide_coords` + `pixel_to_tile`. — not started
- `compute_viewport_rect_on_map(viewport, slide_width, slide_height, thumbnail_width, thumbnail_height)` — rectangle (thumbnail pixel coords) marking the current viewport on the overview map; shrinks/grows with zoom (§4.1). ✅
- `map_click_to_center(map_x, map_y, thumbnail_width, thumbnail_height, slide_width, slide_height)` — click on the overview map → level-0 point the main viewport should recenter on (§4.1, click-to-recenter / drag-to-pan). ✅
- `visible_grid_lines(viewport, grid_origin, tile_size_level0)` — row/column grid-line positions, computed on the fly for only the visible region (§4.3). ✅

---

## `selection.py` — toggle state and union-outline geometry (SPEC §5)

**Status: not started.** Next major step after tile-click resolution
(`viewport.py`'s `resolve_click_to_tile`) lands.

The behavioral core of the tool. Selection is a set of `(row, col)`; rendering
is a pure function of that set (§6.5).

- `toggle_tile(selected_tiles, row, col)` — flip membership in place (§5.1: odd clicks → selected, even → unselected).
- `find_connected_components(selected_tiles)` — group selected tiles into 4-connected components (§5.3: edge-sharing only, corner touches don't merge).
- `compute_boundary_edges(selected_tiles)` — the set of grid edges that separate a selected tile from an unselected/outside one (§5.3 formal definition).
- `trace_outline_polygons(edges)` — stitch boundary edges into closed polygon loops — outer boundary per component *and* boundaries around any holes from deselected interior tiles (§5.3: "deselecting a tile inside a cluster creates a hole").
- `compute_selection_outlines(selected_tiles, grid_origin, tile_size_level0)` — top-level: selection set → outline polygons in level-0 pixel coordinates, ready to hand to the renderer.

---

## `export.py` — JSON export on Complete (SPEC §7)

**Status: not started.**

Exports every tile in the grid, not just selected ones — the unselected tiles
are the background set the enrichment analysis needs.

- `build_slide_metadata(slide_id, slide_width, slide_height, tile_size_level0, tile_size_native, native_magnification, mpp, downsample, grid_origin, n_rows, n_cols, annotator_id, tool_version)` — assemble the slide-level metadata block (§7).
- `build_tile_records(grid_origin, tile_size_level0, n_rows, n_cols, selected_tiles)` — one record per tile — `row`, `col`, `x`, `y`, `width`, `height`, `selected` — for the *entire* grid (§7).
- `export_annotations(output_path, slide_metadata, tile_records)` — write the combined metadata + tile records to a single JSON file (§7).

---

## `anonymize.py` — blinding support (SPEC §2)

**Status: not started.**

Keeps the filename ↔ anonymized-ID mapping outside anything the notebook
displays.

- `load_id_mapping(mapping_path)` — load the filename → anonymized-slide-ID table from a file held separately from the notebook.
- `save_id_mapping(mapping_path, mapping)` — persist the mapping table.
- `get_or_assign_anonymized_id(filename, mapping)` — look up (or create) the anonymized ID (e.g. `slide_001`) for a slide filename.

---

## Explicitly *not* in `utils/`

Per SPEC §9, actual widget/canvas drawing (`ipycanvas` draw calls, `ipywidgets`
layout, event wiring) belongs in the notebook or an `app`/`widgets` module, not
`utils/`. Keeping `utils/` free of `ipycanvas`/`ipywidgets` imports means the
grid math, click resolution, and outline geometry — the parts SPEC §6.4 calls
"the most bug-prone" — can be unit-tested with plain Python, no widget/kernel
required.

That module is now `widgets/viewer.py` (`create_viewer_ui()`), tested live via
`viewer_dev.ipynb` at the project root. It currently owns: the file chooser,
Load button, overview map with click/drag panning, zoom slider, and the main
viewport render loop (slide read + grid overlay draw).

---

## Open question this plan surfaces

`resolve_click_to_tile` and `compute_selection_outlines` both need
`(grid_origin, tile_size_level0)` together, and several `viewport.py`
functions pass the same `(x, y, width, height, downsample)` bundle around. Do
we want lightweight dataclasses (`GridSpec`, `Viewport`) once we start
implementing, to avoid every function taking 4–5 loose positional args? Not
decided yet — flagging before we write real code.
