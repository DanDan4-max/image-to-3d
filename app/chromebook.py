"""One HTML file with a 3D viewer and the chosen models inside, for a Chromebook (or any computer): copy the file
over, open it from the Files app (file://), and it works with no internet, installs or commands.

Browsers won't let a file:// page read other local files, so everything is packed into the one file: the viewer
(three.js, bundled into app/web/vendor/three/three-viewer.iife.js) and each model as base64 text.
"""
import base64
import io
import json
import time
from pathlib import Path

from PIL import Image

WEB = Path(__file__).resolve().parent / "web"
ENGINES = {"trellis-1024": "TRELLIS.2", "trellis-512": "TRELLIS.2 (faster)", "triposr": "TripoSR draft"}


def _thumb(folder):
    """A small see-through picture of the model for its card, or None."""
    cut = folder / "cutout.png"
    if not cut.exists():
        return None
    img = Image.open(cut).convert("RGBA")
    if img.getbbox():
        img = img.crop(img.getbbox())
    img.thumbnail((160, 160), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=80)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def build(folders, dest):
    """Write the viewer file with the models from `folders` (each holding a model.glb). Returns its size in bytes."""
    bundle = (WEB / "vendor" / "three" / "three-viewer.iife.js").read_text(encoding="utf-8")
    template = (WEB / "chromebook.html").read_text(encoding="utf-8")
    meta, blobs = [], []
    for i, folder in enumerate(folders):
        glb = folder / "model.glb"
        info = {}
        try:
            info = json.loads((folder / "info.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        lic = folder / "license.txt"
        meta.append({
            "name": folder.name, "size": glb.stat().st_size, "faces": info.get("faces"),
            "engine": ENGINES.get(info.get("engine"), ""), "thumb": _thumb(folder),
            "license": lic.read_text(encoding="utf-8") if lic.exists() else "",
            "made": time.strftime("%Y-%m-%d", time.localtime(glb.stat().st_mtime)),
        })
        blobs.append(f'<script type="text/plain" id="glb-{i}">{base64.b64encode(glb.read_bytes()).decode()}</script>')
    # nothing inside may close the <script> tags early
    bundle = bundle.replace("</script", "<\\/script")
    models = json.dumps(meta).replace("</", "<\\/")
    html = (template.replace("/*BUNDLE*/", bundle, 1)
            .replace("/*MODELS*/[]", models, 1)
            .replace("<!--GLBS-->", "\n".join(blobs), 1))
    Path(dest).write_text(html, encoding="utf-8")
    return Path(dest).stat().st_size
