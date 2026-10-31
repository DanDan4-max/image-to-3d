"""The Image to 3D app: a small web page on this PC only (http://127.0.0.1:7861) that talks to the AI models.

Nothing is sent anywhere: the page, the AI and the files all stay on this computer.
"""
import gc
import json
import mimetypes
import os
import queue
import shutil
import socket
import threading
import time
import traceback
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pipeline
import symmetry
import trellis_engine

WEB = Path(__file__).resolve().parent / "web"
UPLOADS = pipeline.OUTPUT / ".uploads"
FILES = {"model.glb", "cutout.png", "license.txt", "original.glb", "mirror-a.glb", "mirror-b.glb"}
VERSIONS = {"original", "mirror-a", "mirror-b"}
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/bmp": ".bmp", "image/gif": ".gif"}
ENGINES = {"trellis-1024": "TRELLIS.2", "trellis-512": "TRELLIS.2 (faster)", "triposr": "TripoSR draft"}

jobs = {}
work = queue.Queue()
mirror_lock = threading.Lock()
cutout_lock = threading.Lock()
triposr = None  # loaded the first time a quick draft is asked for, and dropped again before TRELLIS.2 runs


def engines():
    return {"trellis": trellis_engine.available(), "triposr": (pipeline.MODELS / "TripoSR" / "model.ckpt").exists()}


def status():
    e = engines()
    problems = []
    if not e["trellis"]:
        problems.append("TRELLIS.2 isn't installed yet (only quick drafts work)")
    if not pipeline.torch.cuda.is_available():
        problems.append("PyTorch can't see the GPU, so quick drafts will be slow")
    gpu = pipeline.torch.cuda.get_device_name(0) if pipeline.torch.cuda.is_available() else "CPU"
    return {"ready": any(e.values()), "engines": e, "device": gpu, "problems": problems, "output": str(pipeline.OUTPUT)}


def free_triposr():
    global triposr
    if triposr is not None:
        triposr = None
        gc.collect()
        pipeline.torch.cuda.empty_cache()


def worker():
    global triposr
    while True:
        job = work.get()
        job.update(state="running", started=time.time())

        def step(text, frac):
            job["step"], job["progress"] = text, frac

        try:
            common = dict(name=job["name"], remove_bg=job["remove_bg"], progress=step, source_name=job["source_name"] or None,
                          cutout=job["cutout"])
            if job["engine"] == "triposr":
                if triposr is None:
                    step("Loading the TripoSR model", 0.02)
                    triposr = pipeline.ImageTo3D(log=lambda s: print(s, flush=True))
                r = triposr.generate(job["upload"], quality=job["size"], **common)
            else:
                free_triposr()  # TRELLIS.2 at full detail wants the whole 16 GB
                r = trellis_engine.generate(job["upload"], res=int(job["engine"].split("-")[1]), size=job["size"], **common)
            job.update(state="done", result=summary(r.folder))
        except Exception as e:
            traceback.print_exc()
            job.update(state="error", error=str(e) if isinstance(e, pipeline.NoObjectError) else f"Something went wrong: {e}")
        finally:
            Path(job["upload"]).unlink(missing_ok=True)


