"""Turn one picture into a colored 3D model (.glb) with TripoSR, on the GPU if PyTorch can see one.

TripoSR (MIT license, Tripo AI and Stability AI) does the AI part: it reads one picture and predicts the object's
shape and colors. This file wraps it: background removal, mesh clean-up, shrinking the mesh for games, painting a
texture, and saving a .glb that any 3D program or three.js game can open.
"""
import io
import json
import math
import os
import shutil
import sys
import time
import types
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
OUTPUT = ROOT / "output"

# Everything the AI needs lives in models/, and once Setup has run it never goes online again.
os.environ.setdefault("HF_HOME", str(MODELS / "hf-cache"))
os.environ.setdefault("U2NET_HOME", str(MODELS / "u2net"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")

import numpy as np
import torch
import trimesh
from PIL import Image, ImageOps


def _marching_cubes(volume, threshold):
    """Stand-in for torchmcubes (a CUDA-only add-on TripoSR imports) using scikit-image on the CPU."""
    from skimage import measure

    vol = volume.detach().float().cpu().numpy()
    if not (vol.min() < threshold < vol.max()):
        raise NoObjectError()
    verts, faces, _, _ = measure.marching_cubes(vol, threshold)
    # torchmcubes lists vertices as (x, y, z) = (k, j, i); TripoSR flips them back right after
    return torch.from_numpy(np.ascontiguousarray(verts[:, ::-1])), torch.from_numpy(faces.astype(np.int64))


sys.modules["torchmcubes"] = types.SimpleNamespace(marching_cubes=_marching_cubes)
sys.path.insert(0, str(ROOT / "vendor" / "TripoSR"))

from tsr.system import TSR  # noqa: E402
from tsr.utils import resize_foreground  # noqa: E402


class NoObjectError(RuntimeError):
    def __init__(self):
        super().__init__("Couldn't find a solid object in this picture. Try a picture of one object on a plain background.")


# "game" keeps files small (the game embeds its explosion models); "full" keeps every detail.
QUALITY = {
    "game": dict(mc_resolution=256, max_faces=20000, texture_size=1024, jpeg=True),
    "full": dict(mc_resolution=320, max_faces=200000, texture_size=2048, jpeg=False),
}


@dataclass
class Result:
    folder: Path
    glb: Path
    cutout: Path
    faces: int
    size_bytes: int
    seconds: float


class ImageTo3D:
    def __init__(self, device=None, log=print):
        self.log = log
        self.device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
        weights = MODELS / "TripoSR"
        if not (weights / "model.ckpt").exists():
            raise FileNotFoundError(f"Model weights not found in {weights}. Run Setup.bat first.")
        t = time.time()
        self.model = TSR.from_pretrained(str(weights), config_name="config.yaml", weight_name="model.ckpt")
        # bigger chunks are much faster on a 16 GB card; small cards and the CPU get the original size
        big = self.device != "cpu" and torch.cuda.get_device_properties(0).total_memory > 10 * 2**30
        self.model.renderer.set_chunk_size(131072 if big else 8192)
        self.model.to(self.device)
        self.rembg = None
        name = torch.cuda.get_device_name(0) if self.device != "cpu" else "CPU (slow)"
        self.log(f"AI model ready on {name} in {time.time() - t:.0f}s")

    # ---------- picture ----------

    def cutout(self, image, remove_bg=True, foreground_ratio=0.85):
        """The object on a transparent background, centered and padded the way TripoSR was trained."""
        image = ImageOps.exif_transpose(image)
        has_alpha = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
        image = image.convert("RGBA")
        if remove_bg and not (has_alpha and image.getextrema()[3][0] < 255):
            if self.rembg is None:
                import rembg
                self.rembg = rembg.new_session("u2net")
            import rembg
            image = rembg.remove(image, session=self.rembg)
        elif not remove_bg and not has_alpha:
            image.putalpha(255)
        if np.array(image)[..., 3].max() < 16:
            raise NoObjectError()
        return resize_foreground(image, foreground_ratio)

    @staticmethod
    def on_gray(cutout):
        rgba = np.asarray(cutout).astype(np.float32) / 255.0
        rgb = rgba[..., :3] * rgba[..., 3:4] + (1 - rgba[..., 3:4]) * 0.5
        return Image.fromarray((rgb * 255.0).astype(np.uint8))

    # ---------- the whole job ----------

    def generate(self, image_path, name=None, quality="game", colors="texture", remove_bg=True,
                 foreground_ratio=0.85, progress=None, source_name=None, cutout=None):
        """Picture in, output/<name>/model.glb out. progress(text, 0..1) is told what's happening.
        cutout: an already cut-out picture of the object to use instead of cutting it out here."""
        step = progress or (lambda text, frac: self.log(text))
        q = QUALITY[quality]
        t0 = time.time()
        image_path = Path(image_path)
        source_name = source_name or image_path.name
        folder = _new_folder(name or Path(source_name).stem)
        shutil.copyfile(image_path, folder / ("source" + image_path.suffix.lower()))

        step("Cutting the object out of the picture", 0.05)
        cut = self.cutout(Image.open(cutout or image_path), remove_bg, foreground_ratio)
        cut.save(folder / "cutout.png")

        step("AI is imagining the 3D shape", 0.15)
        with torch.no_grad():
            scene_code = self.model([self.on_gray(cut)], device=self.device)[0]

        step("Building the mesh", 0.45)
        mesh = self._mesh(scene_code, q["mc_resolution"])
        if len(mesh.faces) > q["max_faces"]:
            step("Simplifying the mesh", 0.6)
            mesh = mesh.simplify_quadric_decimation(face_count=q["max_faces"])
            mesh = _clean(mesh)

        if colors == "texture":
            step("Painting the texture", 0.7)
            out = self._textured(mesh, scene_code, q["texture_size"], q["jpeg"])
        else:
            step("Coloring the mesh", 0.7)
            rgb = self._colors(scene_code, mesh.vertices)
            out = trimesh.Trimesh(mesh.vertices, mesh.faces, vertex_normals=mesh.vertex_normals,
                                  vertex_colors=np.concatenate([rgb, np.full((len(rgb), 1), 255, np.uint8)], 1),
                                  process=False)

        step("Saving the .glb", 0.95)
        _to_game_axes(out)
        glb = folder / "model.glb"
        glb.write_bytes(trimesh.Scene(out).export(file_type="glb", include_normals=True))
        _write_license(folder, source_name)
        seconds = time.time() - t0
        (folder / "info.json").write_text(json.dumps({
            "source": source_name, "engine": "triposr", "size_mode": quality, "colors": colors, "faces": len(out.faces),
            "seconds": round(seconds, 1)}), encoding="utf-8")
        step(f"Done in {seconds:.0f}s", 1.0)
        return Result(folder, glb, folder / "cutout.png", len(out.faces), glb.stat().st_size, seconds)

    # ---------- steps ----------

    def _mesh(self, scene_code, resolution):
        mesh = self.model.extract_mesh(scene_code[None], False, resolution=resolution)[0]
        if len(mesh.faces) == 0:
            raise NoObjectError()
        return _clean(mesh)

    def _colors(self, scene_code, points):
        """The AI's color at each 3D point (in TripoSR's own coordinates), as 0-255 RGB."""
        out = []
        pts = torch.as_tensor(np.asarray(points, dtype=np.float32))
        with torch.no_grad():
            for chunk in torch.split(pts, 1 << 20):
                c = self.model.renderer.query_triplane(self.model.decoder, chunk.to(self.device), scene_code)["color"]
                out.append(c.float().cpu().numpy())
        rgb = np.concatenate(out) if out else np.zeros((0, 3), np.float32)
        return (np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)

    def _textured(self, mesh, scene_code, size, jpeg):
        """Unwrap the mesh into a flat atlas, then paint every texel with the AI's color at that spot."""
        atlas = Atlas(mesh, size)
        img = atlas.image(self._colors(scene_code, atlas.points), jpeg)
        material = trimesh.visual.material.PBRMaterial(baseColorTexture=img, metallicFactor=0.0, roughnessFactor=0.9)
        return atlas.mesh(material)


class Atlas:
    """A mesh unwrapped into a square texture: `points` are the 3D spots each used texel covers, so a texture is
    painted by working out one color per point."""

    def __init__(self, mesh, size):
        import xatlas

        self.source, self.size = mesh, size
        pad = max(2, round(size / 256))
        atlas = xatlas.Atlas()
        atlas.add_mesh(mesh.vertices, mesh.faces)
        pack = xatlas.PackOptions()
        pack.resolution, pack.padding, pack.bilinear = size, pad, True
        atlas.generate(pack_options=pack)
        self.vmap, self.faces, self.uvs = atlas[0]
        pos = _rasterize_positions(mesh.vertices[self.vmap], self.faces, self.uvs, size, pad)  # rows bottom-up
        self.filled = pos[..., 3] > 0
        self.points = pos[self.filled][:, :3]

    def image(self, values, jpeg):
        """One 0-255 color per point -> a texture, with edge colors spread into the empty space so seams and
        far-away mipmaps never show black."""
        from scipy import ndimage

        tex = np.zeros((self.size, self.size, values.shape[1]), np.uint8)
        tex[self.filled] = values
        _, (iy, ix) = ndimage.distance_transform_edt(~self.filled, return_indices=True)
        img = Image.fromarray(tex[iy, ix]).transpose(Image.FLIP_TOP_BOTTOM)
        return as_jpeg(img) if jpeg else img

    def mesh(self, material):
        m = self.source
        return trimesh.Trimesh(m.vertices[self.vmap], self.faces, vertex_normals=m.vertex_normals[self.vmap],
                               visual=trimesh.visual.TextureVisuals(uv=self.uvs, material=material), process=False)


# ---------- helpers ----------

def as_jpeg(img, quality=90):
    """A JPEG copy of a texture. trimesh writes JPEG-backed images into the .glb as JPEG and everything else as
    PNG, which is several times bigger for photo-like textures."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return Image.open(io.BytesIO(buf.getvalue()))


def jpeg_textures(material):
    """Store an opaque material's textures as JPEG (normal maps and see-through color maps stay PNG)."""
    opaque = getattr(material, "alphaMode", None) in (None, "OPAQUE")
    for k in ("baseColorTexture", "metallicRoughnessTexture", "emissiveTexture", "occlusionTexture"):
        img = getattr(material, k, None)
        if img is not None and (k != "baseColorTexture" or opaque):
            setattr(material, k, as_jpeg(img))
    return material


def _clean(mesh):
    """Drop floating crumbs, and make sure the faces point outwards."""
    parts = mesh.split(only_watertight=False)
    if len(parts) > 1:
        biggest = max(len(p.faces) for p in parts)
        mesh = trimesh.util.concatenate([p for p in parts if len(p.faces) >= 0.02 * biggest])
    if mesh.volume < 0:
        mesh.invert()
    return mesh


def _to_game_axes(mesh):
    """TripoSR is Z-up and looks along +X; glTF and three.js are Y-up and face +Z. Also: 1 unit tall at most,
    centered, sitting on the ground (y = 0)."""
    mesh.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))
    mesh.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
    lo, hi = mesh.bounds
    scale = 1.0 / max(hi - lo)
    center = np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2])
    mesh.apply_translation(-center)
    mesh.apply_scale(scale)


