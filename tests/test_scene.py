"""Scene artifacts: parsing, hashing and the render branch, with a stub renderer."""
from pathlib import Path

import pytest

from mosaico import render as render_mod
from mosaico import scene as scene_mod
from mosaico.render import _input_hash_for
from mosaico.schema import SchemaError, parse_project

SCENE = """
size: [96, 64]
prefabs:
  block:
    parts:
      - {id: b, material: mud, mesh: {box: [1, 1, 1]}}
scene:
  far: 100
  sun: {direction: [0.3, -1.0, 0.4], intensity: 3.0, color: "#ffffff"}
  shots:
    wide: {position: [0, 2, -6], target: [0, 0.5, 0], fov: 40}
    close: {position: [0, 1, -3], target: [0, 0.5, 0], fov: 40}
  objects:
    - {id: ground, material: sand, mesh: {box: [20, 0.1, 20]}}
    - {id: block, prefab: block, at: [0, 0.5, 0]}
pass:
  seed: 1
  sky: {gradient: ["#3060c0", "#f0e0c0"]}
  ink: {color: "#20160f", object: 2, part: 1, crease: 1, wobble: 0.5, far: 50}
  materials:
    sand: {color: "#e0c890", shadow: "#8a7040"}
"""
COMMON = """
pass:
  materials:
    mud: {color: "#8a6545", shadow: "#4a3322"}
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "scenes").mkdir()
    (tmp_path / "scenes" / "block.yml").write_text("include: [common.yml]\n" + SCENE)
    (tmp_path / "scenes" / "common.yml").write_text(COMMON)
    p = tmp_path / "p.yml"
    p.write_text("""
version: 1
name: t
defaults: {out_root: out, state: state.json}
artifacts:
  - id: wide
    scene: scenes/block.yml
    out: wide.jpg
  - id: close
    scene: scenes/block.yml
    shot: close
    out: close.jpg
""")
    return p


def test_scene_artifact_needs_no_prompt(project):
    arts = {a.id: a for a in parse_project(project).artifacts}
    assert arts["wide"].scene == "scenes/block.yml" and arts["wide"].shot is None
    assert arts["close"].shot == "close"


def test_includes_merge_materials(project):
    spec, files = scene_mod.load_scene(project.parent / "scenes" / "block.yml")
    assert set(spec["pass"]["materials"]) == {"mud", "sand"}
    assert [f.name for f in files] == ["block.yml", "common.yml"]


def test_hash_follows_the_shot_and_every_included_file(project):
    proj = parse_project(project)
    wide, close = proj.artifacts
    h_wide, _ = _input_hash_for(wide, proj, {"artifacts": {}})
    h_close, _ = _input_hash_for(close, proj, {"artifacts": {}})
    assert h_wide != h_close
    (project.parent / "scenes" / "common.yml").write_text(COMMON.replace("8a6545", "8a6546"))
    assert _input_hash_for(wide, proj, {"artifacts": {}})[0] != h_wide


def test_scene_artifact_rejects_refs(tmp_path):
    p = tmp_path / "p.yml"
    p.write_text("""
version: 1
name: t
defaults: {out_root: out, state: state.json}
artifacts:
  - id: a
    scene: s.yml
    refs: [{path: x.jpg}]
    out: a.jpg
""")
    with pytest.raises(SchemaError, match="refs"):
        parse_project(p)


def test_shot_without_scene_is_rejected(tmp_path):
    p = tmp_path / "p.yml"
    p.write_text("""
version: 1
name: t
defaults: {out_root: out, state: state.json}
artifacts:
  - {id: a, prompt_template: x, shot: wide, out: a.jpg}
""")
    with pytest.raises(SchemaError, match="shot"):
        parse_project(p)


def test_include_cycle_is_named(tmp_path):
    (tmp_path / "a.yml").write_text("include: [b.yml]\n")
    (tmp_path / "b.yml").write_text("include: [a.yml]\n")
    with pytest.raises(scene_mod.SceneError, match="cycle"):
        scene_mod.load_scene(tmp_path / "a.yml")


def test_render_draws_scenes_locally_and_caches(project, monkeypatch):
    calls = []

    def fake_scene(path, shot, out):
        calls.append(shot)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\xff\xd8\xff" + (shot or "first").encode())
        return out

    def no_api(**kw):
        raise AssertionError("a scene artifact must not call the model")

    monkeypatch.setattr(scene_mod, "render_scene", fake_scene)
    monkeypatch.setattr(render_mod, "run_gen", no_api)
    s = render_mod.run_render(project, None, None, dry_run=False)
    assert sorted(s.rendered) == ["close", "wide"] and set(calls) == {"close", None}
    s = render_mod.run_render(project, None, None, dry_run=False)
    assert sorted(s.skipped) == ["close", "wide"] and len(calls) == 2


def test_real_render_is_deterministic(project, tmp_path):
    pytest.importorskip("manifoldx.stylize")
    try:
        from manifoldx.backends import get_offscreen_canvas
        get_offscreen_canvas(width=8, height=8)
    except Exception as e:
        pytest.skip(f"offscreen canvas unavailable: {e}")
    path = project.parent / "scenes" / "block.yml"
    a = scene_mod.render_scene(path, "wide", tmp_path / "a.png")
    b = scene_mod.render_scene(path, "wide", tmp_path / "b.png")
    assert a.read_bytes() == b.read_bytes()
    from PIL import Image
    assert Image.open(a).size == (96, 64)
