"""The whole app as one HTML file for a Chromebook (or any computer): open it straight from the Files app (file://).

It's the same page as on the PC (index.html, style.css, app.js), packed into one file, because a file:// page can't
load other local files. In it, the models come from your Cloudflare site, and making new ones goes to this PC
through Cloudflare, with the passcode (see cloud.py).
"""
import json
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"
IMPORTS = """import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
"""


def _inline(text):
    return text.replace("</script", "<\\/script")  # nothing inside may close the <script> tag early


def build(site):
    """The file's HTML, with `site` (https://image-to-3d.<you>.workers.dev) as where it finds models and the PC."""
    page = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    if not app.startswith(IMPORTS):
        raise RuntimeError("app.js's imports changed; update chromebook.IMPORTS")
    # three.js comes as one plain script (vendor/three/three-viewer.iife.js, which sets window.V3) instead of imports
    app = ("(() => {\n'use strict';\nconst THREE = V3.THREE, { GLTFLoader, OrbitControls, RoomEnvironment } = V3;\n"
           + app[len(IMPORTS):] + "\n})();")
    scripts = (f"<script>window.I3D = {json.dumps({'site': site})};</script>\n"
               f"<script>{_inline((WEB / 'vendor' / 'three' / 'three-viewer.iife.js').read_text(encoding='utf-8'))}</script>\n"
               f"<script>{_inline(app)}</script>")
    css = (WEB / "style.css").read_text(encoding="utf-8")
    page = page.replace('<link rel="stylesheet" href="/web/style.css">', f"<style>\n{css}</style>", 1)
    page = re.sub(r'\s*<script type="importmap">.*?</script>', "", page, count=1, flags=re.S)
    page = page.replace('<script type="module" src="/web/app.js"></script>', scripts, 1)
    return page
