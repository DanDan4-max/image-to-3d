"""Fix the side the AI couldn't see by mirroring the side it could.

The AI only sees one side of the object, so the far side is a guess (a car can come out with wheels on one side
only). Most things people make models of (cars, robots, characters, trophies) are mirror-symmetric, so: find the
model's mirror plane, cut the model in half there, and replace one half with a mirror copy of the other. The
mirrored half reuses the same texture, so the file doesn't get bigger. Both versions are made; the user picks.
"""
import numpy as np
import trimesh
from scipy.spatial import cKDTree

from pipeline import jpeg_textures
from trellis_engine import normalize_glb


def load(path):
    scene = trimesh.load(path, force="scene")
    return scene.to_geometry() if hasattr(scene, "to_geometry") else scene.dump(concatenate=True)


def mirror_plane(mesh, samples=6000):
    """The upright plane the model is most nearly mirror-symmetric about, as (origin, unit normal).
    Tries every direction around the vertical axis (models stand Y-up) and a few positions along each."""
    pts = np.asarray(mesh.sample(samples, seed=0))
    tree = cKDTree(pts)
    size = float(np.max(mesh.extents))
    best = (np.inf, None, None)
    for deg in range(0, 180, 2):
        a = np.radians(deg)
        n = np.array([np.cos(a), 0.0, np.sin(a)])
        proj = pts @ n
        mid = (proj.min() + proj.max()) / 2
        for shift in np.linspace(-0.06, 0.06, 7) * size:
            d = mid + shift
            mirrored = pts - 2 * (proj - d)[:, None] * n
            dist, _ = tree.query(mirrored, distance_upper_bound=0.1 * size)
            score = np.mean(np.minimum(dist, 0.1 * size))
            if score < best[0]:
                best = (score, d, n)
    # refine the direction to half a degree
    _, d, n = best
    base = np.degrees(np.arctan2(n[2], n[0]))
    for deg in np.arange(base - 2, base + 2.01, 0.5):
        a = np.radians(deg)
        m = np.array([np.cos(a), 0.0, np.sin(a)])
        proj = pts @ m
        for dd in np.linspace(d - 0.01 * size, d + 0.01 * size, 5):
            dist, _ = tree.query(pts - 2 * (proj - dd)[:, None] * m, distance_upper_bound=0.1 * size)
            score = np.mean(np.minimum(dist, 0.1 * size))
            if score < best[0]:
                best = (score, dd, m)
    score, d, n = best
    return d * n, n, score / size


def mirrored(mesh, origin, normal):
    """Keep the half on the `normal` side of the plane and add its mirror image as the other half."""
    v, faces, uv = trimesh.intersections.slice_faces_plane(mesh.vertices, mesh.faces, normal, origin,
                                                            uv=np.asarray(mesh.visual.uv))
    flipped = v - 2 * ((v - origin) @ normal)[:, None] * normal
    material = jpeg_textures(mesh.visual.material.copy())
    out = trimesh.Trimesh(np.vstack([v, flipped]), np.vstack([faces, faces[:, ::-1] + len(v)]),
                          visual=trimesh.visual.TextureVisuals(uv=np.vstack([uv, uv]), material=material),
                          process=False)
    out.merge_vertices()  # stitch the two halves together along the cut so it shades as one smooth surface
    return out


def make_versions(folder):
    """Writes mirror-a.glb and mirror-b.glb (each keeps a different half) next to model.glb. A keeps the more
    detailed half (the simplifier spends more triangles where there's more detail), which is usually the side the
    picture showed. Returns how symmetric the model already looked (0 = perfectly)."""
    src = folder / ("original.glb" if (folder / "original.glb").exists() else "model.glb")
    mesh = load(src)
    origin, normal, score = mirror_plane(mesh)
    halves = sorted((mirrored(mesh, origin, n) for n in (normal, -normal)), key=lambda m: -len(m.faces))
    for name, out in zip(("mirror-a.glb", "mirror-b.glb"), halves):
        path = folder / name
        path.write_bytes(trimesh.Scene(out).export(file_type="glb", include_normals=True))
        normalize_glb(path)
    return score
