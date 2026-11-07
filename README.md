# Extended Image Formats for ComfyUI (fork)

A fork of [kaanyalova/ComfyUI_ExtendedImageFormats](https://github.com/kaanyalova/ComfyUI_ExtendedImageFormats),
taken at upstream commit `aee4ac0` (Jan 2025). Same two nodes, three changes:

- a `quality` slider on `Extended Save`
- jxl files now carry their workflow, like every other format already did
- ComfyUI Manager treats this as its own package instead of matching upstream's registry entry

## What the quality slider does

`quality` (1-100, default 90) maps onto whatever each format can actually do, which is not
the same thing everywhere:

```
quality   jxl                  webp                 avif                 jpeg
1         smallest             smallest             smallest             smallest
50        mid                  mid                  mid                  mid
99        near-lossless        near-lossless        near-lossless        best lossy
100       lossless (bit-exact) lossless (bit-exact) 100 + 4:4:4          still lossy
```

`png` and `dds` ignore the slider; png keeps ComfyUI's own `compress_level`, and dds has no
quality knob at all.

Two rows there are not preferences, they are how the installed encoders behave:

- jxl raises `The encoder API is used in an incorrect way` if you ask for `quality=100`, so
  100 sets `lossless=True` instead. That is why the top of the slider is genuinely lossless
  (verified bit-exact) rather than merely "quality 100".
- `pillow-avif-plugin` has no lossless mode. 100 asks for `quality=100` with
  `subsampling="4:4:4"`, which is close but not byte-exact. If you need exact output, use
  png or jxl.

jpeg is lossy by definition; 100 is just its best setting.

The slider is new, so its default changes what an untouched node produces: upstream used
Pillow's per-format defaults (jpeg 75, webp 80).

## jxl metadata

Upstream printed `JXL export doesn't have metadata/import support yet!`. That was true of
`jxlpy`, which lists *Support EXIF metadata: Failed* in its own feature table. This fork
uses [pillow-jxl-plugin](https://github.com/Isotr0py/pillow-jpegxl-plugin) instead, which
writes EXIF, so a saved `.jxl` carries `{"prompt": ..., "workflow": ...}` in the
`UserComment(0x9286)` tag exactly like webp/jpeg/avif do. Drop the file back on the canvas
and the workflow loads.

`jxlpy` is still detected as a fallback so existing installs keep working, but it cannot
write EXIF — if only jxlpy is present the node says so and saves the image without metadata.

The reading happens in the frontend. `exifr`, which the extension uses for jpeg/avif, cannot
parse `.jxl` at all (it fails even on a bare TIFF payload), so `web/jxl_metadata.js` walks
the JXL container boxes to the `Exif` box and reads the TIFF IFD directly. None of this
needs an image decoder: Chrome 155 has native jxl decoding, and a browser that cannot
decode jxl just shows no preview while the workflow still loads.

## Metadata layout

Unchanged from upstream. Compressed formats put this in `UserComment(0x9286)`; png uses text
chunks:

```
{
    "prompt": { Prompt data... }
    "workflow": { Workflow data... }
    "some_extra_pnginfo_field": {...}
}
```

dds still has no metadata inside the file — that is what `Save DDS for games` and its
sidecar are for.

## Installation

Clone into `custom_nodes`:

```
cd ComfyUI/custom_nodes
git clone https://github.com/lpnmqrpbmjx1064/comfyui-extended-image-formats-fork
```

Then install the encoders:

```
source ./venv/bin/activate
pip install pillow-avif-plugin pillow-jxl-plugin
```

**Do not keep upstream and this fork in `custom_nodes` at the same time.** Both register the
node ids `ExtendedSaveImage` and `DDSSaveImage`, so you get duplicate registration and
whichever folder loads last wins.

In ComfyUI Manager this shows up as `extended-image-formats-fork`. It is not published to
the registry yet (`PublisherId` is deliberately empty), so install via git url for now.

## What still does not work

- dds: no in-file metadata (upstream behaviour, unchanged)
- avif: no lossless mode (see above)
- CI is not in this repo yet: the workflows are written, but the token used to push here
  lacks the `workflow` scope GitHub requires for `.github/workflows/*`
- the tests in `test/` need a ComfyUI checkout and the encoders installed; they are not
  wired into CI

## Tests

`test/run_node_checks.py` boots real ComfyUI on CPU, loads the node the way `nodes.py` does,
and saves every format at q=1/50/90/100. It checks that sizes actually move, that jxl
lossless is bit-exact, that metadata round-trips, and that the cases upstream crashed on
(no `extra_pnginfo`, prompt-only api mode, `--disable-metadata`) now work.

`test/run_frontend_checks.mjs` imports the shipped `web/*.js` with only ComfyUI's own modules
stubbed, and asserts that a dropped `.jxl` really calls `loadGraphData` with the saved graph
— plus the negative controls: no metadata, truncated file, png bytes named `.jxl`, and an
unknown type being passed on to the previous handler.

```
COMFY_DIR=/path/to/ComfyUI python test/run_node_checks.py
SAVED_DIR=<that run's output dir> node test/run_frontend_checks.mjs
```

## Written with AI

The quality slider, the jxl metadata support, the packaging changes and the tests in this
fork were written by an AI agent (Hermes, on DeepSeek v4.1), working from the upstream code
and from the encoders' real behaviour. Every claim above was measured against the installed
packages rather than copied from documentation — including the two encoder limits that drove
the design (jxl's `quality=100` crash, avif's missing lossless mode). Upstream's code and
structure are otherwise untouched.
