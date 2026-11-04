"""Scene artifacts: a YAML scene rendered by manifoldx, with no model call.

A scene file has three sections:

    prefabs:   named assemblies of parts. A part is a mesh (`box: [w, h, d]`,
               `cylinder: [radius, height, segments]`, `icosphere:
               [subdivisions, radius]`, `torus: [major, minor]`) or another
               prefab, placed with `at`, `rotate` (degrees), `scale`, copied
               with `repeat: {line: {count, from, to, scale_to}}` or `repeat:
               {ring: {count, radius, axis}}`, deformed with `ops:` (manifoldx
               Mesh methods) or `taper_all`.
    scene:     `sun`, `far`, `shadow_target`, `shadow_extent`, `shots`
               (`position`, `target`, `fov`) and `objects`. An object is a part,
               a prefab (`prefab:` with `hide:`), or a mannequin (`humanoid:`
               with `pose`, `body`, `height`, `skin`, `cloth`, and `facing`).
    pass:      the ink-and-wash style (`seed`, `sky`, `ink`, `materials`), as
               `manifoldx.stylize.ink_and_wash` reads it.

`size: [w, h]` sets the image size (default 1500x1000). `include: [file, ...]`
merges prefabs and pass materials from other scene files; the including file
wins on a clash.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import yaml

DEFAULT_SIZE = (1500, 1000)


class SceneError(ValueError):
    """A scene file that cannot be read or rendered."""


def load_scene(path: Path) -> tuple[dict, list[Path]]:
    """The scene with its includes merged, and every file it was built from."""
    return _load(Path(path).resolve(), ())


def _load(path: Path, chain: tuple[Path, ...]) -> tuple[dict, list[Path]]:
    if path in chain:
        raise SceneError(f"scene include cycle: {' -> '.join(p.name for p in (*chain, path))}")
    if not path.exists():
        raise SceneError(f"scene file not found: {path}")
    spec = yaml.safe_load(path.read_text()) or {}
    files = [path]
    prefabs, materials = {}, {}
    for inc in spec.pop("include", None) or []:
        sub, sub_files = _load((path.parent / inc).resolve(), (*chain, path))
        prefabs.update(sub.get("prefabs") or {})
        materials.update((sub.get("pass") or {}).get("materials") or {})
        files += [f for f in sub_files if f not in files]
    spec["prefabs"] = {**prefabs, **(spec.get("prefabs") or {})}
    if materials or "pass" in spec:
        spec.setdefault("pass", {})
        spec["pass"]["materials"] = {**materials, **(spec["pass"].get("materials") or {})}
    return spec, files


def scene_hash(path: Path, shot: str | None) -> str:
    """Content hash of a scene: every file it reads, and the shot."""
    _, files = load_scene(path)
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode() + b"\0" + f.read_bytes() + b"\0")
    h.update((shot or "").encode())
    return h.hexdigest()


def render_scene(path: Path, shot: str | None, out: Path) -> Path:
    """Render one shot of a scene file to `out` (JPEG or PNG by suffix)."""
    try:
        import manifoldx as mx
        from manifoldx.stylize import ink_and_wash
    except ImportError as e:
        raise SceneError("scene artifacts need manifoldx with the ink extra: pip install 'mosaico[scene]'") from e
    from manifoldx.components import Material, Mesh as MeshC, Transform
    from manifoldx.humanoid import Proportions, facing, solve
    from manifoldx.resources import DirectionalLight, StandardMaterial
    from PIL import Image

    spec, _ = load_scene(path)
    prefabs, scene, style = spec.get("prefabs") or {}, spec["scene"], spec["pass"]
    shots = scene["shots"]
    shot = shot or next(iter(shots))
    if shot not in shots:
        raise SceneError(f"scene {path.name} has no shot `{shot}`; it has {', '.join(shots)}")
    mspec = style["materials"]
    w, h = spec.get("size") or DEFAULT_SIZE

    engine = mx.Engine("mosaico-scene", width=int(w), height=int(h))
    sun = scene["sun"]
    engine.set_sun(DirectionalLight(color=sun["color"], intensity=sun["intensity"], direction=tuple(sun["direction"])))
    engine.enable_shadows(
        target=tuple(scene.get("shadow_target", (0, 2, 0))),
        extent=float(scene.get("shadow_extent", 30.0)),
        far=300.0, resolution=4096, bias=0.003, pcf_radius=2, auto_fit=False,
    )
    engine.camera.far = scene["far"]

    from manifoldx.stylize import Label

    groups: list[list[int]] = []
    labels: list[Label] = []
    identity = dict(pos=(0, 0, 0), rot=(0, 0, 0, 1))

    def material(name):
        if name not in mspec:
            raise SceneError(f"scene {path.name}: material `{name}` is not in pass.materials")
        return Material(StandardMaterial(color=mspec[name]["color"], roughness=0.9))

    for oi, o in enumerate(scene["objects"], start=1):
        no_ink = bool(o.get("no_ink"))
        if "humanoid" in o:
            hum = o["humanoid"]
            M, T = _matrix(o)
            fig = solve(hum["pose"], Proportions.measured(hum["body"]), height=hum["height"],
                        yaw=facing((T[0], T[2]), tuple(o["facing"])))
            sphere = _mesh().icosphere(subdivisions=3, radius=1.0).recompute_normals().to_geometry()
            by_mat: dict[str, int] = {}
            for part in fig.parts:
                skin = (part.center[1] > hum["height"] * 0.83 or part.center[1] < hum["height"] * 0.38
                        or "arm" in part.kind or "hand" in part.kind)
                mat = hum["skin"] if skin else hum["cloth"]
                e = engine.spawn(MeshC(sphere), material(mat),
                                 Transform(pos=tuple(part.center + T), rot=tuple(part.rotation), scale=tuple(part.radii)))
                if mat not in by_mat:
                    by_mat[mat] = len(groups)
                    groups.append([])
                    labels.append(Label(oi, mat, no_ink))
                groups[by_mat[mat]].append(e.index)
            continue
        if "prefab" in o:
            M, T = _matrix(o)
            leaves = _expand(prefabs, o["prefab"], M, T, o["id"], hide=o.get("hide", ()))
        else:
            leaves = _leaf(o, np.eye(3), np.zeros(3), o["id"])
        for _, mat, positions, faces, smooth in leaves:
            e = engine.spawn(MeshC(_geometry(positions, faces, smooth)), material(mat), Transform(**identity))
            groups.append([e.index])
            labels.append(Label(oi, mat, no_ink))

    cam = shots[shot]
    engine.camera.set_pose(position=tuple(cam["position"]), target=tuple(cam["target"]))
    engine.camera.fov = cam["fov"]
    engine.render_frame(supersample=1)
    buffers = {
        "ids": engine.render_frame(supersample=1, pass_="ids", groups=groups),
        "normals": engine.render_frame(supersample=1, pass_="normals"),
        "depth": engine.render_frame(supersample=1, pass_="depth"),
        "light": engine.render_frame(supersample=1, pass_="light"),
    }
    img = ink_and_wash(buffers, labels, style, seed=style.get("seed", 0))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.round(img * 255).astype(np.uint8)).save(out, quality=92)
    return out


# ---------- prefab expansion ----------

def _mesh():
    from manifoldx.modeling import Mesh

    return Mesh


def _rot(deg):
    x, y, z = np.radians(deg)
    cx, sx, cy, sy, cz, sz = np.cos(x), np.sin(x), np.cos(y), np.sin(y), np.cos(z), np.sin(z)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rz @ ry @ rx


def _matrix(d):
    """A part's or object's own transform as a 3x3 matrix and an offset."""
    s = d.get("scale", 1)
    s = np.diag([s] * 3 if np.isscalar(s) else s)
    return _rot(d.get("rotate", [0, 0, 0])) @ s, np.array(d.get("at", [0, 0, 0]), float)


def _base_mesh(spec):
    Mesh = _mesh()
    kind, a = next(iter(spec.items()))
    if kind == "box":
        return Mesh.box(*a), False
    if kind == "cylinder":
        return Mesh.cylinder(radius=a[0], height=a[1], segments=a[2]), False
    if kind == "icosphere":
        return Mesh.icosphere(subdivisions=a[0], radius=a[1]), True
    if kind == "torus":
        return Mesh.torus(major=a[0], minor=a[1]), True
    raise SceneError(f"unknown mesh `{kind}`: use box, cylinder, icosphere or torus")


def _copies(part):
    """One (matrix, offset) per repeated copy, in the part's own frame."""
    rep = part.get("repeat")
    if not rep:
        return [(np.eye(3), np.zeros(3))]
    if "ring" in rep:
        r = rep["ring"]
        out = []
        for i in range(r["count"]):
            a = 360 * i / r["count"]
            if r["axis"] == "x":
                m, radial = _rot([a, 0, 0]), np.array([0, r["radius"], 0.0])
            else:
                m, radial = _rot([0, a, 0]), np.array([r["radius"], 0, 0.0])
            out.append((m, m @ radial))
        return out
    li = rep["line"]
    a, b = np.array(li["from"], float), np.array(li["to"], float)
    return [
        (np.eye(3) * (1 + (li.get("scale_to", 1) - 1) * t), a + (b - a) * t)
        for t in np.linspace(0, 1, li["count"])
    ]


