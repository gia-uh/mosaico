"""YAML parsing, template expansion, ref resolution, dep graph, topo sort.

The schema has no built-in concept of character/style/scene. Those are
prompt fragments the user defines under top-level `templates:` and composes
into artifact prompts via `{{ templates.NAME }}`.

Every error here MUST tell the agent how to learn the format:
`mosaico render --tour`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


_TOUR_HINT = "Run `mosaico render --tour` for the YAML format."


class SchemaError(ValueError):
    """A project-YAML schema or graph error. Always includes the tour hint."""


@dataclass
class Ref:
    artifact: str | None = None
    path: str | None = None
    hint: str = ""

    def __post_init__(self):
        if (self.artifact is None) == (self.path is None):
            raise SchemaError(
                f"a `refs` entry must specify exactly one of `artifact:` or `path:` "
                f"(got artifact={self.artifact!r}, path={self.path!r}). {_TOUR_HINT}"
            )


@dataclass
class Artifact:
    id: str
    prompt_template: str
    out: str
    refs: list[Ref] = field(default_factory=list)
    grid: tuple[int, int] | None = None
    cells: dict[str, dict] | None = None
    cells_out: str | None = None
    model: str | None = None
    seed: int | None = None
    aspect: str | None = None
    size: str | None = None
    api: str | None = None
    resolution: str | None = None
    scene: str | None = None
    shot: str | None = None

    resolved_model: str = ""
    resolved_seed: int = 0
    resolved_aspect: str = ""
    resolved_api: str | None = None
    resolved_resolution: str | None = None


@dataclass
class Imported:
    """An artifact owned by another manifest: referenceable, never rendered.

    Frozen deps are content-addressed by their *output file*, not by the
    recipe that produced them. Editing the owning manifest's prompts or
    templates therefore cannot invalidate — or silently regenerate — anything
    downstream. Only replacing the file itself does.
    """

    id: str
    out_path: Path
    source: Path


@dataclass
class Project:
    name: str
    out_root: Path
    state_path: Path
    yaml_path: Path
    templates: dict[str, str]
    artifacts: list[Artifact]
    imported: dict[str, Imported] = field(default_factory=dict)


_TEMPLATE_RE = re.compile(r"\{\{\s*templates\.([A-Za-z0-9_]+)\s*\}\}")


def _coerce_aspect(value) -> str:
    """YAML treats `4:3` as sexagesimal int (243). Recover `R:C` form when possible."""
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        # Sexagesimal: value = a*60 + b. Recover only if b < 60 and a > 0.
        if value > 0:
            a, b = divmod(value, 60)
            if a > 0 and b < 60:
                return f"{a}:{b}"
        return str(value)
    return str(value)


def expand_templates(text: str, templates: dict[str, str], _depth: int = 0) -> str:
    """Expand `{{ templates.X }}` references. Recursive (templates may chain).

    Caps depth at 16 to prevent infinite loops if a user defines a cycle
    within templates.
    """
    if _depth > 16:
        raise SchemaError(
            f"template expansion exceeded depth 16 — likely a cycle in "
            f"`templates:`. {_TOUR_HINT}"
        )

    def _sub(match: re.Match) -> str:
        name = match.group(1)
        if name not in templates:
            available = ", ".join(sorted(templates)) or "(none)"
            raise SchemaError(
                f"unknown template `{{{{ templates.{name} }}}}`. "
                f"Available templates: {available}. {_TOUR_HINT}"
            )
        return expand_templates(templates[name], templates, _depth + 1)

    return _TEMPLATE_RE.sub(_sub, text)


def parse_project(path: Path | str) -> Project:
    """Parse + validate a project YAML. Returns a fully resolved `Project`."""
    yaml_path = Path(path).resolve()
    if not yaml_path.exists():
        raise SchemaError(
            f"project YAML not found: {yaml_path}. {_TOUR_HINT}"
        )
    try:
        raw = yaml.safe_load(yaml_path.read_text())
    except yaml.YAMLError as e:
        raise SchemaError(
            f"failed to parse YAML at {yaml_path}: {e}. {_TOUR_HINT}"
        ) from e

    if not isinstance(raw, dict):
        raise SchemaError(
            f"project YAML at {yaml_path} must be a mapping at the top level. "
            f"{_TOUR_HINT}"
        )

    version = raw.get("version")
    if version != 1:
        raise SchemaError(
            f"unsupported `version: {version!r}` in {yaml_path} — "
            f"only version 1 is supported. {_TOUR_HINT}"
        )

    name = raw.get("name") or yaml_path.stem
    defaults = raw.get("defaults") or {}
    templates = raw.get("templates") or {}
    if not isinstance(templates, dict):
        raise SchemaError(
            f"`templates:` must be a mapping of name -> string in {yaml_path}. "
            f"{_TOUR_HINT}"
        )

    out_root = (yaml_path.parent / defaults.get("out_root", ".")).resolve()
    state_path = (
        yaml_path.parent / defaults.get("state", ".image-project/state.json")
    ).resolve()

    raw_artifacts = raw.get("artifacts") or []
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise SchemaError(
            f"`artifacts:` must be a non-empty list in {yaml_path}. "
            f"{_TOUR_HINT}"
        )

    imported: dict[str, Imported] = {}
    imported_templates: dict[str, str] = {}
    raw_imports = raw.get("imports") or []
    if not isinstance(raw_imports, list):
        raise SchemaError(
            f"`imports:` must be a list of manifest paths in {yaml_path}. "
            f"{_TOUR_HINT}"
        )
    for rel in raw_imports:
        imp_path = (yaml_path.parent / str(rel)).resolve()
        if not imp_path.exists():
            raise SchemaError(
                f"`imports:` entry `{rel}` in {yaml_path} resolves to "
                f"{imp_path}, which does not exist. {_TOUR_HINT}"
            )
        if imp_path == yaml_path:
            raise SchemaError(
                f"`imports:` entry `{rel}` in {yaml_path} imports itself. "
                f"{_TOUR_HINT}"
            )
        sub = parse_project(imp_path)
        for tname, tbody in sub.templates.items():
            if tname in templates:
                raise SchemaError(
                    f"template `{tname}` in {yaml_path} shadows a template of "
                    f"the same name imported from {imp_path}. Rename one of "
                    f"them — imported templates are shared vocabulary. "
                    f"{_TOUR_HINT}"
                )
            if tname in imported_templates:
                raise SchemaError(
                    f"template `{tname}` is provided by more than one import "
                    f"in {yaml_path}. Names must be unique across imports. "
                    f"{_TOUR_HINT}"
                )
            imported_templates[tname] = tbody
        for sa in sub.artifacts:
            if sa.id in imported:
                raise SchemaError(
                    f"imported artifact id `{sa.id}` is provided by more than "
                    f"one manifest ({imported[sa.id].source} and {imp_path}). "
                    f"Ids must be unique across imports. {_TOUR_HINT}"
                )
            imported[sa.id] = Imported(
                id=sa.id,
                out_path=(sub.out_root / sa.out).resolve(),
                source=imp_path,
            )

    templates = {**imported_templates, **templates}

    artifacts: list[Artifact] = []
    seen_ids: set[str] = set()
    for i, raw_a in enumerate(raw_artifacts):
        if not isinstance(raw_a, dict):
            raise SchemaError(
                f"artifacts[{i}] must be a mapping in {yaml_path}. {_TOUR_HINT}"
            )
        aid = raw_a.get("id")
        if not aid:
            raise SchemaError(
                f"artifacts[{i}] is missing required field `id`. {_TOUR_HINT}"
            )
        if aid in seen_ids:
            raise SchemaError(
                f"duplicate artifact id `{aid}` in {yaml_path}. {_TOUR_HINT}"
            )
        if aid in imported:
            raise SchemaError(
                f"artifact `{aid}` in {yaml_path} shadows an imported "
                f"artifact of the same id from {imported[aid].source}. "
                f"Rename one of them. {_TOUR_HINT}"
            )
        seen_ids.add(aid)

        scene = raw_a.get("scene")
        prompt = raw_a.get("prompt_template") or raw_a.get("description")
        if scene is not None:
            if not isinstance(scene, str) or not scene.strip():
                raise SchemaError(
                    f"artifact `{aid}`: `scene:` must be a scene file path, "
                    f"relative to the manifest. {_TOUR_HINT}"
                )
            mixed = [k for k in ("refs", "grid", "cells", "cells_out", "model", "api") if raw_a.get(k)]
            if mixed:
                raise SchemaError(
                    f"artifact `{aid}`: a `scene:` artifact is drawn locally "
                    f"from its scene file and takes no {', '.join(f'`{k}:`' for k in mixed)}. "
                    f"{_TOUR_HINT}"
                )
            prompt = prompt or ""
        elif raw_a.get("shot") is not None:
            raise SchemaError(
                f"artifact `{aid}`: `shot:` only goes with `scene:`. {_TOUR_HINT}"
            )
        if not prompt and scene is None:
            raise SchemaError(
                f"artifact `{aid}` is missing `prompt_template:` "
                f"(or `description:` alias). {_TOUR_HINT}"
            )
        out = raw_a.get("out")
        if not out:
            raise SchemaError(
                f"artifact `{aid}` is missing `out:` (output path relative "
                f"to defaults.out_root). {_TOUR_HINT}"
            )

        refs: list[Ref] = []
        for j, raw_r in enumerate(raw_a.get("refs") or []):
            if not isinstance(raw_r, dict):
                raise SchemaError(
                    f"artifact `{aid}` refs[{j}] must be a mapping. "
                    f"{_TOUR_HINT}"
                )
            refs.append(Ref(
                artifact=raw_r.get("artifact"),
                path=raw_r.get("path"),
                hint=raw_r.get("hint", ""),
            ))

        grid = raw_a.get("grid")
        if grid is not None:
            if not (isinstance(grid, list) and len(grid) == 2
                    and all(isinstance(x, int) and x > 0 for x in grid)):
                raise SchemaError(
                    f"artifact `{aid}`: `grid:` must be `[rows, cols]` with "
                    f"positive integers, got {grid!r}. {_TOUR_HINT}"
                )
            grid = (grid[0], grid[1])

        cells = raw_a.get("cells")
        if cells is not None and not isinstance(cells, dict):
            raise SchemaError(
                f"artifact `{aid}`: `cells:` must be a mapping. {_TOUR_HINT}"
            )

        cells_out = raw_a.get("cells_out")
        if cells_out is not None:
            if not isinstance(cells_out, str) or not cells_out.strip():
                raise SchemaError(
                    f"artifact `{aid}`: `cells_out:` must be a non-empty "
                    f"directory path. {_TOUR_HINT}"
                )
            if grid is None:
                raise SchemaError(
                    f"artifact `{aid}`: `cells_out:` needs `grid:` — there is "
                    f"nothing to cut without one. {_TOUR_HINT}"
                )

        a = Artifact(
            id=aid,
            prompt_template=prompt,
            out=out,
            refs=refs,
            grid=grid,
            cells=cells,
            cells_out=cells_out,
            model=raw_a.get("model"),
            seed=raw_a.get("seed"),
            aspect=raw_a.get("aspect"),
            size=raw_a.get("size"),
            api=raw_a.get("api"),
            resolution=raw_a.get("resolution"),
            scene=scene,
            shot=raw_a.get("shot"),
        )
        a.resolved_model = a.model or defaults.get(
            "model", "google/gemini-3.1-flash-image-preview"
        )
        a.resolved_seed = a.seed if a.seed is not None else defaults.get("seed", 42)
        # Coerce aspect to string: YAML parses bare `4:3` as sexagesimal int 243.
        raw_aspect = a.aspect if a.aspect is not None else defaults.get("aspect", "1:1")
        a.resolved_aspect = _coerce_aspect(raw_aspect)
        # None means "not set": api/resolution enter the input hash only when
        # set, so projects written before they existed keep their cache.
        a.resolved_api = a.api or defaults.get("api")
        if a.resolved_api not in (None, "chat", "images"):
            raise SchemaError(
                f"artifact `{aid}`: `api:` must be `chat` or `images`, got "
                f"{a.resolved_api!r}. {_TOUR_HINT}"
            )
        a.resolved_resolution = a.resolution or defaults.get("resolution")
        artifacts.append(a)

    return Project(
        name=name,
        out_root=out_root,
        state_path=state_path,
        yaml_path=yaml_path,
        templates=templates,
        artifacts=artifacts,
        imported=imported,
    )


def topo_sort(project: Project) -> list[Artifact]:
    """Topologically sort artifacts. Ties broken by id lex order.

    Errors with `SchemaError` on:
    - unknown internal `artifact:` reference (with the offending id pair)
    - cycle (with the cycle path included in the message)
    """
    import bisect

    by_id: dict[str, Artifact] = {a.id: a for a in project.artifacts}
    deps: dict[str, list[str]] = {a.id: [] for a in project.artifacts}
    rev: dict[str, list[str]] = {a.id: [] for a in project.artifacts}
    for a in project.artifacts:
        for r in a.refs:
            if r.artifact is None:
                continue
            if r.artifact in project.imported:
                # Frozen: satisfied by a file on disk, not by a render step.
                continue
            if r.artifact not in by_id:
                known = sorted(by_id) + sorted(project.imported)
                raise SchemaError(
                    f"artifact `{a.id}` references unknown artifact "
                    f"`{r.artifact}`. Known ids: "
                    f"{', '.join(known)}. {_TOUR_HINT}"
                )
            deps[a.id].append(r.artifact)
            rev[r.artifact].append(a.id)

    indegree = {aid: len(deps[aid]) for aid in by_id}
    ready = sorted([aid for aid, d in indegree.items() if d == 0])
    out: list[Artifact] = []
    seen: set[str] = set()
    while ready:
        aid = ready.pop(0)
        out.append(by_id[aid])
        seen.add(aid)
        for child in sorted(rev[aid]):
            indegree[child] -= 1
            if indegree[child] == 0:
                bisect.insort(ready, child)

    if len(out) != len(project.artifacts):
        remaining = [a.id for a in project.artifacts if a.id not in seen]
        cycle = _find_cycle(deps, remaining)
        raise SchemaError(
            f"cycle in artifact dependency graph: "
            f"{' -> '.join(cycle)}. Use `mosaico render "
            f"<project> --dry-run` to inspect the graph. {_TOUR_HINT}"
        )

    return out


def _find_cycle(deps: dict[str, list[str]], nodes: list[str]) -> list[str]:
    """Return one cycle path through `nodes`, or [nodes[0]] if not found."""
    visited: set[str] = set()
    stack: list[str] = []
    on_stack: set[str] = set()

    def dfs(n: str) -> list[str] | None:
        if n in on_stack:
            i = stack.index(n)
            return stack[i:] + [n]
        if n in visited:
            return None
        visited.add(n)
        stack.append(n)
        on_stack.add(n)
        for child in deps.get(n, []):
            if child not in nodes:
                continue
            r = dfs(child)
            if r is not None:
                return r
        stack.pop()
        on_stack.remove(n)
        return None

    for n in nodes:
        r = dfs(n)
        if r is not None:
            return r
    return [nodes[0]] if nodes else []
