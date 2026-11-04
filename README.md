# mosaico

> Declarative image-project renderer.

`mosaico` reads a YAML manifest of image artifacts, builds the dependency
graph, topologically sorts it, and renders only what's missing or stale —
content-addressed caching, idempotent re-runs, two-phase plan/save flow.
Two commands:

- `mosaico gen "<prompt>"` — low-level: prompt → single image (with optional
  `--grid RxC` sheet + auto-cut).
- `mosaico render <project.yml>` — high-level: declarative project, full
  graph render with caching.

Both are designed to be agent-friendly: `--tour`, `--dry-run`, and
`mosaico` itself prints next-step instructions on every failure.

## Install

```bash
uv add mosaico
# or
pip install mosaico
```

## Quick start

```bash
export OPENROUTER_API_KEY=sk-...
mosaico gen "watercolor of a heron at dawn" --save --out heron.jpg
```

```yaml
# project.yml
version: 1
name: my-book
defaults:
  out_root: out
  state: state.json
artifacts:
  - id: cover
    prompt_template: "book cover, watercolor style, title '{{ templates.title }}'"
    out: cover.jpg
templates:
  title: "Érase una vez el Conocimiento"
```

```bash
mosaico render project.yml --save
```

## Models on OpenRouter's images endpoint

By default mosaico calls `/api/v1/chat/completions` with image modalities
(Gemini, GPT image models). Models that only answer on `/api/v1/images`,
such as `black-forest-labs/flux-3-image-klein`, need `api: images`, set per
artifact or in `defaults:`:

```yaml
defaults:
  model: black-forest-labs/flux-3-image-klein
  api: images
  resolution: 1K      # optional tier: 512, 1K, 2K, 4K...
  aspect: "3:2"
```

With `api: images`, refs go as `input_references`, the aspect as
`aspect_ratio` (not baked into the prompt) and no seed is sent, because
some of these models reject it. On the CLI: `mosaico gen "<prompt>" --api
images --resolution 2K --model <slug> --save=true`. Set
`MOSAICO_IMAGES_ENDPOINT` to point it elsewhere.
## Freezing a canonical image set

A project whose visual identity lives in reference sheets — character sheets,
a style board, wardrobe sheets — has a hazard: those sheets are artifacts like
any other, so editing a shared template invalidates their hash, and the next
render that *depends* on them regenerates them as a side effect. The canon is
destroyed by a change that never mentioned it.

Split the manifest and `imports:` the canon:

```yaml
# refs.yml — the canon. Rendered only when named explicitly.
version: 1
name: canon
artifacts:
  - id: style-reference
    prompt_template: "palette board…"
    out: style-reference.jpg
  - id: hermanas-sheet
    prompt_template: "two sisters…"
    refs: [{artifact: style-reference}]
    out: hermanas-sheet.jpg
```

```yaml
# chapter-02.yml — imports the canon, renders only its own artifacts.
version: 1
name: chapter-02
imports:
  - refs.yml
artifacts:
  - id: cover
    prompt_template: "the cover…"
    refs:
      - artifact: style-reference   # resolved from refs.yml, never rendered
      - artifact: hermanas-sheet
    out: cover.jpg
```

Imported manifests also contribute their `templates:` — the shared prompt
vocabulary — so chapter manifests don't duplicate it. A local template that
shadows an imported name is a parse error.

Imported artifacts are **frozen**:

- They never enter the render plan — not directly, and not as transitive
  dependencies of an `--only` target.
- They are content-addressed by their **output file**, not by the recipe that
  produced it. Editing the canon's prompts or templates cannot invalidate
  anything downstream; only replacing the file itself does.
- `--only <imported-id>` is refused, naming the manifest that owns it.
- A missing output is a hard error telling you which manifest to render.

Regenerating the canon stays possible and becomes deliberate:

```bash
mosaico render refs.yml --save        # the only way to touch the canon
mosaico render chapter-02.yml --save  # can never touch it
```

## Cutting a sheet into named pieces

`grid: [rows, cols]` cuts a generated sheet into per-cell files. Two shapes:

- **The sheet is the deliverable** and the cells are a convenience — omit
  `cells:`, take the default `cell-rN-cM.jpg` names under `<out-stem>/cells/`.
- **The cells are the deliverable** and something downstream reads them from a
  fixed path — name them with `cells:` and place them with `cells_out:`.

```yaml
  - id: glossary-sheet
    prompt_template: "a 4x3 reference grid…"
    grid: [4, 3]
    cells:                          # slug -> position on the grid
      plumb-line:  {row: 1, col: 1}
      flying-buttress: {row: 2, col: 1}
    cells_out: img/glossary         # out_root-relative, like `out:`
    out: img/glossary-sheet.jpg
```

Two properties worth knowing:

**Cutting never calls the API.** If the sheet is up to date but declared cells
are missing on disk, mosaico re-cuts them from the bytes it already has and
reports them as `recut`. Losing the pieces can't break a downstream build, and
can't cost a re-roll to fix.