def _taper_all(p, spec):
    i = "xyz".index(spec["axis"])
    lo, hi = p[:, i].min(), p[:, i].max()
    k = 1 + (spec["factor"] - 1) * (p[:, i] - lo) / (hi - lo)
    q = p.copy()
    for j in range(3):
        if j != i:
            q[:, j] *= k
    return q


def _expand(prefabs, name, M, T, path, hide=()):
    """Yield every leaf mesh of a prefab, baked: (path, material, positions, faces, smooth)."""
    if name not in prefabs:
        raise SceneError(f"unknown prefab `{name}` (in {path})")
    for part in prefabs[name]["parts"]:
        if part["id"] in hide:
            continue
        pm, pt = _matrix(part)
        if "prefab" in part:
            yield from _expand(prefabs, part["prefab"], M @ pm, M @ pt + T, f"{path}/{part['id']}")
            continue
        yield from _leaf(part, M, T, f"{path}/{part['id']}")


def _leaf(part, M, T, path):
    pm, pt = _matrix(part)
    mesh, smooth = _base_mesh(part["mesh"])
    for op in part.get("ops", []):
        kind, args = next(iter(op.items()))
        mesh = getattr(mesh, kind)(**args)
    items = [(im, it, mesh.positions @ im.T + it) for im, it in _copies(part)]
    if "taper_all" in part:
        n = len(mesh.positions)
        joined = _taper_all(np.concatenate([x[2] for x in items]), part["taper_all"])
        items = [(im, it, joined[i * n : (i + 1) * n]) for i, (im, it, _) in enumerate(items)]
    for i, (_, _, p) in enumerate(items):
        world = (p @ pm.T + pt) @ M.T + T
        yield f"{path}.{i}", part["material"], world.astype(np.float32), mesh.faces, smooth


def _geometry(positions, faces, smooth):
    Mesh = _mesh()
    if smooth:
        return Mesh(positions=positions, faces=faces).recompute_normals().to_geometry()
    # Split the vertices so every face keeps its own normal and creases stay sharp.
    tri = positions[faces].reshape(-1, 3)
    return Mesh(positions=tri, faces=np.arange(len(tri), dtype=np.uint32).reshape(-1, 3)).recompute_normals().to_geometry()
