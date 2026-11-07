# Changelog

All notable changes to this fork are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning:
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Forked from
[kaanyalova/ComfyUI_ExtendedImageFormats](https://github.com/kaanyalova/ComfyUI_ExtendedImageFormats)
at commit `aee4ac0` (upstream `1.0.0`).

## [Unreleased]

## [1.1.0] - 2026-11-08

### Added

- `quality` widget on `ExtendedSaveImage` (INT, 1–100, default 90). One slider for every
  format; what it maps to is per-encoder: JXL and WebP switch to true lossless at 100, AVIF
  gets `quality=100` plus 4:4:4 chroma (it has no lossless mode), JPEG stays lossy, PNG and
  DDS ignore it.
- `web/jxl_metadata.js` — a JXL container box-walk plus a TIFF-IFD reader, so dropping a
  `.jxl` into ComfyUI restores the saved workflow the same way AVIFF/JPEG/WebP already did.
  `exifr` cannot parse JXL containers or bare TIFF payloads, hence the dedicated reader.
- `test/run_node_checks.py` and `test/run_frontend_checks.mjs`: the node is exercised through
  real ComfyUI on CPU (all formats × q=1/50/90/100, bit-exactness, EXIF round-trip) and the
  shipped `web/*.js` is exercised under Node with only ComfyUI's own modules stubbed.
- CI: `validate.yml` (comfy-cli validation + ruff security gate including `S605`/`S607`) and
  `test.yml` (CPU torch + ComfyUI, both harnesses) run on every push and PR.

### Changed

- JXL backend moved from `jxlpy` to `pillow-jxl-plugin`. `jxlpy` lists EXIF writing as
  unsupported in its own README, which is why the workflow could never survive a `.jxl`
  round-trip; `pillow-jxl-plugin` writes EXIF and exposes `quality`/`lossless`.
- `quality=100` never reaches the JXL encoder as `quality=100` — that raises
  `RuntimeError: The encoder API is used in an incorrect way`. It maps to `lossless=True`.
- Passing `quality=100` to WebP maps to `lossless=True` as well; WebP at `quality=100` is
  still lossy.
- Package identity is `extended-image-formats-fork` so ComfyUI Manager reads this as its own
  package instead of matching upstream's registry entry. `PublisherId` is intentionally empty.

### Fixed

- `ExtendedSaveImage` crashed with `KeyError: 'extra_pnginfo'` when no workflow was attached
  (API-only prompts, `--disable-metadata`). It now saves without metadata instead.
- EXIF is passed to every non-PNG encoder as bytes. Passing a Pillow `Exif` object crashes
  `pillow-jxl-plugin` with `'Exif' object has no attribute 'startswith'`.
- The frontend's MIME check was `file.type === ("image/avif" || "image/jpeg" || ...)`, which
  evaluates to just `image/avif` — so JPEG never reached the metadata branch, and a new format
  could not either. Replaced with an explicit `includes()` list plus a JXL branch.

## [1.0.0] - 2025-01-25

Upstream baseline, inherited unmodified apart from the changes above. Adds `ExtendedSaveImage`
(JPEG/WebP/AVIF/JXL/PNG/DDS via one node) and `DDSSaveImage` to ComfyUI.
