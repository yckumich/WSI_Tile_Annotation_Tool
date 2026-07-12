# WSI Tile Annotation Tool

An interactive whole-slide-image (WSI) annotation tool for pathologists, built in Jupyter with `ipywidgets`.

A pathologist opens an `.svs` slide, navigates it like a microscope (overview map + zoomable main view), and clicks tiles to mark the regions they consider diagnostically important. Selections are exported as **level-0 tile coordinates** plus the metadata needed to reconstruct those tiles downstream.

The tool never cuts, writes, or copies tiles. Tiles are **virtual** — a coordinate lattice computed on the fly. Only integers are stored.

---

## 1. Scope (v1 / MVP)

### In scope
- Local Jupyter notebook tool, `ipywidgets`-based, run on the annotator's machine.
- Load a single `.svs` slide selected by the user.
- Overview navigation map + zoomable main viewport.
- Virtual tile grid overlay on the main viewport.
- Click-to-toggle tile selection at **any** magnification, with merged cluster outlines.
- Selections held in an in-memory dictionary (no database).
- Export selections + reconstruction metadata to JSON on **Complete**.

### Out of scope (v1)
- The model's attention overlay — deliberately excluded (see §2).
- Tissue masking / background detection.
- Graded or multi-class labels — v1 is **binary**: important / not important.
- Materializing tiles to disk.
- Multi-user, server-hosted, or web deployment.
- Sub-tile annotation (brush, polygons).

---

## 2. Blinding (hard constraint)

The pathologist must **not** see, infer, or accidentally encounter:
- the model's attention map or any high-attention indicator;
- the patient's survival group (short-term / long-term);
- the patient ID, clinical metadata, or a filename encoding either.

If the tool showed her the regions the model already found important, any agreement we later measure would be **confirmation bias** — she'd be confirming what she was shown, not judging independently. She annotates the whole tissue blind; the attention map is joined to her annotations only afterward, in a separate analysis step.

Slides should therefore be presented under **anonymized IDs** (`slide_001`, …), with the mapping held separately and never displayed in the notebook.

---

## 3. User Workflow

1. **Select slide.** The user picks an `.svs` file via a file-browse control.
2. **Open.** The tool opens the slide with `openslide`, reads its level-0 dimensions and pyramid levels, and renders a thumbnail. **No tiling or preprocessing occurs** — the grid is purely computed from `tile_size` and `grid_origin`, so opening is instant.
3. **Navigate.** The overview map shows the whole slide with a rectangle marking the current viewport. Click or drag on the map to pan.
4. **Zoom.** A slider sets the magnification of the main viewport. Zoom changes only what is *rendered*; it never changes the grid.
5. **Annotate.** The main viewport shows tissue at the chosen zoom with a light gray tile grid. Clicking a tile toggles it. Selected tiles get a dark outline; contiguous selected tiles merge into one outline around the region.
6. **Correct.** Clicking a selected tile again deselects it. **Reset** clears all selections for the slide.
7. **Complete.** Exports JSON: per-tile index, level-0 upper-left x/y, and the binary selected flag, plus slide-level metadata.

---

## 4. Interface Layout

```
┌──────────────────────────────────────────────────────────────┐
│  [ Select slide: ▼ browse ]                                  │
├──────────────────────────────────────────────────────────────┤
│  Zoom: [────────●──────────────────]  27.0                   │
├────────────────────┬─────────────────────────────────────────┤
│                    │                                         │
│   OVERVIEW MAP     │          MAIN VIEWPORT                  │
│   (thumbnail)      │                                         │
│   ┌──────────┐     │   Tissue at selected zoom, with         │
│   │  ▓▓▓▓    │     │   gray tile grid overlay.               │
│   │ ▓▓┌──┐▓▓ │     │   Click tiles to select.                │
│   │ ▓▓└──┘▓▓ │     │   Selected clusters outlined in dark.   │
│   │   ▓▓▓▓   │     │                                         │
│   └──────────┘     │                                         │
│                    │                                         │
│  Click or drag to  │                                         │
│  navigate          │                                         │
├────────────────────┴─────────────────────────────────────────┤
│  Selected: 42 tiles        [ Reset ]  [ Complete ]           │
└──────────────────────────────────────────────────────────────┘
```

### 4.1 Overview map
- Downsampled thumbnail of the entire slide, rendered once and cached.
- A **viewport rectangle** shows the region currently in the main view. It shrinks as the user zooms in and grows as they zoom out.
- **Click** recenters the main viewport on that point; **drag** pans continuously.
- The map does **not** display selection progress (v1 decision).

### 4.2 Zoom control
- A continuous slider controls the downsample factor of the main viewport (the mockup reads `27.0`).
- Recommended: display the **equivalent magnification** (e.g. "≈10×") next to the raw factor, since pathologists reason in 10×/20×/40× steps and step through them when reading a slide. Optionally snap the slider to those familiar stops while keeping continuous values available.
- **Zoom affects rendering only.** The tile grid is defined in level-0 coordinates and is invariant to zoom: zooming out shows more, smaller tiles; zooming in shows fewer, larger tiles. The same physical tissue always belongs to the same tile.

