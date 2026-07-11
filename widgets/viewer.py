"""Interactive WSI viewer UI: file selection, overview map, zoom/pan (SPEC §3, §4).

Widget layout, canvas/image draw calls, and event wiring live here rather than
in `utils/`, so the grid/viewport math in `utils/` stays unit-testable without
a running kernel.
"""

from pathlib import Path

import ipywidgets as widgets
from IPython.display import display, clear_output
from ipyevents import Event
from ipyfilechooser import FileChooser
from PIL import Image, ImageDraw

from utils.grid import tile_to_pixel
from utils.selection import compute_selection_outlines, toggle_tile
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

THUMB_MAX = 300
VIEW_SIZE = 700

# Hardcoded per SPEC §6.3 pending confirmation of the model's actual tiling
# convention (tile size, magnification/level, and grid origin).
GRID_ORIGIN = (0, 0)
TILE_SIZE_LEVEL0 = 448
GRID_COLOR = (211, 211, 211)  # light gray — visible without obscuring tissue
SELECTED_COLOR = (40, 40, 40)  # dark outline for selected tiles (SPEC §5.3)
SELECTED_WIDTH = 3
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
        "slide_w": 0,
        "slide_h": 0,
        "thumbnail": None,
        "thumb_w": 0,
        "thumb_h": 0,
        "center_x": 0,
        "center_y": 0,
        "is_dragging": False,
        "selected_tiles": set(),
    }

    # ── File chooser ──
    slide_chooser = FileChooser(str(SLIDES_DIR))
    slide_chooser.filter_pattern = ["*.svs", "*.ndpi", "*.tiff", "*.tif"]
    slide_chooser.title = "<b>Select WSI File:</b>"
    slide_chooser.layout.width = "80%"

    # ── Load button ──
    load_button = widgets.Button(
        description="Load Slide",
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

    # ── Controls (hidden until slide is loaded) ──
    controls = widgets.VBox([zoom_slider, image_row], layout=widgets.Layout(display="none"))

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

    def render_view():
        """Re-render the zoomed viewport and the thumbnail overlay."""
        if state["slide"] is None:
            return

        slide = state["slide"]
        downsample = zoom_slider.value
        slide_w = state["slide_w"]
        slide_h = state["slide_h"]

        viewport = compute_viewport(
            state["center_x"], state["center_y"], downsample, VIEW_SIZE, VIEW_SIZE
        )
        x0, y0, _, _ = clamp_viewport(viewport, slide_w, slide_h)

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
    # Click-to-toggle tile selection on the main viewport
    # ──────────────────────────────────────────

    view_event = Event(source=view_widget, watched_events=["click"])
    view_event.prevent_default_action = True

    def handle_view_click(event):
        if state["slide"] is None:
            return

        downsample = zoom_slider.value
        viewport = compute_viewport(
            state["center_x"], state["center_y"], downsample, VIEW_SIZE, VIEW_SIZE
        )
        viewport = clamp_viewport(viewport, state["slide_w"], state["slide_h"])

        canvas_x = event.get("offsetX", 0)
        canvas_y = event.get("offsetY", 0)
        row, col = resolve_click_to_tile(
            canvas_x, canvas_y, viewport, VIEW_SIZE, VIEW_SIZE, GRID_ORIGIN, TILE_SIZE_LEVEL0
        )
        toggle_tile(state["selected_tiles"], row, col)
        render_view()

    view_event.on_dom_event(handle_view_click)

    # ──────────────────────────────────────────
    # Zoom slider change
    # ──────────────────────────────────────────

    def on_zoom_change(change):
        render_view()

    zoom_slider.observe(on_zoom_change, names="value")

    # ──────────────────────────────────────────
    # Load button
    # ──────────────────────────────────────────

    def on_load_click(b):
        load_button.disabled = True
        load_button.description = "Loading..."
        load_button.button_style = "warning"

        with output_area:
            clear_output(wait=True)

            slide_path = slide_chooser.selected
            if not slide_path:
                print("Please select a WSI file.")
                load_button.disabled = False
                load_button.description = "Load Slide"
                load_button.button_style = "success"
                return

            if state["slide"] is not None:
                state["slide"].close()

            print(f"Loading: {Path(slide_path).name}")

            try:
                slide = open_slide(slide_path)
            except Exception as e:
                print(f"Failed to open slide: {e}")
                load_button.disabled = False
                load_button.description = "Load Slide"
                load_button.button_style = "success"
                return

            metadata = get_slide_metadata(slide)
            state["slide"] = slide
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

            mpp_str = f"{metadata['mpp']:.4f} µm/px" if metadata["mpp"] else "N/A"
            obj_str = f"{metadata['objective']}×" if metadata["objective"] else "N/A"

            info_label.value = (
                f"<b>{Path(slide_path).name}</b> — "
                f"{metadata['dimensions'][0]:,} × {metadata['dimensions'][1]:,} px, "
                f"Objective: {obj_str}, "
                f"MPP: {mpp_str}, "
                f"{metadata['level_count']} levels"
            )

            print("Slide loaded.")
            print("  Click on the thumbnail to navigate.")
            print("  Drag on the thumbnail to pan.")
            print("  Use the zoom slider to zoom in/out.")

            controls.layout.display = ""
            render_view()

        load_button.disabled = False
        load_button.description = "Load Slide"
        load_button.button_style = "success"

    load_button.on_click(on_load_click)

    # ── Display ──
    display(slide_chooser, load_button, info_label, output_area, controls)
