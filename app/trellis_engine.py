"""TRELLIS.2 (Microsoft, MIT license) through trellis.cpp, a C++ port that runs on AMD cards through Vulkan.

trellis-cli.exe does the whole job (background removal, the three AI stages, mesh clean-up, UV unwrap, texture
bake) and writes a textured .glb. This file runs it, turns its log into progress, and tidies the result.
"""
import json
import re
import shutil
import struct
import subprocess
import time
from pathlib import Path

from pipeline import MODELS, NoObjectError, Result, _new_folder, _write_license, ROOT

EXE = ROOT / "engines" / "trellis" / "trellis-cli.exe"
WEIGHTS = MODELS / "trellis2-q8"
FILES = ["dinov3", "ss_flow", "ss_dec", "shape_flow_512", "shape_flow_1024", "shape_dec",
         "tex_flow_512", "tex_flow_1024", "tex_dec"]

# trellis-cli's "[n/7]" stage lines -> (what to tell the user, progress at the start, progress at the end).
# Measured on an RX 9070 XT at full detail: the AI stages take about half the time, the clean-up the other half.
STAGES = {
    1: ("Cutting the object out of the picture", 0.01, 0.03),
    2: ("Reading the picture", 0.03, 0.05),
    3: ("AI is imagining the rough 3D shape", 0.05, 0.12),
    4: ("AI is sculpting the detailed shape", 0.12, 0.32),
    5: ("Building the mesh", 0.32, 0.36),
    6: ("AI is painting the colors", 0.36, 0.52),
    7: ("Cleaning up the mesh", 0.52, 0.95),
}
FLOW = re.compile(r"\[flow\].*?(\d+)/(\d+)")
CLEANUP = [  # clean-up log lines -> (what to tell the user, progress)
    (r"fill_holes", "Filling holes in the mesh", 0.56),
    (r"remesh_dc", "Rebuilding the mesh cleanly", 0.70),
    (r"decimate", "Simplifying the mesh", 0.80),
    (r"uv_bake", "Unwrapping and baking the texture", 0.86),
    (r"textured GLB", "Saving the .glb", 0.94),
]
GAME_FACES, GAME_ATLAS = 20000, 1024  # small enough for the game to embed


def available():
    return EXE.exists() and all((WEIGHTS / f"{f}.gguf").exists() for f in FILES)