### 4.3 Main viewport
- Renders the tissue region determined by map position + zoom, fetched lazily via `openslide.read_region` at the nearest appropriate pyramid level.
- Overlays the tile grid as **light gray lines**, drawn on the fly for the visible region only.
- The grid covers the full slide bounds (no tissue masking in v1), so grid cells will appear over blank glass. This is cosmetic; the pathologist simply won't click them.
- Annotation is drawn as an **outline plus a light translucent wash**: a dark border around the selection (or union of contiguous selections) plus a 50%-opacity gray fill over the selected tiles, so a selection reads at a glance without having to trace the outline. (An earlier draft of this spec called for outlines only, rejecting fills as obscuring morphology; revised after hands-on use showed a 50%-opacity gray wash still leaves tissue detail legible through the tint. A solid, undiluted color wash remains rejected for that reason.)

---

## 5. Selection Semantics

The behavioral core of the tool.

### 5.1 Toggle model
- Each tile has a **binary state**: `selected` or `unselected`. Default `unselected`.
- Clicking **anywhere within** a tile toggles that tile's state.
- **Where inside the tile the click lands is irrelevant** — a click at a corner and a click at the centre are equivalent; both map to the same tile.
- Therefore an **odd** number of clicks on a tile → `selected` (important); an **even** number → `unselected` (not important).
- Clicks are not counted or exported. State is set membership; clicks toggle membership.

### 5.2 Clicking is zoom-invariant
The pathologist can select tiles at **any magnification**. The click-resolution chain is always:

```
canvas pixel  →  viewport offset  →  level-0 slide coordinate  →  (row, col)
```

Because the click is resolved into **level-0 coordinates before** being mapped to a tile, a tile selected while zoomed out is the *same tile* as one selected while zoomed in. At low magnification a tile may be only a few screen pixels wide — a single click still resolves cleanly to exactly one tile.

*Note:* at very low zoom, tiles become small enough that precise clicking is difficult. Consider disabling selection below a minimum zoom, or simply letting the pathologist zoom in when precision matters.

### 5.3 Rendering rules
- **Unselected tile:** thin light-gray border (the base grid).
- **Selected tile fill:** a 50%-opacity gray wash over every selected tile, so selection is visible at a glance; tissue remains discernible through the tint (§4.3).
- **Selected tile (isolated):** thick dark border around the tile.
- **Adjacent selected tiles:** shared internal edges are **not drawn**. The cluster renders as a **single dark outline around the union** of the contiguous tiles — one boundary around the region, not a lattice of boxes.
- **Multiple disjoint clusters:** each gets its own union outline.
- **Deselecting a tile inside a cluster:** creates a **hole (island)**. The rendering shows the union outline *plus* an internal boundary around the hole.

Formally: selected tiles form a set of cells on a grid; the rendered outline is the **boundary of that cell set** — the outer boundary of each connected component, plus the boundary of any enclosed holes. An edge is drawn if and only if it separates a selected cell from an unselected cell (or from outside the grid).

**Adjacency: 4-connected** (edge-sharing). Two tiles touching only at a corner are *not* merged and keep their own outlines. This matches the visual intuition of a contiguous region and is the simpler, less surprising behavior.

### 5.4 Why union outlines matter
A pathologist marking a large tumor region will select dozens of contiguous tiles. Sixty individual boxes would obscure the tissue and read as noise. A single outline keeps the morphology visible and lets her see her annotation as a *region*, which is how pathologists think about tissue.

---

## 6. Data Model

### 6.1 Tiles are virtual
There is **no tiling step, no tile files, and no copy of the slide.** The tile grid is a coordinate lattice defined entirely by `(grid_origin, tile_size)`:

- **Grid overlay:** lines computed on the fly for the visible region.
- **Click:** converted to `(row, col)` by inverting the grid formula.
- **State:** an in-memory dict/set of `(row, col)`.
- **Display:** `openslide.read_region` fetches only the current viewport.
- **Export:** coordinates + grid parameters, from which anyone can regenerate the exact pixel tiles.

This makes opening a slide instant, keeps storage at zero, and means a change to `tile_size` is a one-line change rather than a re-tiling job.

### 6.2 Coordinate system

All stored and exported coordinates are in **level-0 (full-resolution) slide pixels**, always — regardless of the magnification the pathologist was viewing at when they clicked.

Level-0 is chosen because `openslide.read_region(location, level, size)` always takes `location` in level-0 coordinates whatever level it reads. Storing level-0 means exported coordinates can be fed straight back into openslide at any level with no conversion, eliminating a whole class of scale-factor bugs.

A tile is identified by grid index `(row, col)`, mapping deterministically to its level-0 pixel origin:

```
x = grid_origin_x + col * tile_size_level0
y = grid_origin_y + row * tile_size_level0
```

and inversely, for click resolution:

```
col = (x - grid_origin_x) // tile_size_level0
row = (y - grid_origin_y) // tile_size_level0
```

