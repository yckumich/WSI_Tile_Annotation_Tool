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
3. **Tile selection with merged union outlines + translucent fill.** Clicking
   toggles a tile; adjacent selected tiles merge into one outline instead of
   a lattice of boxes, with holes rendered for deselected interior tiles
   (SPEC §5.3). Selected tiles also get a 50%-opacity gray wash (SPEC §4.3,
   revised from an original outlines-only decision — see SPEC.md for the
   reasoning). Built on `viewport.py`'s `resolve_click_to_tile` and
   `selection.py`'s `compute_boundary_edges`/`compute_selection_outlines`.
4. **Click-and-drag ("paint-stroke") selection.** Dragging across the main
   viewport selects (or erases) every tile the cursor passes over, not just
   start/end — chosen over a rectangle/marquee selection because tumor
   regions are irregular, not axis-aligned boxes (SPEC §8). The value painted
   for the whole stroke is decided once, from the tile under `mousedown`, and
   every tile touched is *set* to that value rather than toggled, so a tile
   doesn't flicker if the cursor re-enters it mid-drag. `selection.py`'s
   `set_tile` replaced the earlier `toggle_tile` (a plain click — mousedown
   with no movement — still behaves like a toggle, just computed from the
   starting tile's state rather than by calling a literal toggle function).
5. **Selection status bar + Reset + Complete/export.** A "Selected: N tiles"
   label refreshed on every render, a Reset button (clears the current
   slide's selection; also now happens automatically when a *new* slide is
   loaded — fixed a latent bug where a previous slide's selection carried
   over), and a Complete button that writes a JSON export via the new
   `export.py` (SPEC §7). The export is deliberately more complete than
   SPEC §7's own metadata list — it's designed to be reloadable later to
   resume an in-progress slide, not just consumed by the downstream
   enrichment analysis, per an explicit ask to make it precise/comprehensive
   enough for that. Added an "Annotator ID" text input to populate that
   metadata field, since nothing collected it before. **Known gap:**
   `slide_filename`/`slide_path` in the export are the real filename/path,
   because `anonymize.py` doesn't exist yet (SPEC §2) — close that before
   using this for real annotation collection. The "load JSON to resume"
   half of this feature is not built yet — only the export side.
   **Naming/overwrite behavior revised (superseded step 5's original
   choice):** writes to `annotations/{slide_stem}_{annotator_slug}_
   {timestamp}.json` (e.g. `8130_Reva-Basho_20260711T212640Z.json`) instead
   of a single `{slide_stem}.json` overwritten in place — so multiple saves
   sort chronologically by filename and never clobber each other.
   `export.py`'s `build_slide_metadata` now takes `timestamp` as an explicit
   param (was generated internally via `datetime.now()`) so the same
   instant drives both the JSON content and the filename. A future "resume"
   loader will need to pick the latest file for a given slide stem (sort by
   filename, take the last).
6. **`tiles` list holds only selected tiles, not the full grid.** Originally
   wrote all ~39K tiles per slide (SPEC §7's original wording); revised
   because the unselected background set doesn't need to be written out
   literally to stay recoverable — `grid_origin`/`tile_size_level0`/
   `n_rows`/`n_cols` in the slide metadata are enough to regenerate the full
   grid at analysis time, and anything not in `tiles` is unselected by
   definition. SPEC.md §7 updated to match. Cut a 5-selected-tile test
   export from ~5.8MB to ~1.2KB.
7. **Resume from an exported JSON.** The file chooser now accepts `.json` as
   well as WSI files (title changed "Select WSI File" → "Choose file", Load
   button "Load Slide" → "Load"); `on_load_click` dispatches on file
   extension. Loading a JSON runs, in order: (1) `export.py`'s new
   `validate_annotation_payload` — structural check that every field
   resuming actually depends on is present, and that `export_schema_version`
   matches; (2) resolve the referenced slide — try the recorded
   `slide_path`, fall back to `SLIDES_DIR / slide_filename` (handles the
   common case of the file moving or the JSON being opened on a different
   machine/cwd); (3) once opened, compare the *live* slide's dimensions,
   plus the app's current `TILE_SIZE_LEVEL0`/`GRID_ORIGIN`, against what the
   JSON recorded — a mismatch on any of these means "pathologist tile
   (12, 30)" could silently be different tissue than "model tile (12, 30)"
   (SPEC §6.4), so it's a hard failure, not a warning; (4) only if all of
   that passes are `tiles` added to `selected_tiles` and the slide
   committed as active. Every failure mode prints exactly which check
   failed and tells the user to choose a different file — verified for all
   six paths (happy path, missing required field, slide not found, path
   fallback resolution, dimension mismatch, grid-config mismatch) with a
   simulation script against the real `8130.svs`.

   The candidate slide is opened into a local variable and only swapped
   into `state` (via a new shared `_commit_slide` helper, also now used by
   the plain-WSI load path) once every check has passed — so a bad JSON can
   never destroy a still-valid slide/selection you already had loaded.
   Per instruction, the Annotator ID field is left untouched on a JSON
   load — the file's own `annotator_id` is never read back into the input.

Git initialized; first commit covers steps 1-3 above (steps 4-7 not yet
committed). `grid.py`, `viewport.py`'s navigation/click-resolution
functions, and `export.py` are fully implemented. Not started yet:
`anonymize.py` and `zoom.py` (the slider still shows a raw downsample
factor, not a magnification). See per-file status notes below.

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

**Status: done.** All six functions below are implemented and driving the
current viewer. Two signature deviations from the original plan:
`canvas_to_slide_coords` and `resolve_click_to_tile` gained explicit
`canvas_width`/`canvas_height` params, since the planned 3/5-arg signatures
didn't carry enough information to recover the downsample factor from a
level-0-sized viewport tuple alone.

Ties the overview map, the main viewport, and the click → tile chain together.

- `compute_viewport(center_x, center_y, downsample, canvas_width, canvas_height)` — level-0 region `(x, y, width, height)` to render, given a center point + zoom + canvas size. ✅
- `clamp_viewport(viewport, slide_width, slide_height)` — keep the viewport within slide bounds. ✅
- `canvas_to_slide_coords(canvas_x, canvas_y, viewport, canvas_width, canvas_height)` — click chain step 1–2: canvas pixel → level-0 slide coordinate (§5.2). ✅
- `resolve_click_to_tile(canvas_x, canvas_y, viewport, canvas_width, canvas_height, grid_origin, tile_size_level0)` — full click chain: canvas pixel → level-0 coordinate → `(row, col)` (§5.2). Composes `canvas_to_slide_coords` + `pixel_to_tile`. ✅
- `compute_viewport_rect_on_map(viewport, slide_width, slide_height, thumbnail_width, thumbnail_height)` — rectangle (thumbnail pixel coords) marking the current viewport on the overview map; shrinks/grows with zoom (§4.1). ✅
- `map_click_to_center(map_x, map_y, thumbnail_width, thumbnail_height, slide_width, slide_height)` — click on the overview map → level-0 point the main viewport should recenter on (§4.1, click-to-recenter / drag-to-pan). ✅
- `visible_grid_lines(viewport, grid_origin, tile_size_level0)` — row/column grid-line positions, computed on the fly for only the visible region (§4.3). ✅

---

## `selection.py` — selection state and union-outline geometry (SPEC §5)

**Status: mostly done**, with deviations from the original plan. `toggle_tile`
was replaced by `set_tile(selected_tiles, row, col, selected)`, which sets
membership explicitly rather than flipping it — needed once drag-to-paint
landed (SPEC §8): a plain click still computes a toggle (from the tile's
state at the start of the click), but a paint stroke needs to set every tile
it passes over to the *same* value or a re-visited tile would flicker.
`find_connected_components` and `trace_outline_polygons` were skipped
entirely: since annotation is drawn as outlines/wash, never a filled
polygon, a flat list of boundary line segments renders identically to
stitched closed polygons and is much simpler — see `compute_selection_outlines`.
Revisit `trace_outline_polygons` only if something later needs actual closed
polygon geometry (e.g. exporting outline shapes, not just tile records).

The behavioral core of the tool. Selection is a set of `(row, col)`; rendering
is a pure function of that set (§6.5).

- `set_tile(selected_tiles, row, col, selected)` — set membership explicitly, in place (§5.1, §8). ✅
- ~~`find_connected_components(selected_tiles)`~~ — skipped, not needed for outline-only (no-fill) rendering.
- `compute_boundary_edges(selected_tiles)` — the set of grid edges that separate a selected tile from an unselected/outside one (§5.3 formal definition). ✅
- ~~`trace_outline_polygons(edges)`~~ — skipped; `compute_selection_outlines` draws boundary edges as independent line segments instead of stitching closed loops.
- `compute_selection_outlines(selected_tiles, grid_origin, tile_size_level0)` — top-level: selection set → outline line segments in level-0 pixel coordinates, ready to hand to the renderer. ✅

---

## `export.py` — JSON export on Complete (SPEC §7)

**Status: done**, with signature deviations from the original plan:
`slide_id` became `slide_path` (no `anonymize.py` yet, see the known-gap note
in Progress above), and `downsample`/`n_rows`/`n_cols` are no longer caller
params — `build_slide_metadata` derives `n_rows`/`n_cols` itself via
`grid.py`'s `compute_grid_dimensions` (so they can't drift out of sync with
the grid params in the same call) and adds a `tile_downsample` field
(`tile_size_level0 / tile_size_native`) instead of taking it as an opaque
input. Also added an `export_schema_version` field and a `timestamp` (UTC,
set at write time) not in the original bullet list, both aimed at the
future "reload this JSON to resume" feature — schema version so a future
loader can detect an incompatible file, timestamp so multiple saves of the
same slide are distinguishable.

**Also revised from SPEC §7's original wording:** `build_tile_records` now
writes only *selected* tiles, not the full grid, and dropped the now-unused
`n_rows`/`n_cols` params as a result. The unselected background set the
enrichment analysis needs is still fully recoverable — `grid_origin`,
`tile_size_level0`, `n_rows`, `n_cols` in the slide metadata are enough to
regenerate every `(row, col)` in the grid, so anything absent from `tiles`
is unselected by definition. This cut a 5-tile test export from ~5.8MB
(full grid) to ~1.2KB. SPEC.md §7 updated to match.

- `build_slide_metadata(slide_path, slide_width, slide_height, tile_size_level0, tile_size_native, native_magnification, mpp, grid_origin, annotator_id, tool_version, timestamp)` — assemble the slide-level metadata block (§7). `timestamp` is caller-supplied (ISO string) rather than generated internally, so `widgets/viewer.py` can reuse the same instant for the export filename. ✅
- `build_tile_records(grid_origin, tile_size_level0, selected_tiles)` — one record per *selected* tile — `row`, `col`, `x`, `y`, `width`, `height`, `selected` (§7, revised). ✅
- `export_annotations(output_path, slide_metadata, tile_records)` — write the combined metadata + tile records to a single JSON file (§7). ✅

**Import/resume, not in the original plan** (v1 scope didn't include resuming — added once asked for): `validate_annotation_payload(data)` — structural check (required `slide_metadata` fields present, `export_schema_version` matches, `tiles` well-formed) on a loaded annotation file, *before* anything tries to reopen the slide it points to. Returns a list of human-readable problems, empty if clean. Deliberately checks only fields resuming actually depends on (e.g. not `mpp`/`annotator_id`, which are informational). The slide-exists/dimensions/grid-config checks that follow schema validation live in `widgets/viewer.py`'s `load_annotation_json`, not here — they need `slide_io.py`'s `open_slide`, and orchestrating "open a candidate slide, compare, maybe roll back" is app-layer flow control, not pure validation. ✅

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