def summary(folder):
    glb = folder / "model.glb"
    meta = {}
    try:
        meta = json.loads((folder / "info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {"folder": folder.name, "path": str(glb), "size": glb.stat().st_size, "time": glb.stat().st_mtime,
            "engine_label": ENGINES.get(meta.get("engine"), ""), **meta}


def recent():
    if not pipeline.OUTPUT.exists():
        return []
    folders = [f for f in pipeline.OUTPUT.iterdir() if (f / "model.glb").exists()]
    folders.sort(key=lambda f: (f / "model.glb").stat().st_mtime, reverse=True)
    return [summary(f) for f in folders[:40]]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body=b"", kind="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path):
        if not path.is_file():
            return self._send(404, {"error": "not found"})
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            kind = "text/javascript"
        elif path.suffix == ".glb":
            kind = "model/gltf-binary"
        self._send(200, path.read_bytes(), kind)

    def _local_only(self):
        # only this PC's own page may use the app (stops other websites from poking at it)
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost") or (self.command == "POST" and self.headers.get("X-Image-To-3D") != "1"):
            self._send(403, {"error": "local use only"})
            return False
        return True

    def do_GET(self):
        if not self._local_only():
            return
        p = unquote(urlparse(self.path).path)
        if p == "/":
            return self._file(WEB / "index.html")
        if p.startswith("/web/"):
            target = (WEB / p[5:]).resolve()
            return self._file(target) if WEB.resolve() in target.parents else self._send(404, {"error": "not found"})
        if p == "/api/status":
            return self._send(200, status())
        if p == "/api/recent":
            return self._send(200, recent())
        if p.startswith("/api/jobs/"):
            job = jobs.get(p[len("/api/jobs/"):])
            if not job:
                return self._send(404, {"error": "no such job"})
            return self._send(200, {k: v for k, v in job.items() if k not in ("upload", "cutout")})
        if p.startswith("/uploads/"):
            cut = cutout_file(p[len("/uploads/"):].removesuffix(".png"))
            return self._file(cut) if cut else self._send(404, {"error": "not found"})
        if p.startswith("/files/"):
            parts = p[len("/files/"):].split("/")
            if len(parts) == 2 and parts[1] in FILES and parts[0] and not parts[0].startswith("."):
                folder = (pipeline.OUTPUT / parts[0]).resolve()
                if folder.parent == pipeline.OUTPUT.resolve():
                    return self._file(folder / parts[1])
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._local_only():
            return
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path in ("/api/jobs", "/api/cutout"):
            ext = IMAGE_TYPES.get((self.headers.get("Content-Type") or "").split(";")[0].strip())
            size = int(self.headers.get("Content-Length") or 0)
            if not ext:
                return self._send(400, {"error": "That file isn't a picture. Use a PNG, JPG or WEBP image."})
            if not 0 < size < 60 * 2**20:
                return self._send(400, {"error": "That picture is too big (60 MB max)."})
        if url.path == "/api/cutout":
            UPLOADS.mkdir(parents=True, exist_ok=True)
            cut_id = uuid.uuid4().hex[:12]
            upload = UPLOADS / f"{cut_id}-picture{ext}"
            upload.write_bytes(self.rfile.read(size))
            try:
                with cutout_lock:
                    make_cutout(upload, UPLOADS / f"{cut_id}.png")
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": str(e) if isinstance(e, pipeline.NoObjectError) else f"Couldn't cut it out: {e}"})
            finally:
                upload.unlink(missing_ok=True)
            return self._send(200, {"id": cut_id, "url": f"/uploads/{cut_id}.png"})
        if url.path == "/api/jobs":
            engine = q.get("engine") if q.get("engine") in ENGINES else "trellis-1024"
            if not engines()["triposr" if engine == "triposr" else "trellis"]:
                return self._send(400, {"error": f"{ENGINES[engine]} isn't installed. Run Setup.bat."})
            UPLOADS.mkdir(parents=True, exist_ok=True)
            job_id = uuid.uuid4().hex[:12]
            upload = UPLOADS / f"{job_id}{ext}"
            upload.write_bytes(self.rfile.read(size))
            ahead = sum(1 for j in jobs.values() if j["state"] in ("queued", "running"))
            jobs[job_id] = {
                "id": job_id, "state": "queued", "progress": 0.0, "started": None,
                "step": f"Waiting for {ahead} other model{'s' if ahead > 1 else ''} to finish" if ahead else "Starting",
                "name": (q.get("name") or "model")[:60], "engine": engine,
                "size": "full" if q.get("size") == "full" else "game", "remove_bg": q.get("remove_bg", "1") != "0",
                "upload": str(upload), "source_name": q.get("filename", "")[:120],
                "cutout": cutout_file(q.get("cutout")) if q.get("remove_bg", "1") != "0" else None,
            }
            work.put(jobs[job_id])
            return self._send(200, {"id": job_id})
        if url.path == "/api/open":
            target = model_folder(q.get("folder")) or pipeline.OUTPUT
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(target)
            return self._send(200, {"ok": True})
        if url.path in ("/api/mirror", "/api/choose"):
            folder = model_folder(q.get("folder"))
            if not folder or not (folder / "model.glb").exists():
                return self._send(404, {"error": "That model isn't there any more."})
            original = folder / "original.glb"
            if not original.exists():
                shutil.copyfile(folder / "model.glb", original)  # so the untouched model can always come back
            if url.path == "/api/mirror":
                try:
                    with mirror_lock:
                        symmetry.make_versions(folder)
                except Exception as e:
                    traceback.print_exc()
                    return self._send(500, {"error": f"Couldn't mirror this model: {e}"})
                return self._send(200, {"ok": True})
            version = q.get("version")
            if version not in VERSIONS or not (folder / f"{version}.glb").exists():
                return self._send(400, {"error": "Pick one of the versions first."})
            shutil.copyfile(folder / f"{version}.glb", folder / "model.glb")
            for f in ("mirror-a.glb", "mirror-b.glb"):
                (folder / f).unlink(missing_ok=True)
            info = folder / "info.json"
            meta = json.loads(info.read_text(encoding="utf-8")) if info.exists() else {}
            meta["mirrored"] = version != "original"
            meta["faces"] = trellis_engine.normalize_glb(folder / "model.glb", count_only=True)
            info.write_text(json.dumps(meta), encoding="utf-8")
            return self._send(200, summary(folder))
        self._send(404, {"error": "not found"})


def cutout_file(cut_id):
    """The cut-out picture made by /api/cutout, if `cut_id` really is one."""
    if not cut_id or not all(c in "0123456789abcdef" for c in cut_id) or len(cut_id) != 12:
        return None
    f = UPLOADS / f"{cut_id}.png"
    return f if f.exists() else None


def make_cutout(picture, dest):
    """Cut the object out with TRELLIS.2's own remover (BiRefNet), or TripoSR's if TRELLIS.2 isn't installed.
    While a model is being made the GPU is busy, so the remover runs on the CPU instead (slower, same result)."""
    if trellis_engine.available():
        busy = any(j["state"] == "running" for j in jobs.values())
        return trellis_engine.cutout_only(picture, dest, cpu=busy)
    import rembg
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(picture)).convert("RGBA")
    rembg.remove(img, session=rembg.new_session("u2net")).save(dest)
    return dest


