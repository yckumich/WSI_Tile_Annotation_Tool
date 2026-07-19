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
    build_cluster_records,
    build_slide_metadata,
    export_annotations,
    validate_annotation_payload,
)
from utils.grid import compute_tiles_centroid, tile_to_pixel
from utils.selection import (
    all_selected_tiles,
    cluster_state_from_clusters,
    compute_selection_outlines,
    create_cluster_state,
    deselect_tile,
    select_tile,
)
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
SELECTED_FILL_ALPHA = 128  # ~0.5 opacity at 8-bit, per cluster (Cluster Annotations §5)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """"#rrggbb" -> (r, g, b), for PIL's fill= which needs plain numbers, not a hex string."""
    hex_color = hex_color.lstrip("#")
    return (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))


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
        "cluster_state": create_cluster_state(),
        "is_painting": False,
        "paint_value": None,
        "last_painted_tile": None,
        "syncing_pan_sliders": False,
        "note_widgets": {},  # cluster_id -> {"row": HBox, "textarea": Textarea}
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
        placeholder="e.g. Ye Chan Kim",
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
        layout=widgets.Layout(border="1px solid #ccc", cursor="crosshair", margin="0px"),
    )
    # Pan sliders alongside the thumbnail -- an alternative to click/drag for
    # precise navigation. Range/size are set to match the slide/thumbnail
    # once a slide is loaded (see _commit_slide). Vertical slider value
    # increases upward (ipywidgets convention for vertical orientation), so
    # it's inverted against center_y (which increases downward) when read.
    horizontal_pan_slider = widgets.IntSlider(
        value=0, min=0, max=1, step=1, readout=False,
        layout=widgets.Layout(width="300px", margin="0px"),
    )
    vertical_pan_slider = widgets.IntSlider(
        value=0, min=0, max=1, step=1, readout=False, orientation="vertical",
        # ipywidgets' Layout has no "gap" property for HBox/VBox -- spacing
        # between children comes from their own margins, so the small
        # right-margin here is the actual gap to the thumbnail.
        layout=widgets.Layout(height="300px", width="20px", margin="0px 4px 0px 0px"),
    )
    # Horizontal slider stacks directly under the thumbnail (not the vertical
    # slider too), and the vertical slider top-aligns with the thumbnail
    # image itself so its height ends where the horizontal slider begins.
    thumb_and_hslider = widgets.VBox(
        [thumb_widget, horizontal_pan_slider],
        layout=widgets.Layout(align_items="center", margin="0px"),
    )
    thumb_row = widgets.HBox(
        [vertical_pan_slider, thumb_and_hslider],
        layout=widgets.Layout(align_items="flex-start", margin="0px"),
    )
    # ── Cluster notes: one row per currently-existing cluster (color swatch
    #    + free-text note), rebuilt as clusters are created/merged/split/
    #    deleted (Cluster Annotations §7). Populated by sync_note_rows.
    #    Lives under the thumbnail (narrower than the rest of the interface,
    #    but reuses space that's otherwise empty next to the taller main
    #    viewport) rather than below the Reset/Complete row.
    #    Fixed max-height with its own scrollbar -- individual rows/text
    #    boxes are unchanged, but the list of them scrolls internally once
    #    it overflows, instead of growing the whole page as clusters
    #    accumulate.
    note_rows_box = widgets.VBox(
        [], layout=widgets.Layout(margin="10px 0px", width="100%", max_height="450px", overflow="auto")
    )

    thumb_column = widgets.VBox([thumb_row, note_rows_box])

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
        description="Zoom:",
        readout_format=".1f",
        layout=widgets.Layout(width="50%"),
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
        [zoom_slider, image_row, status_row, complete_output],
        layout=widgets.Layout(display="none"),
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
        count = len(all_selected_tiles(state["cluster_state"]))
        tile_word = "tile" if count == 1 else "tiles"
        status_label.value = f"<b>Selected:</b> {count} {tile_word}"

    def render_main_view():
        """Re-render the zoomed main viewport (tissue + grid + selection)."""
        if state["slide"] is None:
            return

        slide = state["slide"]
        downsample = zoom_slider.value

        viewport = current_viewport()
        x0, y0 = viewport[0], viewport[1]

        view_img = read_region_at_size(slide, x0, y0, downsample, VIEW_SIZE, VIEW_SIZE)

        # Translucent per-cluster fill, composited under the grid lines and
        # outline (Cluster Annotations §5-§6: each cluster's tiles are
        # filled in its own color at 50% opacity, replacing the old shared
        # gray wash -- outline geometry itself is unchanged).
        fill_overlay = Image.new("RGBA", view_img.size, (0, 0, 0, 0))
        fill_draw = ImageDraw.Draw(fill_overlay)
        for cluster in state["cluster_state"]["clusters"].values():
            fill_color = _hex_to_rgb(cluster["color"])
            for row, col in cluster["tiles"]:
                tile_x, tile_y = tile_to_pixel(row, col, GRID_ORIGIN, TILE_SIZE_LEVEL0)
                fx0 = round((tile_x - x0) / downsample)
                fy0 = round((tile_y - y0) / downsample)
                fx1 = round((tile_x + TILE_SIZE_LEVEL0 - x0) / downsample)
                fy1 = round((tile_y + TILE_SIZE_LEVEL0 - y0) / downsample)
                fill_draw.rectangle([fx0, fy0, fx1, fy1], fill=fill_color + (SELECTED_FILL_ALPHA,))
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
            all_selected_tiles(state["cluster_state"]), GRID_ORIGIN, TILE_SIZE_LEVEL0
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
        update_status_label()

    def render_thumbnail():
        """
        Re-render the thumbnail overview: selection fill/outline (scaled to
        thumbnail pixels) plus the current viewport rectangle. Split out
        from `render_main_view` so a paint-stroke drag can update the main
        viewport on every tile without also redoing the thumbnail's own
        selection pass and PNG encode on every step -- the thumbnail only
        needs to catch up once the drag ends.
        """
        if state["slide"] is None:
            return

        slide_w = state["slide_w"]
        slide_h = state["slide_h"]
        viewport = current_viewport()

        thumb_scale_x = state["thumb_w"] / slide_w
        thumb_scale_y = state["thumb_h"] / slide_h

        thumb_fill_overlay = Image.new("RGBA", state["thumbnail"].size, (0, 0, 0, 0))
        thumb_fill_draw = ImageDraw.Draw(thumb_fill_overlay)
        for cluster in state["cluster_state"]["clusters"].values():
            fill_color = _hex_to_rgb(cluster["color"])
            for row, col in cluster["tiles"]:
                tile_x, tile_y = tile_to_pixel(row, col, GRID_ORIGIN, TILE_SIZE_LEVEL0)
                tfx0 = tile_x * thumb_scale_x
                tfy0 = tile_y * thumb_scale_y
                tfx1 = (tile_x + TILE_SIZE_LEVEL0) * thumb_scale_x
                tfy1 = (tile_y + TILE_SIZE_LEVEL0) * thumb_scale_y
                thumb_fill_draw.rectangle([tfx0, tfy0, tfx1, tfy1], fill=fill_color + (SELECTED_FILL_ALPHA,))
        thumb_copy = Image.alpha_composite(state["thumbnail"].convert("RGBA"), thumb_fill_overlay)

        outline_segments = compute_selection_outlines(
            all_selected_tiles(state["cluster_state"]), GRID_ORIGIN, TILE_SIZE_LEVEL0
        )
        draw = ImageDraw.Draw(thumb_copy)
        for (seg_x0, seg_y0), (seg_x1, seg_y1) in outline_segments:
            tsx0 = seg_x0 * thumb_scale_x
            tsy0 = seg_y0 * thumb_scale_y
            tsx1 = seg_x1 * thumb_scale_x
            tsy1 = seg_y1 * thumb_scale_y
            draw.line([(tsx0, tsy0), (tsx1, tsy1)], fill=SELECTED_COLOR, width=1)

        rx0, ry0, rx1, ry1 = compute_viewport_rect_on_map(
            viewport, slide_w, slide_h, state["thumb_w"], state["thumb_h"]
        )
        draw.rectangle([rx0, ry0, rx1, ry1], outline="red", width=2)
        thumb_widget.value = pil_to_png_bytes(thumb_copy.convert("RGB"))

    def render_view():
        """Full re-render: main viewport + thumbnail overview + status label."""
        render_main_view()
        render_thumbnail()

    def sync_pan_sliders():
        """
        Reflect state's center_x/center_y onto the pan sliders, without
        re-triggering their own change handlers (which would otherwise
        re-set center_x/center_y from the slider and double-render).
        """
        state["syncing_pan_sliders"] = True
        horizontal_pan_slider.value = state["center_x"]
        vertical_pan_slider.value = state["slide_h"] - state["center_y"]
        state["syncing_pan_sliders"] = False

    # ──────────────────────────────────────────
    # Cluster note rows (Cluster Annotations §7)
    # ──────────────────────────────────────────

    def _make_note_row(cluster_id, cluster):
        """A [color swatch][note textarea] row for one cluster. Typing writes
        straight into cluster_state -- there's no separate save step; the
        text present when Complete is clicked is what gets exported.
        Clicking the swatch recenters the viewport on that cluster."""
        swatch = widgets.Button(
            description="",
            tooltip="Click to view this cluster",
            layout=widgets.Layout(
                width="16px", height="16px", padding="0px", margin="0px 8px 0px 0px", border="1px solid #888"
            ),
            style={"button_color": cluster["color"]},
        )

        def on_swatch_click(b, cluster_id=cluster_id):
            if state["slide"] is None:
                return
            cluster = state["cluster_state"]["clusters"].get(cluster_id)
            if cluster is None:
                return
            center_x, center_y = compute_tiles_centroid(cluster["tiles"], GRID_ORIGIN, TILE_SIZE_LEVEL0)
            state["center_x"] = center_x
            state["center_y"] = center_y
            sync_pan_sliders()
            render_view()

        swatch.on_click(on_swatch_click)

        textarea = widgets.Textarea(
            value=cluster["note"],
            placeholder="Note for this region...",
            # flex, not a fixed width -- grows to fill the row's remaining
            # horizontal space next to the fixed-width swatch.
            layout=widgets.Layout(flex="1 1 auto", height="40px"),
            continuous_update=True,
        )

        def on_note_change(change, cluster_id=cluster_id):
            state["cluster_state"]["clusters"][cluster_id]["note"] = change["new"]

        textarea.observe(on_note_change, names="value")
        row = widgets.HBox(
            [swatch, textarea],
            # flex="0 0 auto" -- without this, note_rows_box (a flex column
            # with a capped height) shrinks every row to fit instead of
            # overflowing into a scrollbar, since flex items default to
            # flex-shrink:1. Pinning each row's own size is what makes the
            # container actually scroll instead of squeezing rows smaller.
            layout=widgets.Layout(align_items="center", margin="4px 0px", width="100%", flex="0 0 auto"),
        )
        return row, textarea

    def sync_note_rows():
        """
        Add a row for every cluster that doesn't have one yet, remove rows
        for clusters that no longer exist, and refresh the displayed text of
        every surviving row to match cluster_state.

        That refresh matters for a merge: the winning cluster keeps its
        existing id/row, but its note changes (joined with the losing
        clusters' notes) without the pathologist typing anything -- without
        this, the row would keep showing its stale pre-merge text. It's safe
        to always push cluster_state's note into the widget because typing
        already keeps the two in sync live (continuous_update), so they can
        only differ right after an external change like this; a mouse-driven
        merge can't happen mid-keystroke in a single browser tab, so this
        never clobbers an in-progress edit.
        """
        clusters = state["cluster_state"]["clusters"]
        current_ids = set(clusters.keys())
        tracked_ids = set(state["note_widgets"].keys())

        for cluster_id in tracked_ids - current_ids:
            removed = state["note_widgets"].pop(cluster_id)
            removed["textarea"].close()
            removed["row"].close()

        for cluster_id in current_ids - tracked_ids:
            row, textarea = _make_note_row(cluster_id, clusters[cluster_id])
            state["note_widgets"][cluster_id] = {"row": row, "textarea": textarea}

        for cluster_id in current_ids:
            textarea = state["note_widgets"][cluster_id]["textarea"]
            current_note = clusters[cluster_id]["note"]
            if textarea.value != current_note:
                textarea.value = current_note

        note_rows_box.children = tuple(state["note_widgets"][cid]["row"] for cid in sorted(current_ids))

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
        sync_pan_sliders()
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
    # Pan sliders (alternative to click/drag on the thumbnail)
    # ──────────────────────────────────────────

    def on_horizontal_pan_change(change):
        if state["syncing_pan_sliders"] or state["slide"] is None:
            return
        state["center_x"] = change["new"]
        render_view()

    def on_vertical_pan_change(change):
        if state["syncing_pan_sliders"] or state["slide"] is None:
            return
        # Vertical slider's value increases upward; center_y increases
        # downward, hence the flip.
        state["center_y"] = state["slide_h"] - change["new"]
        render_view()

    horizontal_pan_slider.observe(on_horizontal_pan_change, names="value")
    vertical_pan_slider.observe(on_vertical_pan_change, names="value")

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
        if state["paint_value"]:
            select_tile(state["cluster_state"], row, col)
        else:
            deselect_tile(state["cluster_state"], row, col)
        # Main viewport only during the drag -- the thumbnail catches up
        # once on mouseup instead of redoing its own selection pass and PNG
        # encode on every tile touched mid-drag.
        render_main_view()

    def handle_view_mouse(event):
        if state["slide"] is None:
            return
        etype = event.get("type", "")

        if etype == "mousedown":
            row, col = resolve_event_to_tile(event)
            state["is_painting"] = True
            state["paint_value"] = (row, col) not in state["cluster_state"]["tile_to_cluster"]
            state["last_painted_tile"] = None
            paint_tile(row, col)
        elif etype == "mouseup":
            state["is_painting"] = False
            state["last_painted_tile"] = None
            render_thumbnail()
            # Once per stroke, not per tile -- mirrors the deferred
            # thumbnail render just above.
            sync_note_rows()
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
        state["cluster_state"] = create_cluster_state()
        state["last_painted_tile"] = None
        render_view()
        sync_note_rows()

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
            cluster_records = build_cluster_records(
                GRID_ORIGIN,
                TILE_SIZE_LEVEL0,
                state["cluster_state"],
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
            export_annotations(str(output_path), slide_metadata, cluster_records)

            print(f"Saved {len(cluster_records)} clusters to {output_path}")

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

        horizontal_pan_slider.max = state["slide_w"]
        horizontal_pan_slider.layout.width = f"{state['thumb_w']}px"
        vertical_pan_slider.max = state["slide_h"]
        vertical_pan_slider.layout.height = f"{state['thumb_h']}px"

        state["center_x"] = state["slide_w"] // 2
        state["center_y"] = state["slide_h"] // 2
        state["cluster_state"] = create_cluster_state()
        state["last_painted_tile"] = None
        sync_pan_sliders()
        sync_note_rows()

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
        state["cluster_state"] = cluster_state_from_clusters(data["clusters"])
        render_view()
        sync_note_rows()

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