**`cells:` is part of the input hash; `cells_out:` is not.** Adding `cells:` to
an already-rendered sheet marks it stale, so a plain `--save` would regenerate
the image to produce files the cropper can cut for free. Anchor instead — the
anchor pass cuts as it goes:

```bash
mosaico render project.yml --bootstrap    # no API call, pieces cut
```

## Scene artifacts: drawn locally, no model

An artifact can name a scene file instead of a prompt. mosaico builds the scene
from boxes, cylinders, spheres, tori and posed mannequins, renders it with
[manifoldx](https://github.com/gia-uh/manifoldx), and paints the passes as ink
and watercolour. No API call and no cost; about 5 to 10 seconds per image on a
laptop GPU, and the same file always gives the same image.

```bash
pip install 'mosaico[scene]'
```

```yaml
# project.yml
artifacts:
  - id: atum-cover
    scene: scenes/atum.yml   # relative to this manifest
    shot: cover              # optional; the scene's first shot by default
    out: atum/cover.jpg
```

```yaml
# scenes/atum.yml
include: [common.yml]        # shared prefabs and materials; this file wins a clash
size: [1500, 1000]
prefabs:
  hill:
    parts:
      - {id: mound, material: mud, mesh: {icosphere: [3, 1.0]}, scale: [3.4, 0.55, 2.2]}
scene:
  far: 1500
  sun: {direction: [0.1, -0.22, 1.0], intensity: 3.0, color: "#ffd9a0"}
  shots:
    cover: {position: [0, 2.2, -14], target: [0, 1.6, 0], fov: 38}
  objects:
    - {id: hill, prefab: hill}
    - id: atum
      humanoid: {pose: stand, body: male, height: 1.8, skin: skin, cloth: linen}
      at: [0, 0.5, 0]
      facing: [0, -14]
pass:
  seed: 11
  sky: {gradient: ["#1d2235", "#e7a467", "#f6d38e"], blotch: 0.25, blotch_scale: 150}
  ink: {color: "#20160f", object: 2.3, part: 1.15, crease: 0.9, wobble: 0.8, far: 160}
  materials:
    mud:   {color: "#8a6545", shadow: "#4a3322", blotch: 0.35, blotch_scale: 18}
    skin:  {color: "#b07a52", shadow: "#5e3b26", blotch: 0.1, blotch_scale: 8}
    linen: {color: "#efe6d2", shadow: "#9c8f7a", blotch: 0.1, blotch_scale: 8}
```

A part is a mesh (`box: [w, h, d]`, `cylinder: [radius, height, segments]`,
`icosphere: [subdivisions, radius]`, `torus: [major, minor]`) or a nested
`prefab:`, placed with `at`, `rotate` (degrees) and `scale`, copied with
`repeat: {line: {count, from, to}}` or `repeat: {ring: {count, radius, axis}}`,
and deformed with `ops:` (any manifoldx `Mesh` method, such as `taper`). An
object with `no_ink: true` gets no outline (clouds, a sun). The full format is
in `src/mosaico/scene.py`.

The cache key is the content of the scene file, every file it includes, and
the shot: edit any of them and the image renders again. A scene artifact takes
no `refs`, `grid` or `model`.

The mannequins hold up as small figures in a landscape, under about 60 pixels
tall. Close-ups of people and animals are beyond them.

## Migrating existing images under mosaico

If you already have generated images on disk and want to bring them under
mosaico's cache without re-rendering, use `--bootstrap`:

```bash
# Preview what would be anchored (dry-run, no state write)
mosaico render project.yml --bootstrap --dry-run

# Anchor existing files to the current manifest's hashes
mosaico render project.yml --bootstrap

# Anchor existing files, then render anything still missing on disk
mosaico render project.yml --bootstrap --save
```

For each artifact whose `out:` already exists, mosaico computes the
manifest's `input_hash` and the file's `output_hash` and writes that
entry to state — no API call. Pendings (artifacts whose `out:` is missing)
are reported but not rendered unless `--save` is also passed.

`--bootstrap` is also the right tool for **prompt refactors that shouldn't
trigger a re-render**: edit the manifest, run `--bootstrap`, and the new
`input_hash` is anchored to the existing output. A subsequent
`mosaico render --save` will see zero pending and call no API.

## Token discovery

`mosaico gen` reads its OpenRouter token from, in order:

1. `$OPENROUTER_API_KEY`
2. `$MOSAICO_TOKEN_FILE` (path to a file containing the key)
3. `$CLAUDE_TOOLKIT_WORKSPACE/.claude/openrouter.token` (set automatically
   when invoked under `claude-toolkit image …`)

## Mounting under another microcli app

`mosaico` exposes its `App` so a parent CLI can mount it:

```python
import microcli as m
from mosaico import app as mosaico_app

root = m.App(name="my-tool")
root.mount("image", mosaico_app)
root.main()
# my-tool image gen "<prompt>" --save
# my-tool image render project.yml --save
```

## License

MIT.
