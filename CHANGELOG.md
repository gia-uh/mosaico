# Changelog

## [Unreleased]

### Added

- **Scene artifacts.** `scene: <file>` (and optional `shot:`) in place of a
  prompt: mosaico builds the scene from primitives, prefabs and mannequins,
  renders it with manifoldx and paints it as ink and watercolour, locally, with
  no API call. `include:` shares prefabs and materials between scene files; the
  cache key covers every file read and the shot. New `scene` extra (#8).
- **OpenRouter `/api/v1/images` endpoint.** New `api:` field (`chat`, the
  default, or `images`) on artifacts and in `defaults:`, and `--api` on
  `mosaico gen`. With `images`, refs are sent as `input_references`, the
  aspect as `aspect_ratio`, the optional new `resolution:` /
  `--resolution` tier as `resolution`, and no seed. The response's
  `data[0].b64_json` is decoded. `MOSAICO_IMAGES_ENDPOINT` overrides the
  URL. `api` and `resolution` enter the cache input hash only when set,
  so existing projects keep their cached renders.

## [0.5.0] - 2026-07-29

### Added

- **`cells_out:` places cut pieces where the consumer needs them.** `grid:`
  wrote its cells to `<out-stem>/cells/` and nowhere else. That fits Pattern B
  (the sheet is the deliverable, cells are a side effect) but not Pattern A
  (the cells ARE the deliverable and a downstream build reads them from a
  fixed path). `cells_out:` is out_root-relative, like `out:`. Declaring it
  without `grid:` is a parse error — there would be nothing to cut.

- **Cutting repairs itself, without an API call.** When a sheet is a cache hit
  (or is being anchored by `--bootstrap`) but its declared cells are missing on
  disk, mosaico re-cuts them from the sheet it already has and reports them as
  `recut` in the summary. Cutting is deterministic and free; there was no
  reason for missing pieces to be unfixable except by a paid re-roll.

  This closes the same hole 0.4.1 half-closed, found the same way: the
  enciclopedia's chapter glossary. The sheet had `grid:` but no `cells:`, so
  the pieces landed as `cell-rN-cM.jpg` while the book's LaTeX asked for
  `glosario/<slug>.jpg`. The PDF died with `Unable to load picture` — an error
  that names neither the glossary nor the manifest.

### Note

`cells:` is part of the input hash and `cells_out:` deliberately is not.
Adding `cells:` to an already-rendered sheet therefore marks it stale: use
`--bootstrap` to anchor the new hash instead of paying for a re-render, since
the anchor pass now cuts as it goes.

## [0.4.1] - 2026-05-06

### Fixed

- **`render` now propagates `artifact.cells` to the cropper.** When a
  project-YAML artifact declared both `grid:` and `cells:` (mapping
  `slug -> {row, col}`), the `cells` dict was loaded from YAML but
  `render.py` called `run_gen(..., cell_names=None)`, so cropped cells
  were written with generic names (`cell-rR-cC.jpg`) instead of the
  declared slugs. Discovered while generating the cap. 5.1 glossary in
  the enciclopedia repo.

### Changed

- **`run_gen` signature: `cell_names: list[str] | None` -> `cells: dict[str, dict] | None`.**
  Single shape end-to-end: the artifact-level `cells:` dict (slug ->
  `{row, col, rowspan?, colspan?}`) is what the cropper accepts, and
  `run_gen` now passes it through unchanged. The flat-list ergonomic form
  is still exposed via the `--cell-names` CLI option on `mosaico gen`,
  but conversion + count validation now happen at the CLI layer (in
  `gen()`), not inside `run_gen`.

  Internal API change. External consumers calling `run_gen` directly with
  `cell_names=...` must rename to `cells=...` and convert flat lists to
  the dict shape themselves (or use the `gen` CLI which still handles
  `--cell-names` ergonomics).

- **Output convention reaffirmed.** Cells continue to land at
  `<sheet-stem>/cells/<slug>.jpg` (subdirectory under the sheet's stem),
  not flat alongside the sheet. This was already the established
  convention; documenting here to settle the question raised during the
  bug discovery.

## [0.4.0] - 2026-05-04

### Changed

- **Resolved-prompt format: `Image N (label): hint` declarations now PREPEND
  the body** with a directive header, instead of `Reference N: ...` appended
  after. The append-style block was producing low fidelity to the attached
  references — multimodal image-gen models (Nano Banana / Gemini Image)
  treated refs as decoration rather than character/style anchors. The new
  format mirrors the convention these models actually associate with the
  attached `image_url` payloads:

      Use the following N attached image(s) as visual references. For each
      one, follow the per-image instruction below EXACTLY — preserve faces,
      bodies, palette, medium, wardrobe and composition cues from the
      referenced images. Do NOT invent different characters or styles than
      what the references show.

      Image 1 (style-reference): style anchor — preserve medium and palette
      Image 2 (hermanas-sheet): canonical faces and bodies

      ---

      [body of prompt]

  This change invalidates the `input_hash` of every artifact that has refs.
  Run `mosaico render <project.yml> --bootstrap` after upgrading to re-anchor
  existing outputs to the new hashes (re-anchoring does not call the API).

## [0.3.0] - 2026-05-04

### Added

- `mosaico explain <project.yml>` — read-only inspection command that
  prints, for each artifact in topo order: status (ready / render / stale),
  output path, model/seed/aspect, refs with their upstream hashes, and the
  *fully resolved* prompt that would be sent to the API (templates expanded,
  ref-hint block appended). Use to validate the manifest before any render.
- `mosaico explain <project.yml> --only id1,id2` — restrict to specific
  artifacts (transitive deps included, same semantics as `render --only`).

### Use cases

- Confirm template expansion works as intended.
- Audit ref wiring before generating images.
- Inspect why an artifact is `[render]` vs `[ready]` (input_hash compared
  against state).

## [0.2.0] - 2026-05-04

### Added

- `mosaico render <project.yml> --bootstrap` — anchor existing on-disk
  outputs to the current manifest's hashes without calling the API. For
  each artifact whose `out:` exists, computes the manifest's `input_hash`
  and the file's `output_hash` and writes that entry to state. Pendings
  (artifacts whose `out:` is missing) are reported in the summary but not
  rendered. Use to migrate an existing image set under mosaico's cache, or
  to re-anchor after a prompt refactor that shouldn't trigger re-render.
- `--bootstrap --dry-run` previews the anchor pass without writing state.
- `--bootstrap --save` anchors existing files first, then renders any
  pending (missing-on-disk) artifacts in a single command.
- Idempotent: re-running `--bootstrap` preserves `rendered_at` when the
  output hasn't changed; updates only the `input_hash` if the manifest
  changed.
- `RenderSummary` extended with `anchored: list[str]` and
  `pending: list[str]` fields.

### Constraints

- `--bootstrap` is incompatible with `--force`. Run them as separate
  invocations if both behaviors are needed.

## [0.1.0] - 2026-05-03

### Initial release

Lifted from `claude-toolkit/src/claude_toolkit/tools/image/` into a
standalone repo, per design doc
`vault/Atlas/Architecture/2026-05-03-microcli-app-mosaico-mira-split-design.md`.

### Features
- `mosaico gen "<prompt>"` — single-image generation via OpenRouter, with
  optional `--grid RxC` sheet + auto-cut.
- `mosaico render <project.yml>` — declarative project rendering with
  content-addressed cache, topo-sorted graph, two-phase plan/save flow.
- Exposed as a microcli `App` for mounting under parent CLIs (e.g.
  `claude-toolkit image …`).
- Token discovery: `OPENROUTER_API_KEY` → `MOSAICO_TOKEN_FILE` →
  `$CLAUDE_TOOLKIT_WORKSPACE/.claude/openrouter.token`.

### Renamed commands

- `image-gen` → `gen` (now `mosaico gen`)
- `image-render` → `render` (now `mosaico render`)
