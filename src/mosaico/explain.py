"""mosaico explain — print resolved prompt + dep graph per artifact.

Read-only inspection command. No state changes, no API calls.

Useful for:
- Validating that templates expand correctly.
- Confirming that refs are wired up the way the manifest intends.
- Inspecting the *exact* prompt block that will be sent to the API
  (templates expanded, ref-hint block appended).
- Auditing artifact status (`ready` = cached and matching, `render` = will
  render on next `--save`, `stale` = in state but input has drifted).

Discoverability:
    mosaico explain --tour
    mosaico explain <project.yml>
    mosaico explain <project.yml> --only id1,id2
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

import microcli as m

from . import app
from .render import _input_hash_for, _resolve_prompt, _restrict_to_only
from .schema import SchemaError, parse_project, topo_sort
from .state import load_state


def _status(stored: dict, ihash: str) -> str:
    if not stored:
        return "render"
    if stored.get("input_hash") == ihash:
        return "ready"
    return "stale"


@app.command
def explain(
    project: Annotated[str, "Path to the project YAML"],
    only: Annotated[
        str, "Inspect only these artifact ids (comma-separated)"
    ] = "",
):
    """Print resolved prompts and the dependency graph.

    Walks the topo-sorted artifact list and prints, for each artifact:
    its status, output path, model/seed/aspect, refs (with upstream hashes
    when available) and the *fully resolved* prompt that would be sent to
    the API — templates expanded, ref-hint block appended.

    Read-only: never writes state, never calls the API. Use this to verify
    that the manifest produces the prompts you expect before running
    `mosaico render --save`.
    """
    project_path = Path(project)
    try:
        proj = parse_project(project_path)
        ordered = topo_sort(proj)
    except SchemaError as e:
        m.fail(str(e))

    by_id = {a.id: a for a in proj.artifacts}
    only_list = [s.strip() for s in only.split(",") if s.strip()] or None
    if only_list:
        ordered = _restrict_to_only(ordered, only_list, by_id)

    state = load_state(proj.state_path)

    print(f"# {proj.name} — {len(ordered)} artifact(s) (topo order)")
    print()

    for art in ordered:
        try:
            ihash, _ = _input_hash_for(art, proj, state)
        except SchemaError as e:
            m.fail(str(e))

        stored = state["artifacts"].get(art.id, {})
        status = _status(stored, ihash)
        out_abs = proj.out_root / art.out
        out_marker = "exists" if out_abs.exists() else "missing"

        print(f"┌─ {art.id}  [{status}]")
        print(f"│  out:     {art.out}  ({out_marker})")
        if art.scene is not None:
            print(f"│  scene:   {art.scene}  shot: {art.shot or '(first)'}")
            print(f"└─ input_hash: {ihash[:24]}…  ({'will skip' if status == 'ready' else 'will render, locally'})")
            print()
            continue
        print(f"│  model:   {art.resolved_model}")
        print(
            f"│  seed:    {art.resolved_seed}     "
            f"aspect: {art.resolved_aspect}"
        )
        if art.grid:
            print(f"│  grid:    {art.grid[0]}×{art.grid[1]}")
            if art.cells:
                print(f"│  cells:   {len(art.cells)} declared")
        if art.refs:
            print(f"│  refs:")
            for r in art.refs:
                if r.artifact:
                    upstream = state["artifacts"].get(r.artifact, {})
                    h = upstream.get("output_hash", "(not rendered yet)")
                    short = h[:24] + "…" if len(h) > 24 else h
                    hint = r.hint or "(no hint)"
                    print(f"│    - artifact {r.artifact}  [{short}]")
                    print(f"│        hint: {hint}")
                else:
                    hint = r.hint or "(no hint)"
                    print(f"│    - path {r.path}")
                    print(f"│        hint: {hint}")
        else:
            print(f"│  refs:    (none)")
        print(f"│  prompt:")
        resolved = _resolve_prompt(art, proj)
        for line in resolved.splitlines() or [""]:
            print(f"│    {line}")
        ih_short = ihash[:24] + "…"
        if status == "ready":
            print(f"└─ input_hash: {ih_short}  (matches state — will skip)")
        elif status == "stale":
            stored_short = stored.get("input_hash", "")[:24] + "…"
            print(
                f"└─ input_hash: {ih_short}  "
                f"(state has {stored_short} — will re-render)"
            )
        else:
            print(f"└─ input_hash: {ih_short}  (not in state — will render)")
        print()