def _rasterize_positions(verts, faces, uvs, size, pad):
    """Draw each triangle at its atlas spot, storing its 3D position per texel (adapted from TripoSR's
    bake_texture.py, MIT). Edges are drawn `pad` texels thick so bilinear filtering never reads empty texels."""
    import moderngl

    ctx = moderngl.create_context(standalone=True)
    try:
        vs = """#version 330
            in vec2 in_uv; in vec3 in_pos; out vec3 v_pos;
            void main() { v_pos = in_pos; gl_Position = vec4(in_uv * 2.0 - 1.0, 0.0, 1.0); }"""
        fill = ctx.program(vertex_shader=vs, fragment_shader="""#version 330
            in vec3 v_pos; out vec4 o_col; void main() { o_col = vec4(v_pos, 1.0); }""")
        edges = ctx.program(vertex_shader=vs.replace("v_pos", "vg_pos"), geometry_shader="""#version 330
            uniform float u_resolution; uniform float u_dilation;
            layout (triangles) in; layout (triangle_strip, max_vertices = 12) out;
            in vec3 vg_pos[]; out vec3 vf_pos;
            void seg(int a, int b) {
                vec2 pa = gl_in[a].gl_Position.xy, pb = gl_in[b].gl_Position.xy;
                vec2 d = normalize((pb - pa) * u_resolution);
                vec2 o = vec2(-d.y, d.x) * u_dilation / u_resolution;
                gl_Position = vec4(pa + o, 0.0, 1.0); vf_pos = vg_pos[a]; EmitVertex();
                gl_Position = vec4(pa - o, 0.0, 1.0); vf_pos = vg_pos[a]; EmitVertex();
                gl_Position = vec4(pb + o, 0.0, 1.0); vf_pos = vg_pos[b]; EmitVertex();
                gl_Position = vec4(pb - o, 0.0, 1.0); vf_pos = vg_pos[b]; EmitVertex();
                EndPrimitive();
            }
            void main() { seg(0, 1); seg(1, 2); seg(2, 0); }""", fragment_shader="""#version 330
            in vec3 vf_pos; out vec4 o_col; void main() { o_col = vec4(vf_pos, 1.0); }""")
        content = [ctx.buffer(uvs.astype("f4").tobytes()).bind("in_uv", layout="2f"),
                   ctx.buffer(verts.astype("f4").tobytes()).bind("in_pos", layout="3f")]
        ibo = ctx.buffer(faces.astype("i4").tobytes())
        fbo = ctx.framebuffer(color_attachments=[ctx.texture((size, size), 4, dtype="f4")])
        fbo.use()
        fbo.clear(0.0, 0.0, 0.0, 0.0)
        edges["u_resolution"].value = size
        edges["u_dilation"].value = pad
        ctx.vertex_array(edges, content, ibo).render()
        ctx.vertex_array(fill, content, ibo).render()
        return np.frombuffer(fbo.color_attachments[0].read(), dtype="f4").reshape(size, size, 4).copy()
    finally:
        ctx.release()


def _new_folder(name):
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.strip().lower()).strip("-") or "model"
    folder, n = OUTPUT / safe, 2
    while folder.exists():
        folder, n = OUTPUT / f"{safe}-{n}", n + 1
    folder.mkdir(parents=True)
    return folder


def _write_license(folder, source_name, ai="TripoSR by Tripo AI and Stability AI, MIT license"):
    (folder / "license.txt").write_text(
        "Model Information:\n"
        f"* title:\t{folder.name}\n"
        f"* made from:\t{source_name}\n"
        f"* made with:\timage-to-3d ({ai}), {time.strftime('%Y-%m-%d')}\n"
        "\nModel License:\n"
        "* The AI model and this tool add no restrictions (MIT). You still need the right to use the source picture.\n",
        encoding="utf-8",
    )
