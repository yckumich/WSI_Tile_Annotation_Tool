# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Behavioral guidelines
Reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## Project status

Pre-implementation. `SPEC.md` is the full design doc; `utils/FUNCTIONS.md` is an
agreed-upon function inventory for `utils/` with no code written yet
(`utils/__init__.py` is empty). There is no build, lint, or test tooling
configured yet — when you add the first modules, also set up `pytest` and wire
it into this file.

Not a git repository yet.

## What this tool is

A local Jupyter/`ipywidgets` tool that lets a pathologist open a single `.svs`
whole-slide image, navigate it (overview map + zoomable/pannable main
viewport), and click tiles to mark diagnostically important regions. On
**Complete** it exports every tile's coordinates and selected/unselected state
to JSON. Read `SPEC.md` in full before implementing — it is the source of
truth; the summary below is only a map to it.

## Environment

- Python venv at `.venv`, deps in `requirements.txt`.
- `openslide-python` requires the system-level `openslide` library installed
  separately (e.g. via Homebrew) — this is the main environment risk called
  out in `SPEC.md` §9.
- Rendering uses `ipycanvas` (or an `Image` widget with a click handler) for
  pixel-accurate click coordinates and cheap redraws without re-reading the
  slide.

## Core architectural constraints (from SPEC.md)

These are non-negotiable design decisions baked into the whole codebase, not
just style preferences:

- **Tiles are virtual.** There is no tiling step, no tile files, no slide
  copies. The grid is a coordinate lattice computed from `(grid_origin,
  tile_size_level0)`. Opening a slide must stay instant — no preprocessing on
  open, only `read_region` reads of the current viewport.
- **All stored/exported tile coordinates are level-0 (full-resolution) pixels**,
  regardless of the zoom the pathologist was viewing at when they clicked.
  Zoom affects rendering only; it never changes the grid. See SPEC §6.2–6.3
  for the forward/inverse grid formulas and the 20x/40x tile-size conversion.
- **The click → tile resolution chain is the most bug-prone part of the tool**
  (SPEC §6.4): canvas pixel → viewport offset → level-0 coordinate →
  `(row, col)`. Pathologist tile `(row, col)` must land on exactly the same
  tissue as the model's tile `(row, col)`, or downstream enrichment analysis
  silently produces garbage. Any change touching grid math or click
  resolution needs unit tests against known coordinates, independent of any
  widget/kernel.
- **Selection state is a plain set of `(row, col)`**, in-memory only, no
  database. Rendering is a pure function of that set plus the current
  viewport/zoom.
- **Selection rendering uses union outlines, not per-tile boxes.** Adjacent
  selected tiles (4-connected, edge-sharing only) merge into one boundary
  around the connected component; deselecting an interior tile creates a
  hole that must be rendered as an internal boundary. Selected tiles also get
  a 50%-opacity gray fill (SPEC §4.3/§5.3, revised from an earlier
  outlines-only decision) — but never a solid/undiluted wash, so tissue
  morphology stays legible through the tint.
- **Export includes every tile in the grid, not just selected ones** — the
  unselected tiles are the background set the enrichment analysis needs.
  Tissue-mask filtering happens at analysis time, not in this tool.
- **Blinding is a hard constraint (SPEC §2).** The pathologist must never see
  the model's attention map, patient ID, clinical metadata, or survival
  group, and slides must be presented under anonymized IDs
  (`slide_001`, ...) with the real filename mapping kept outside anything the
  notebook displays. Do not add any code path that could surface these to the
  UI.

## Planned module layout (`utils/`)

Per `utils/FUNCTIONS.md`, `utils/` is deliberately kept free of
`ipycanvas`/`ipywidgets` imports so the bug-prone math (grid, click
resolution, outline geometry) can be unit-tested with plain Python and no
kernel/widget context. Widget layout, canvas draw calls, and event wiring
belong in the notebook or a separate `app`/`widgets` module, not `utils/`.

- `grid.py` — tile-size conversion (native mag → level-0 px), grid dimensions,
  `tile_to_pixel` / `pixel_to_tile` forward and inverse formulas, bounds
  checks.
- `slide_io.py` — thin OpenSlide wrapper: open, metadata extraction, pyramid
  level selection, thumbnail generation, lazy viewport reads.
- `zoom.py` — conversion between raw downsample factor and displayed
  magnification (pathologists reason in 10x/20x/40x steps), with optional
  snapping to those stops.
- `viewport.py` — pan/zoom viewport computation, overview-map click-to-recenter,
  and the full click → tile resolution chain (composes `grid.py`'s
  `pixel_to_tile`).
- `selection.py` — toggle state, 4-connected component grouping, boundary-edge
  computation, and stitching edges into outline polygons (including holes).
- `export.py` — assembling slide-level metadata + per-tile records and writing
  the Complete-time JSON export.
- `anonymize.py` — filename ↔ anonymized-slide-ID mapping, kept separate from
  anything the notebook renders.

An open design question noted in `utils/FUNCTIONS.md`: whether `GridSpec` /
`Viewport`-style dataclasses are worth introducing once implementation starts,
since several functions across `viewport.py` and `selection.py` need to pass
the same `(grid_origin, tile_size_level0)` or `(x, y, width, height,
downsample)` bundles together.

## Data

`slides/` contains real `.svs` whole-slide image files. Treat these as
patient-derived data: don't move, rename, or expose filenames/metadata in
ways that would defeat the blinding requirements above.
