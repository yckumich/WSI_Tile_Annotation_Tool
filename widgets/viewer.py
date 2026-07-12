"""Interactive WSI viewer UI: file selection, overview map, zoom/pan (SPEC §3, §4).

Widget layout, canvas/image draw calls, and event wiring live here rather than
in `utils/`, so the grid/viewport math in `utils/` stays unit-testable without
a running kernel.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import ipywidgets as widgets
from IPython.display import display, clear_output
from ipyevents import Event
from ipyfilechooser import FileChooser
from PIL import Image, ImageDraw

from utils.export import (
    build_slide_metadata,
    build_tile_records,
    export_annotations,
    validate_annotation_payload,
)
from utils.grid import tile_to_pixel
from utils.selection import compute_selection_outlines, set_tile
from utils.slide_io import (
    open_slide,
    get_slide_metadata,
    generate_thumbnail,
    read_region_at_size,
    pil_to_png_bytes,
)
from utils.viewport import (
    compute_viewport,
    clamp_viewport,
    compute_viewport_rect_on_map,
    map_click_to_center,
    resolve_click_to_tile,
    visible_grid_lines,
)

SLIDES_DIR = Path(__file__).resolve().parent.parent / "slides"
ANNOTATIONS_DIR = Path(__file__).resolve().parent.parent / "annotations"


def _slugify(text: str, fallback: str = "unknown") -> str:
    """Filesystem-safe slug for use in an export filename."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", text.strip()).strip("-")
    return slug or fallback

THUMB_MAX = 300
VIEW_SIZE = 700
TOOL_VERSION = "0.1.0"

# Hardcoded per SPEC §6.3 pending confirmation of the model's actual tiling
# convention (tile size, magnification/level, and grid origin).
GRID_ORIGIN = (0, 0)
TILE_SIZE_LEVEL0 = 448
TILE_SIZE_NATIVE = 224  # the model's tile size @ 20x (SPEC §6.3)
GRID_COLOR = (211, 211, 211)  # light gray — visible without obscuring tissue
SELECTED_COLOR = (40, 40, 40)  # dark outline for selected tiles (SPEC §5.3)
SELECTED_WIDTH = 2
SELECTED_FILL_COLOR = (128, 128, 128)  # translucent gray wash (SPEC §4.3, revised)
SELECTED_FILL_ALPHA = 128  # ~0.5 opacity at 8-bit