def upright_png(image_path, dest):
    """trellis.cpp reads PNG/JPG only, and ignores phone-camera rotation, so hand it a clean upright PNG.
    Returns whether the picture already has a transparent background."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(image_path))
    has_alpha = "A" in img.getbands() or "transparency" in img.info
    img.convert("RGBA" if has_alpha else "RGB").save(dest)
    return has_alpha


def cutout_only(image_path, dest, cpu=False):
    """Just the background removal (BiRefNet, the same one TRELLIS.2 uses): about 10 s on the GPU, 30 s on the
    CPU (used while a model is being made, so the two don't fight over graphics memory)."""
    dest = Path(dest)
    picture = dest.with_name(dest.stem + "-in.png")
    upright_png(image_path, picture)
    cmd = [str(EXE), str(picture), str(dest.with_suffix(".glb")), "--models", str(WEIGHTS), "--bg-only"]
    if cpu:
        cmd += ["--backend", "CPU"]
    r = subprocess.run(cmd, cwd=str(EXE.parent), capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=subprocess.CREATE_NO_WINDOW)
    picture.unlink(missing_ok=True)
    made = dest.with_name(dest.stem + "_cutout.png")
    if r.returncode != 0 or not made.exists():
        raise RuntimeError("Couldn't cut the object out: " + "\n".join((r.stdout or "").splitlines()[-3:]))
    made.replace(dest)
    return dest


def generate(image_path, name=None, res=1024, size="game", remove_bg=True, progress=None, source_name=None, log=print,
             cutout=None):
    """cutout: an already cut-out picture of the object (from cutout_only) to use instead of cutting it out here."""
    step = progress or (lambda text, frac: log(text))
    t0 = time.time()
    image_path = Path(image_path)
    source_name = source_name or image_path.name
    folder = _new_folder(name or Path(source_name).stem)
    shutil.copyfile(image_path, folder / ("source" + image_path.suffix.lower()))
    glb = folder / "model.glb"

    picture = folder / "input.png"
    has_alpha = upright_png(cutout or image_path, picture)

    # --fa-fast: Radeon cards have no BF16 in Vulkan, so trellis.cpp's default attention precision crawls; F16 runs
    # about 3x faster with the same look (checked side by side on an RX 9070 XT)
    cmd = [str(EXE), str(picture), str(glb), "--models", str(WEIGHTS), "--res", str(res), "--require-gpu",
           "--fa-fast", "--verbose", "--dump-bg", "--copyright", f"Made with image-to-3d (TRELLIS.2, MIT) from {source_name}"]
    if not remove_bg and not has_alpha:
        cmd += ["--bg-removal", "threshold"]

    step("Starting TRELLIS.2 on the GPU", 0.01)
    lines, stage = [], 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(EXE.parent),
                            text=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
    for line in proc.stdout:
        line = line.rstrip()
        lines.append(line)
        m = re.match(r"\[(\d)/\d\]", line)
        if m and int(m.group(1)) in STAGES:
            stage = int(m.group(1))
            step(STAGES[stage][0], STAGES[stage][1])
        elif stage and (m := FLOW.search(line)):
            text, a, b = STAGES[stage]
            done, total = int(m.group(1)), int(m.group(2))
            step(f"{text} (step {done} of {total})", a + (b - a) * done / max(total, 1))
        elif stage == 7:
            for pattern, text, frac in CLEANUP:
                if re.search(pattern, line):
                    step(text, frac)
                    break
    proc.wait()
    (folder / "trellis-log.txt").write_text("\n".join(lines), encoding="utf-8")
    picture.unlink(missing_ok=True)
    for extra in [glb.with_suffix(".ply"), glb.with_name("model_base.png")]:
        extra.unlink(missing_ok=True)
    if proc.returncode != 0 or not glb.exists():
        tail = "\n".join(lines[-6:])
        if re.search(r"no (active )?voxels|empty", tail.lower()):
            raise NoObjectError()
        raise RuntimeError(f"TRELLIS.2 stopped (code {proc.returncode}). Last lines:\n{tail}")

    cut = glb.with_name("model_cutout.png")
    if cut.exists():
        cut.replace(folder / "cutout.png")
    if size == "game":
        step("Shrinking it for the game", 0.97)
        shrink_glb(glb, GAME_FACES, GAME_ATLAS)
    faces = normalize_glb(glb)
    _write_license(folder, source_name, "TRELLIS.2 by Microsoft, MIT license; run with trellis.cpp, MIT")
    seconds = time.time() - t0
    (folder / "info.json").write_text(json.dumps({
        "source": source_name, "engine": f"trellis-{res}", "size_mode": size, "faces": faces,
        "seconds": round(seconds, 1)}), encoding="utf-8")
    step(f"Done in {seconds / 60:.1f} min", 1.0)
    return Result(folder, glb, folder / "cutout.png", faces, glb.stat().st_size, seconds)


def shrink_glb(path, max_faces, size):
    """Game-size version of a model: simplify the mesh to `max_faces` triangles, unwrap it again, and repaint its
    textures from millions of points sampled on the detailed original, so the colors keep their detail."""
    import numpy as np
    import trimesh
    from scipy.spatial import cKDTree

    from pipeline import Atlas, jpeg_textures

    scene = trimesh.load(path, force="scene")
    hi = scene.to_geometry() if hasattr(scene, "to_geometry") else scene.dump(concatenate=True)
    if len(hi.faces) <= max_faces:
        return len(hi.faces)
    mat = hi.visual.material
    maps = {k: getattr(mat, k) for k in ("baseColorTexture", "metallicRoughnessTexture", "emissiveTexture")
            if getattr(mat, k, None) is not None}

    # colored points densely covering the detailed surface (finer than one texel)
    n = 3_000_000
    rng = np.random.default_rng(0)
    face = rng.choice(len(hi.faces), n, p=hi.area_faces / hi.area_faces.sum())
    r1, r2 = np.sqrt(rng.random(n)), rng.random(n)
    bary = np.stack([1 - r1, r1 * (1 - r2), r1 * r2], 1).astype(np.float32)
    pts = np.einsum("nk,nkd->nd", bary, hi.vertices[hi.faces[face]].astype(np.float32))
    uv = np.einsum("nk,nkd->nd", bary, hi.visual.uv[hi.faces[face]].astype(np.float32))
    samples = {}
    for k, img in maps.items():
        a = np.asarray(img.convert("RGBA" if k == "baseColorTexture" else "RGB"))
        h, w = a.shape[:2]
        x = np.clip((uv[:, 0] % 1) * w, 0, w - 1).astype(np.int32)
        y = np.clip((1 - uv[:, 1] % 1) * h, 0, h - 1).astype(np.int32)
        samples[k] = a[y, x].astype(np.float32)

    lo = trimesh.Trimesh(hi.vertices, hi.faces).simplify_quadric_decimation(face_count=max_faces)
    atlas = Atlas(lo, size)
    d, idx = cKDTree(pts).query(atlas.points, k=4, workers=-1)
    wgt = 1.0 / (d + 1e-6)
    wgt /= wgt.sum(1, keepdims=True)
    tex = {k: atlas.image((s[idx] * wgt[..., None]).sum(1).round().astype(np.uint8), jpeg=False)
           for k, s in samples.items()}
    material = trimesh.visual.material.PBRMaterial(
        baseColorTexture=tex.get("baseColorTexture"), metallicRoughnessTexture=tex.get("metallicRoughnessTexture"),
        emissiveTexture=tex.get("emissiveTexture"), emissiveFactor=mat.emissiveFactor,
        baseColorFactor=mat.baseColorFactor, metallicFactor=mat.metallicFactor, roughnessFactor=mat.roughnessFactor,
        alphaMode=mat.alphaMode, doubleSided=mat.doubleSided)
    out = atlas.mesh(jpeg_textures(material))
    Path(path).write_bytes(trimesh.Scene(out).export(file_type="glb", include_normals=True))
    return len(out.faces)


def normalize_glb(path, count_only=False):
    """Make the model 1 unit at its biggest, centered, sitting on y = 0, by wrapping the scene in one parent
    node (the mesh data and textures are left exactly as they are). Returns the triangle count; with count_only
    the file isn't changed."""
    data = Path(path).read_bytes()
    magic, version, _ = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        return 0
    jlen, jtype = struct.unpack_from("<II", data, 12)
    gltf = json.loads(data[20:20 + jlen])
    rest = data[20 + jlen:]

    import numpy as np

    def matrix(node):
        n = gltf["nodes"][node]
        if "matrix" in n:
            return np.array(n["matrix"], float).reshape(4, 4).T
        m = np.eye(4)
        if "scale" in n:
            m = np.diag([*n["scale"], 1.0]) @ m
        if "rotation" in n:
            x, y, z, w = n["rotation"]
            r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                          [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                          [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
            rm = np.eye(4)
            rm[:3, :3] = r
            m = rm @ m
        if "translation" in n:
            t = np.eye(4)
            t[:3, 3] = n["translation"]
            m = t @ m
        return m

    lo, hi, faces = np.full(3, np.inf), np.full(3, -np.inf), 0

    def walk(node, parent):
        nonlocal lo, hi, faces
        m = parent @ matrix(node)
        n = gltf["nodes"][node]
        if "mesh" in n:
            for prim in gltf["meshes"][n["mesh"]]["primitives"]:
                acc = gltf["accessors"][prim["attributes"]["POSITION"]]
                a, b = acc.get("min"), acc.get("max")
                if a and b:
                    corners = np.array([[x, y, z, 1] for x in (a[0], b[0]) for y in (a[1], b[1]) for z in (a[2], b[2])])
                    p = (m @ corners.T).T[:, :3]
                    lo, hi = np.minimum(lo, p.min(0)), np.maximum(hi, p.max(0))
                count = gltf["accessors"][prim["indices"]]["count"] if "indices" in prim else acc["count"]
                faces += count // 3
        for c in n.get("children", []):
            walk(c, m)

    scene = gltf["scenes"][gltf.get("scene", 0)]
    for r in scene["nodes"]:
        walk(r, np.eye(4))
    if count_only or not np.all(np.isfinite(lo)):
        return faces
    s = 1.0 / max(float(np.max(hi - lo)), 1e-9)
    center = [(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2]
    gltf["nodes"].append({"name": "image-to-3d", "children": scene["nodes"], "scale": [s, s, s],
                          "translation": [-center[0] * s, -center[1] * s, -center[2] * s]})
    scene["nodes"] = [len(gltf["nodes"]) - 1]

    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    out = struct.pack("<III", magic, version, 12 + 8 + len(js) + len(rest)) + struct.pack("<II", len(js), jtype) + js + rest
    Path(path).write_bytes(out)
    return faces