### 6.3 Tile size and the 20× / 40× relationship

The model tiles at **224 × 224 px at 20×**. The source slides are scanned at **40× (~0.25 mpp)**, i.e. level 0 is 40×.

The *same physical tissue* is described by different pixel counts at different levels. A 224px tile at 20× covers **448 level-0 pixels**:

```
tile_size_20x     = 224          # the model's tile, in 20x pixels
downsample        = 2            # level-0 (40x) -> 20x
tile_size_level0  = 448          # same tissue, in level-0 pixels
```

So each grid cell **is** one model tile, but its coordinates are written in level-0 pixels. This gives exact correspondence with the model's tiles *and* clean openslide reads.

> **Must confirm with Reva before collecting any annotations:** the exact `tile_size`, the magnification/level tiles were cut at, the `grid_origin` / offset convention, whether tiles were non-overlapping, and the native magnification of the `.svs` files. If the slides are natively 20×, then level 0 *is* 20× and `tile_size_level0 = 224`.

### 6.4 Alignment requirement (critical)

The grid must reproduce the model pipeline's tiling exactly. If the pathologist's grid is offset by even a few pixels, or uses a different tile size, then "pathologist tile (12, 30)" and "model tile (12, 30)" are **different pieces of tissue**, and the enrichment analysis silently produces garbage.

The click → tile mapping chain is the most bug-prone part of the tool and should be unit-tested against known coordinates.

### 6.5 In-session state
```
selected_tiles : set of (row, col)     # in-memory only, no database
```
Rendering is a pure function of this set plus the current viewport and zoom.

---

## 7. Export Format ("Complete")

Writes a single JSON file.

**Slide-level metadata** — everything needed to reconstruct the tiles:
- anonymized slide ID (and/or filename)
- level-0 slide dimensions
- `tile_size_level0`, and the `tile_size` / magnification the grid corresponds to
- `grid_origin`
- native magnification and mpp
- downsample factor
- grid dimensions (`n_rows`, `n_cols`)
- tool version, timestamp, annotator ID

**Per-tile records:**
- `row`, `col` — tile index
- `x`, `y` — **level-0** upper-left pixel coordinate
- `width`, `height` — `tile_size_level0`
- `selected` — boolean

**The `tiles` list contains only selected tiles** (revised from an earlier draft that wrote every tile in the grid). The unselected tiles still matter — they're the pathologist's implicit judgment that those regions were *not* important, and the enrichment analysis needs that background set to compare against — but they don't need to be written out literally to preserve that signal: `grid_origin`, `tile_size_level0`, `n_rows`, and `n_cols` in the slide-level metadata are enough to regenerate every `(row, col)` in the grid and its pixel coordinates, so any tile *not* present in `tiles` is unambiguously unselected. This keeps the export lossless while avoiding writing tens of thousands of mostly-`selected: false` records.

> Because no tissue mask is applied in v1, the *reconstructed* full grid will include many blank-glass tiles among the unselected background. These should be filtered at **analysis** time (they were never real candidates and would dilute the enrichment statistics), even though they are harmless in the UI.

---

## 8. Open Questions

1. **Drag-to-paint** — should click-and-sweep across multiple tiles be supported? It would greatly speed up marking large tumor regions, but complicates the toggle semantics (a drag over an already-selected tile should probably *set*, not *toggle*). Deferred; revisit after the first pathologist trial.
2. **Autosave** — v1 persists only on **Complete**. With ~37 slides, losing a slide's work to a kernel crash would be costly. Consider periodic autosave and resume.
3. **Minimum zoom for selection** — should clicking be disabled below a zoom where tiles are too small to hit reliably?

---

## 9. Technical Notes

- **Environment:** Jupyter Notebook, Python, `ipywidgets`.
- **Slide I/O:** `openslide-python` — native `.svs` support, pyramidal reads, `read_region` at arbitrary level/location, which is exactly what a zoom + pan viewport needs. Requires a system-level library install (`openslide`), which is the main environment risk. Alternatives: `tifffile`, `large_image`.
- **Rendering:** draw the viewport and grid on an `ipycanvas` canvas (or an `Image` widget with a click handler) — we need pixel-accurate click coordinates and cheap redraws of grid/outlines without re-reading the slide.
- **Performance:** never load the full slide into memory. Fetch only the current viewport via `read_region` at the nearest pyramid level. Cache the thumbnail once. When only the *selection* changes, redraw outlines without re-fetching tissue.

---

## 10. Success Criteria

1. A pathologist annotates a full slide in roughly **5 minutes**, and all ~37 slides in **2–3 hours**.
2. She never sees model attention, patient ID, or survival group.
3. She can view tissue with enough surrounding context, at a magnification she chooses, to judge each region confidently — solving the original "tiles too small / wrong magnification" complaint.
4. Exported coordinates align **exactly** with the model's tiling, so attention-vs-annotation enrichment needs no coordinate reconciliation.
5. The underlying morphology is never obscured by the annotation UI.