def create_viewer_ui():
    """
    Interactive whole-slide-image viewer.

    The user selects a slide file, clicks Load, then:
        - Clicks on the thumbnail to navigate to any region
        - Drags on the thumbnail to pan around
        - Uses the zoom slider to zoom in/out

    Layout:
        - Left:  thumbnail (preserves slide aspect ratio) with a red rectangle
                 showing the current viewport
        - Right: square zoomed view of the current region
        - Above: zoom slider
    """
    # ── State ──
    state = {
        "slide": None,
        "slide_path": None,
        "slide_metadata": None,
        "slide_w": 0,
        "slide_h": 0,
        "thumbnail": None,
        "thumb_w": 0,
        "thumb_h": 0,
        "center_x": 0,
        "center_y": 0,
        "is_dragging": False,
        "selected_tiles": set(),
        "is_painting": False,
        "paint_value": None,
        "last_painted_tile": None,
    }

    # ── File chooser (a WSI file to start fresh, or a previously exported
    #    annotation JSON to resume) ──
    slide_chooser = FileChooser(str(SLIDES_DIR))
    slide_chooser.filter_pattern = ["*.svs", "*.ndpi", "*.tiff", "*.tif", "*.json"]
    slide_chooser.title = "<b>Choose file:</b>"
    slide_chooser.layout.width = "80%"

    # ── Annotator ID (SPEC §7 slide-level metadata) ──
    annotator_input = widgets.Text(
        value="",
        placeholder="e.g. reva",
        description="Annotator ID:",
        layout=widgets.Layout(width="300px"),
        style={"description_width": "100px"},
    )

    # ── Load button ──
    load_button = widgets.Button(
        description="Load",
        button_style="success",
        icon="eye",
        layout=widgets.Layout(margin="10px 0px"),
    )

    # ── Info label ──
    info_label = widgets.HTML(value="<i>No slide loaded.</i>")

    # ── Thumbnail (clickable/draggable navigator) ──
    thumb_widget = widgets.Image(
        format="png",
        layout=widgets.Layout(border="1px solid #ccc", cursor="crosshair"),
    )
    thumb_label = widgets.HTML(
        value="<i style='color:gray; font-size:12px;'>Click or drag on the thumbnail to navigate</i>"
    )
    thumb_column = widgets.VBox([thumb_widget, thumb_label])

    # ── Viewport (square) ──
    view_widget = widgets.Image(
        format="png",
        width=VIEW_SIZE,
        height=VIEW_SIZE,
        layout=widgets.Layout(border="1px solid #ccc"),
    )

    # ── Image row: thumbnail left, viewport right ──
    image_row = widgets.HBox(
        [thumb_column, view_widget], layout=widgets.Layout(margin="10px 0px", gap="20px")
    )

    # ── Zoom slider ──
    zoom_slider = widgets.FloatSlider(
        value=32.0,
        min=1.0,
        max=64.0,
        step=0.5,
        description="Zoom out:",
        readout_format=".1f",
        layout=widgets.Layout(width="80%"),
        style={"description_width": "80px"},
        continuous_update=True,
    )

    # ── Selection status + reset + complete (SPEC §3 steps 6-7) ──
    status_label = widgets.HTML(value="<b>Selected:</b> 0 tiles")
    reset_button = widgets.Button(
        description="Reset",
        button_style="danger",
        icon="trash",
    )
    complete_button = widgets.Button(
        description="Complete",
        button_style="info",
        icon="save",
    )
    status_row = widgets.HBox(
        [status_label, reset_button, complete_button],
        layout=widgets.Layout(margin="10px 0px", align_items="center", gap="20px"),
    )
    complete_output = widgets.Output()

    # ── Controls (hidden until slide is loaded) ──
    controls = widgets.VBox(
        [zoom_slider, image_row, status_row, complete_output], layout=widgets.Layout(display="none")
    )

    # ── Output area ──
    output_area = widgets.Output()

    # ── Mouse events on thumbnail ──
    thumb_event = Event(
        source=thumb_widget,
        watched_events=["click", "mousedown", "mouseup", "mousemove"],
    )
    thumb_event.prevent_default_action = True

    # ──────────────────────────────────────────
    # Rendering
    # ──────────────────────────────────────────

    def current_viewport():
        """Clamped level-0 viewport (x, y, width, height) for the current center/zoom."""
        downsample = zoom_slider.value
        viewport = compute_viewport(
            state["center_x"], state["center_y"], downsample, VIEW_SIZE, VIEW_SIZE
        )
        return clamp_viewport(viewport, state["slide_w"], state["slide_h"])

    def update_status_label():
        count = len(state["selected_tiles"])
        tile_word = "tile" if count == 1 else "tiles"
        status_label.value = f"<b>Selected:</b> {count} {tile_word}"

    def render_view():
        """Re-render the zoomed viewport and the thumbnail overlay."""
        if state["slide"] is None:
            return

        slide = state["slide"]
        downsample = zoom_slider.value
        slide_w = state["slide_w"]
        slide_h = state["slide_h"]

        viewport = current_viewport()
        x0, y0 = viewport[0], viewport[1]

        view_img = read_region_at_size(slide, x0, y0, downsample, VIEW_SIZE, VIEW_SIZE)

        # Translucent gray fill for selected tiles, composited under the grid
        # lines and outline (SPEC §4.3, revised: 50%-opacity wash, not a
        # solid fill, so tissue stays visible through the tint).
        fill_overlay = Image.new("RGBA", view_img.size, (0, 0, 0, 0))
        fill_draw = ImageDraw.Draw(fill_overlay)
        for row, col in state["selected_tiles"]:
            tile_x, tile_y = tile_to_pixel(row, col, GRID_ORIGIN, TILE_SIZE_LEVEL0)
            fx0 = round((tile_x - x0) / downsample)
            fy0 = round((tile_y - y0) / downsample)
            fx1 = round((tile_x + TILE_SIZE_LEVEL0 - x0) / downsample)
            fy1 = round((tile_y + TILE_SIZE_LEVEL0 - y0) / downsample)
            fill_draw.rectangle([fx0, fy0, fx1, fy1], fill=SELECTED_FILL_COLOR + (SELECTED_FILL_ALPHA,))
        view_img = Image.alpha_composite(view_img.convert("RGBA"), fill_overlay).convert("RGB")

        draw_view = ImageDraw.Draw(view_img)
        vertical_lines, horizontal_lines = visible_grid_lines(
            (x0, y0, viewport[2], viewport[3]), GRID_ORIGIN, TILE_SIZE_LEVEL0
        )
        for grid_x in vertical_lines:
            screen_x = round((grid_x - x0) / downsample)
            draw_view.line([(screen_x, 0), (screen_x, VIEW_SIZE)], fill=GRID_COLOR, width=1)
        for grid_y in horizontal_lines:
            screen_y = round((grid_y - y0) / downsample)
            draw_view.line([(0, screen_y), (VIEW_SIZE, screen_y)], fill=GRID_COLOR, width=1)

        # Union outlines: only edges between a selected tile and an
        # unselected/outside one are drawn, so adjacent selected tiles merge
        # into one outline instead of a lattice of boxes (SPEC §5.3).
        outline_segments = compute_selection_outlines(
            state["selected_tiles"], GRID_ORIGIN, TILE_SIZE_LEVEL0
        )
        for (seg_x0, seg_y0), (seg_x1, seg_y1) in outline_segments:
            screen_x0 = round((seg_x0 - x0) / downsample)
            screen_y0 = round((seg_y0 - y0) / downsample)
            screen_x1 = round((seg_x1 - x0) / downsample)
            screen_y1 = round((seg_y1 - y0) / downsample)
            draw_view.line(
                [(screen_x0, screen_y0), (screen_x1, screen_y1)],
                fill=SELECTED_COLOR,
                width=SELECTED_WIDTH,
            )

        view_widget.value = pil_to_png_bytes(view_img)

        thumb_copy = state["thumbnail"].copy()
        draw = ImageDraw.Draw(thumb_copy)

        rx0, ry0, rx1, ry1 = compute_viewport_rect_on_map(
            (x0, y0, viewport[2], viewport[3]),
            slide_w,
            slide_h,
            state["thumb_w"],
            state["thumb_h"],
        )
        draw.rectangle([rx0, ry0, rx1, ry1], outline="red", width=2)
        thumb_widget.value = pil_to_png_bytes(thumb_copy)

        update_status_label()

    # ──────────────────────────────────────────
    # Mouse interaction on thumbnail
    # ──────────────────────────────────────────

    def set_position_from_mouse(event):
        offset_x = event.get("offsetX", 0)
        offset_y = event.get("offsetY", 0)

        center_x, center_y = map_click_to_center(
            offset_x, offset_y, state["thumb_w"], state["thumb_h"], state["slide_w"], state["slide_h"]
        )
        state["center_x"] = center_x
        state["center_y"] = center_y
        render_view()

    def handle_mouse(event):
        etype = event.get("type", "")

        if etype == "mousedown":
            state["is_dragging"] = True
            set_position_from_mouse(event)
        elif etype == "mouseup":
            state["is_dragging"] = False
        elif etype == "mousemove" and state["is_dragging"]:
            set_position_from_mouse(event)
        elif etype == "click":
            set_position_from_mouse(event)

    thumb_event.on_dom_event(handle_mouse)

    # ──────────────────────────────────────────
    # Click-and-drag ("paint-stroke") tile selection on the main viewport
    # ──────────────────────────────────────────
    #
    # A plain click (mousedown with no movement) still behaves like a toggle.
    # A drag paints every tile the cursor passes over to the same value
    # (SPEC §8): if the tile under mousedown was unselected, the whole
    # stroke selects; if it was already selected, the whole stroke erases.
    # This avoids a tile flickering on/off if the cursor re-enters it
    # mid-drag, which a literal per-tile toggle would cause.

    view_event = Event(source=view_widget, watched_events=["mousedown", "mouseup", "mousemove"])
    view_event.prevent_default_action = True

    def resolve_event_to_tile(event):
        viewport = current_viewport()
        canvas_x = event.get("offsetX", 0)
        canvas_y = event.get("offsetY", 0)
        return resolve_click_to_tile(
            canvas_x, canvas_y, viewport, VIEW_SIZE, VIEW_SIZE, GRID_ORIGIN, TILE_SIZE_LEVEL0
        )

    def paint_tile(row, col):
        if (row, col) == state["last_painted_tile"]:
            return
        state["last_painted_tile"] = (row, col)
        set_tile(state["selected_tiles"], row, col, state["paint_value"])
        render_view()

    def handle_view_mouse(event):
        if state["slide"] is None:
            return
        etype = event.get("type", "")

        if etype == "mousedown":
            row, col = resolve_event_to_tile(event)
            state["is_painting"] = True
            state["paint_value"] = (row, col) not in state["selected_tiles"]
            state["last_painted_tile"] = None
            paint_tile(row, col)
        elif etype == "mouseup":
            state["is_painting"] = False
            state["last_painted_tile"] = None
        elif etype == "mousemove" and state["is_painting"]:
            row, col = resolve_event_to_tile(event)
            paint_tile(row, col)

    view_event.on_dom_event(handle_view_mouse)

    # ──────────────────────────────────────────
    # Reset
    # ──────────────────────────────────────────

    def on_reset_click(b):
        if state["slide"] is None:
            return
        state["selected_tiles"].clear()
        state["last_painted_tile"] = None
        render_view()

    reset_button.on_click(on_reset_click)

    # ──────────────────────────────────────────
    # Complete (SPEC §7 export, precise enough to reload and resume later)
    # ──────────────────────────────────────────

    def on_complete_click(b):
        if state["slide"] is None:
            return

        with complete_output:
            clear_output(wait=True)
            now = datetime.now(timezone.utc)
            metadata = state["slide_metadata"]
            slide_metadata = build_slide_metadata(
                slide_path=state["slide_path"],
                slide_width=state["slide_w"],
                slide_height=state["slide_h"],
                tile_size_level0=TILE_SIZE_LEVEL0,
                tile_size_native=TILE_SIZE_NATIVE,
                native_magnification=metadata["objective"],
                mpp=metadata["mpp"],
                grid_origin=GRID_ORIGIN,
                annotator_id=annotator_input.value,
                tool_version=TOOL_VERSION,
                timestamp=now.isoformat(),
            )
            tile_records = build_tile_records(
                GRID_ORIGIN,
                TILE_SIZE_LEVEL0,
                state["selected_tiles"],
            )

            # {slide_filename}_{annotator}_{timestamp}.json, so exports for
            # the same slide sort chronologically by filename and multiple
            # saves never overwrite each other.
            slide_stem = Path(state["slide_path"]).stem
            annotator_slug = _slugify(annotator_input.value)
            timestamp_slug = now.strftime("%Y%m%dT%H%M%SZ")
            output_filename = f"{slide_stem}_{annotator_slug}_{timestamp_slug}.json"

            ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)
            output_path = ANNOTATIONS_DIR / output_filename
            export_annotations(str(output_path), slide_metadata, tile_records)

            print(f"Saved {len(tile_records)} selected tile records to {output_path}")

    complete_button.on_click(on_complete_click)

    # ──────────────────────────────────────────
    # Zoom slider change
    # ──────────────────────────────────────────

    def on_zoom_change(change):
        render_view()

    zoom_slider.observe(on_zoom_change, names="value")

    # ──────────────────────────────────────────
    # Load button — a WSI file starts fresh; an annotation JSON resumes
    # (validated, then the referenced slide is reopened and its selection
    # restored).
    # ──────────────────────────────────────────

    def _commit_slide(slide, slide_path, metadata):
        """
        Make `slide` the active slide: close whatever was open before, reset
        per-slide state (selection, center, thumbnail), and update the info
        label. Only called once a slide is fully validated and ready to
        become the active one, so a failed load never destroys a
        still-valid prior session.
        """
        if state["slide"] is not None:
            state["slide"].close()

        state["slide"] = slide
        state["slide_path"] = str(slide_path)
        state["slide_metadata"] = metadata
        state["slide_w"] = metadata["dimensions"][0]
        state["slide_h"] = metadata["dimensions"][1]

        thumb = generate_thumbnail(slide, max_size=THUMB_MAX)
        state["thumbnail"] = thumb
        state["thumb_w"] = thumb.size[0]
        state["thumb_h"] = thumb.size[1]
        thumb_widget.width = state["thumb_w"]
        thumb_widget.height = state["thumb_h"]

        state["center_x"] = state["slide_w"] // 2
        state["center_y"] = state["slide_h"] // 2
        state["selected_tiles"] = set()
        state["last_painted_tile"] = None

        mpp_str = f"{metadata['mpp']:.4f} µm/px" if metadata["mpp"] else "N/A"
        obj_str = f"{metadata['objective']}×" if metadata["objective"] else "N/A"
        info_label.value = (
            f"<b>{Path(slide_path).name}</b> — "
            f"{metadata['dimensions'][0]:,} × {metadata['dimensions'][1]:,} px, "
            f"Objective: {obj_str}, "
            f"MPP: {mpp_str}, "
            f"{metadata['level_count']} levels"
        )
        controls.layout.display = ""

    def load_wsi_file(slide_path):
        print(f"Loading: {Path(slide_path).name}")
        try:
            slide = open_slide(slide_path)
            metadata = get_slide_metadata(slide)
        except Exception as e:
            print(f"Failed to open slide: {e}")
            return

        _commit_slide(slide, slide_path, metadata)

        print("Slide loaded.")
        print("  Click on the thumbnail to navigate.")
        print("  Drag on the thumbnail to pan.")
        print("  Use the zoom slider to zoom in/out.")
        render_view()

    def load_annotation_json(json_path):
        try:
            with open(json_path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"Could not read '{json_path.name}': {e}")
            print("Choose a different JSON or WSI file.")
            return

        # 1. Structural validation — required fields present, well-formed.
        problems = validate_annotation_payload(data)
        if problems:
            print(f"'{json_path.name}' is missing required data:")
            for problem in problems:
                print(f"  - {problem}")
            print("Choose a different JSON or WSI file.")
            return

        slide_metadata = data["slide_metadata"]

        # 2. Does the referenced slide still exist? Try the recorded path,
        #    then fall back to this project's slides/ folder by filename.
        candidate_paths = [Path(slide_metadata["slide_path"])]
        fallback = SLIDES_DIR / slide_metadata["slide_filename"]
        if fallback not in candidate_paths:
            candidate_paths.append(fallback)

        resolved_slide_path = next((p for p in candidate_paths if p.exists()), None)
        if resolved_slide_path is None:
            tried = ", ".join(str(p) for p in candidate_paths)
            print(f"Referenced slide not found. Tried: {tried}")
            print("Move/restore the slide file, or choose a different JSON.")
            return

        # Open the candidate into a local variable first -- don't touch the
        # currently active slide/state until this one is fully validated,
        # so a bad JSON can't clobber a still-valid prior session.
        try:
            candidate_slide = open_slide(resolved_slide_path)
            candidate_metadata = get_slide_metadata(candidate_slide)
        except Exception as e:
            print(f"Failed to open slide '{resolved_slide_path}': {e}")
            return

        # 3. Does this slide actually match what the JSON was exported for?
        mismatches = []
        slide_w, slide_h = candidate_metadata["dimensions"]
        if slide_w != slide_metadata["slide_width"] or slide_h != slide_metadata["slide_height"]:
            mismatches.append(
                f"slide dimensions {slide_w}x{slide_h} don't match the annotation "
                f"file's recorded {slide_metadata['slide_width']}x{slide_metadata['slide_height']}"
            )
        if slide_metadata["tile_size_level0"] != TILE_SIZE_LEVEL0:
            mismatches.append(
                f"tile_size_level0 in the file ({slide_metadata['tile_size_level0']}) doesn't "
                f"match the tool's current grid ({TILE_SIZE_LEVEL0})"
            )
        if tuple(slide_metadata["grid_origin"]) != GRID_ORIGIN:
            mismatches.append(
                f"grid_origin in the file ({slide_metadata['grid_origin']}) doesn't match "
                f"the tool's current grid ({list(GRID_ORIGIN)})"
            )

        if mismatches:
            candidate_slide.close()
            print(f"'{json_path.name}' doesn't match the slide at '{resolved_slide_path}':")
            for m in mismatches:
                print(f"  - {m}")
            print("Choose a different JSON or WSI file.")
            return

        # 4. Everything checks out -- commit the slide and restore selection.
        #    Quiet on success by design: only failures print, so a routine
        #    resume doesn't clutter the log.
        _commit_slide(candidate_slide, resolved_slide_path, candidate_metadata)
        for tile in data["tiles"]:
            state["selected_tiles"].add((tile["row"], tile["col"]))
        render_view()

    def on_load_click(b):
        load_button.disabled = True
        load_button.description = "Loading..."
        load_button.button_style = "warning"

        with output_area:
            clear_output(wait=True)

            selected_path = slide_chooser.selected
            if not selected_path:
                # Only nag if nothing is loaded yet -- once a slide is
                # active, clicking Load again with no new selection should
                # just leave the current session alone, silently.
                if state["slide"] is None:
                    print("Please choose a file.")
            elif Path(selected_path).suffix.lower() == ".json":
                load_annotation_json(Path(selected_path))
            else:
                load_wsi_file(selected_path)

        load_button.disabled = False
        load_button.description = "Load"
        load_button.button_style = "success"

    load_button.on_click(on_load_click)

    # ── Display ──
    display(slide_chooser, annotator_input, load_button, info_label, output_area, controls)
