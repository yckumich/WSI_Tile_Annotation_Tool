# WSI Tile Annotation Tool

A local Jupyter tool for pathologists to review a whole-slide image (`.svs`) and
mark diagnostically important regions, tile by tile. Adjacent selected tiles
automatically form a colored **region**, and each region gets its own note box
for recording the reasoning behind it. On **Complete**, everything is exported
to a timestamped JSON file for downstream analysis.

 [Watch the walkthrough]

## Requirements

- Python 3.10 or 3.11
- The [OpenSlide](https://openslide.org/download/) system library
  - **macOS:** `brew install openslide`
  - **Windows:** download the Windows binaries from the link above, extract
    them, and add the extracted `bin` folder to your PATH

## 1. Clone the repo

```bash
git clone https://github.com/yckumich/WSI_Tile_Annotation_Tool.git
cd WSI_Tile_Annotation_Tool
```

## 2. Add a slide

Copy the `.svs` whole-slide image you want to annotate into the `slides/`
folder. Slide files aren't tracked in git (they're large, patient-derived
data — see `.gitignore`), so this step is always manual and local to your
machine.

## 3. Run setup

```bash
# macOS / Linux
bash setup.sh

# Windows
setup.bat
```

This finds a compatible Python, checks for OpenSlide, creates a virtual
environment, installs dependencies, registers a Jupyter kernel, and launches
the notebook in your browser — all in one step. It's safe to re-run any time;
it skips work that's already done.

## 4. Use the tool

The notebook (`viewer.ipynb`) opens with a **Choose file** picker. Select
your `.svs`, enter your name as **Annotator ID**, and click **Load**.

- **Navigate:** zoom slider, click/drag on the thumbnail, or the pan sliders.
- **Select tiles:** click or drag on the zoomed view. Adjacent selected tiles
  automatically form a region; selecting a tile that bridges two regions
  merges them, and deselecting a bridge tile splits them back apart.
- **Add notes:** each region gets its own color swatch and text box under
  the thumbnail — click a swatch to jump the viewport to that region.
- **Reset** clears everything (without changing your zoom/pan position);
  **Complete** exports your regions to `annotations/` as a timestamped JSON
  file.
- **Resume a session:** pick a previously exported JSON instead of a `.svs`
  in the file picker, and your regions, colors, and notes come back exactly
  as you left them.

Full step-by-step instructions are also in the notebook itself.