"""The Image to 3D app: a small web page on this PC only (http://127.0.0.1:7861) that talks to the AI models.

Nothing is sent anywhere: the page, the AI and the files all stay on this computer.
"""
import gc
import json
import mimetypes
import os
import queue
import shutil
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pipeline
import trellis_engine

WEB = Path(__file__).resolve().parent / "web"
UPLOADS = pipeline.OUTPUT / ".uploads"
FILES = {"model.glb", "cutout.png", "license.txt"}
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/bmp": ".bmp", "image/gif": ".gif"}
ENGINES = {"trellis-1024": "TRELLIS.2", "trellis-512": "TRELLIS.2 (faster)", "triposr": "TripoSR draft"}

jobs = {}
work = queue.Queue()
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
            common = dict(name=job["name"], remove_bg=job["remove_bg"], progress=step, source_name=job["source_name"] or None)
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
            return self._send(200, {k: v for k, v in job.items() if k != "upload"})
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
        if url.path == "/api/jobs":
            ext = IMAGE_TYPES.get((self.headers.get("Content-Type") or "").split(";")[0].strip())
            size = int(self.headers.get("Content-Length") or 0)
            if not ext:
                return self._send(400, {"error": "That file isn't a picture. Use a PNG, JPG or WEBP image."})
            if not 0 < size < 60 * 2**20:
                return self._send(400, {"error": "That picture is too big (60 MB max)."})
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
            }
            work.put(jobs[job_id])
            return self._send(200, {"id": job_id})
        if url.path == "/api/open":
            target = pipeline.OUTPUT
            if q.get("folder"):
                f = (pipeline.OUTPUT / q["folder"]).resolve()
                if f.parent == pipeline.OUTPUT.resolve() and f.is_dir():
                    target = f
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(target)
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})


def main():
    pipeline.OUTPUT.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(UPLOADS, ignore_errors=True)
    server = None
    for port in range(7861, 7881):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit("No free port between 7861 and 7880.")
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