def model_folder(name):
    """output/<name> if that's really one of the model folders, else None."""
    if not name or name.startswith("."):
        return None
    f = (pipeline.OUTPUT / name).resolve()
    return f if f.parent == pipeline.OUTPUT.resolve() and f.is_dir() else None


class Server(ThreadingHTTPServer):
    # Python turns on SO_REUSEADDR, which on Windows lets a second copy of the app bind the same port and answer
    # half of the first one's requests. Claim the port exclusively instead.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def already_running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1) as r:
            return "engines" in json.loads(r.read())
    except (OSError, ValueError):
        return False


def main():
    pipeline.OUTPUT.mkdir(parents=True, exist_ok=True)
    first = int(os.environ.get("IMAGE_TO_3D_PORT", 7861))
    if "IMAGE_TO_3D_PORT" not in os.environ and already_running(first):
        # one copy at a time, so two never fight over the GPU: just show the one that's open
        print(f"Image to 3D is already open at http://127.0.0.1:{first}/")
        webbrowser.open(f"http://127.0.0.1:{first}/")
        return
    for old in UPLOADS.glob("*"):  # leftovers from a crash
        if time.time() - old.stat().st_mtime > 86400:
            old.unlink(missing_ok=True)
    server = None
    for port in range(first, first + 20):
        try:
            server = Server(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit(f"No free port between {first} and {first + 19}.")
    url = f"http://127.0.0.1:{server.server_port}/"
    threading.Thread(target=worker, daemon=True).start()
    print(f"\n  Image to 3D is running at {url}\n  Keep this window open while you use it. Close it to quit.\n", flush=True)
    if os.environ.get("IMAGE_TO_3D_NO_BROWSER") != "1":
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
