"""The Image to 3D app: a small web page on this PC only (http://127.0.0.1:7861) that talks to the AI models.

Nothing is sent anywhere unless the user turns on the Chromebook parts (see cloud.py): then the models go up to their
own Cloudflare site, and the Chromebook file can use this app through Cloudflare (port 7891), but only with the
passcode.
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

import chromebook
import cloud
import picture_ai
import pipeline
import symmetry
import trellis_engine
import web_search

WEB = Path(__file__).resolve().parent / "web"
UPLOADS = pipeline.OUTPUT / ".uploads"
FILES = {"model.glb", "cutout.png", "license.txt", "original.glb", "mirror-a.glb", "mirror-b.glb"}
VERSIONS = {"original", "mirror-a", "mirror-b"}
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/bmp": ".bmp", "image/gif": ".gif"}
ENGINES = {"trellis-1024": "TRELLIS.2", "trellis-512": "TRELLIS.2 (faster)", "triposr": "TripoSR draft"}

REMOTE_PORT = 7891
PC_ONLY = {"/api/open", "/api/chromebook", "/api/cloud", "/api/cloud/setup", "/api/cloud/publish", "/api/cloud/settings"}
CHROMEBOOK_FILE = "Image to 3D for Chromebook.html"
remote = {"server": None, "error": None}
remote_lock = threading.Lock()
wrong_codes = {}  # who -> times they typed a wrong passcode, to slow down guessing

jobs = {}
work = queue.Queue()
mirror_lock = threading.Lock()
cutout_lock = threading.Lock()
triposr = None  # loaded the first time a quick draft is asked for, and dropped again before TRELLIS.2 runs


def engines():
    return {"trellis": trellis_engine.available(), "triposr": (pipeline.MODELS / "TripoSR" / "model.ckpt").exists(),
            "pictures": picture_ai.available()}


def status():
    e = engines()
    problems = []
    if not e["trellis"]:
        problems.append("TRELLIS.2 isn't installed yet (only quick drafts work)")
    if not pipeline.torch.cuda.is_available():
        problems.append("PyTorch can't see the GPU, so quick drafts will be slow")
    gpu = pipeline.torch.cuda.get_device_name(0) if pipeline.torch.cuda.is_available() else "CPU"
    return {"ready": e["trellis"] or e["triposr"], "engines": e, "device": gpu, "problems": problems,
            "output": str(pipeline.OUTPUT)}


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
            if job["kind"] in ("imagine", "edit"):
                free_triposr()
                out = UPLOADS / f"{job['id']}.png"
                if job["kind"] == "imagine":
                    picture_ai.imagine(job["prompt"], out, seed=job["seed"], progress=step)
                else:
                    picture_ai.edit(job["upload"], job["mask"], job["prompt"], out, seed=job["seed"], progress=step)
                job.update(state="done", result={"id": job["id"], "image": f"/uploads/{job['id']}.png"})
                continue
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
            if job.get("credit"):
                add_credit(r.folder, job["credit"])
            job.update(state="done", result=summary(r.folder))
            cloud.publish_later(all_models)
        except Exception as e:
            traceback.print_exc()
            job.update(state="error", error=str(e) if isinstance(e, pipeline.NoObjectError) else f"Something went wrong: {e}")
        finally:
            for f in (job.get("upload"), job.get("mask")):
                if f:
                    Path(f).unlink(missing_ok=True)


def add_credit(folder, credit):
    """Where a web picture came from and its license, for license.txt (needed to use most free pictures)."""
    lic = folder / "license.txt"
    lines = lic.read_text(encoding="utf-8").splitlines() if lic.exists() else []
    lines.insert(min(3, len(lines)), f"* picture from:\t{credit}")
    lines.append("* Credit the picture's creator wherever you use this model. A license with SA (share-alike) also "
                 "means the model must be shared under that same license.")
    lic.write_text("\n".join(lines) + "\n", encoding="utf-8")
    info = folder / "info.json"
    meta = json.loads(info.read_text(encoding="utf-8")) if info.exists() else {}
    meta["credit"] = credit
    info.write_text(json.dumps(meta), encoding="utf-8")


def queue_job(**fields):
    ahead = sum(1 for j in jobs.values() if j["state"] in ("queued", "running"))
    job_id = fields.pop("id", None) or uuid.uuid4().hex[:12]
    jobs[job_id] = {"id": job_id, "state": "queued", "progress": 0.0, "started": None, "upload": None, "mask": None,
                    "step": f"Waiting for {ahead} other job{'s' if ahead > 1 else ''} to finish" if ahead else "Starting",
                    **fields}
    work.put(jobs[job_id])
    return job_id


def summary(folder):
    glb = folder / "model.glb"
    meta = {}
    try:
        meta = json.loads((folder / "info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {"folder": folder.name, "path": str(glb), "size": glb.stat().st_size, "time": glb.stat().st_mtime,
            "engine_label": ENGINES.get(meta.get("engine"), ""), **meta}


def recent(limit=40):
    if not pipeline.OUTPUT.exists():
        return []
    folders = [f for f in pipeline.OUTPUT.iterdir() if (f / "model.glb").exists()]
    folders.sort(key=lambda f: (f / "model.glb").stat().st_mtime, reverse=True)
    return [summary(f) for f in folders[:limit]]


def all_models():
    return recent(limit=10000)


# ---------- the Chromebook file: making models from it, through Cloudflare ----------
def set_remote(on):
    """Open (or close) the Chromebook's way in: a door on this PC only (127.0.0.1), plus a Cloudflare tunnel to it."""
    with remote_lock:
        if on and not remote["server"]:
            server = None
            for port in range(REMOTE_PORT, REMOTE_PORT + 10):
                try:
                    server = Server(("127.0.0.1", port), RemoteHandler)
                    break
                except OSError:
                    continue
            if server is None:
                remote["error"] = f"No free port between {REMOTE_PORT} and {REMOTE_PORT + 9}."
                return
            remote.update(server=server, error=None)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            cloud.tunnel.start(server.server_port)
        elif not on and remote["server"]:
            cloud.tunnel.stop()
            remote["server"].shutdown()
            remote["server"].server_close()
            remote["server"] = None
        cloud.settings["make"] = on
        cloud.save()


def cloud_info():
    return {**cloud.info(), "remote_error": remote["error"], "count": len(all_models())}


class Handler(BaseHTTPRequestHandler):
    remote = False  # True for RemoteHandler: a request from the Chromebook file, through Cloudflare

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body=b"", kind="application/json", headers=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
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

    def _allowed(self):
        # only this PC's own page may use the app (stops other websites from poking at it)
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost") or (self.command == "POST" and self.headers.get("X-Image-To-3D") != "1"):
            self._send(403, {"error": "local use only"})
            return False
        return True

    def do_GET(self):
        if not self._allowed():
            return
        p = unquote(urlparse(self.path).path)
        if self.remote and p in PC_ONLY:
            return self._send(403, {"error": "That only works on the PC itself."})
        if p == "/":
            return self._file(WEB / "index.html")
        if p == "/api/cloud":
            return self._send(200, cloud_info())
        if p.startswith("/web/"):
            target = (WEB / p[5:]).resolve()
            return self._file(target) if WEB.resolve() in target.parents else self._send(404, {"error": "not found"})
        if p == "/api/status":
            s = {**status(), "remote": self.remote}
            if self.remote:
                s.pop("output")  # where the files are on the PC is no business of the internet
            return self._send(200, s)
        if p == "/api/recent":
            return self._send(200, all_models() if "all=1" in urlparse(self.path).query else recent())
        if p == "/api/search":
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            try:
                return self._send(200, web_search.search(q.get("q", ""), q.get("source", "commons")))
            except Exception as e:
                name = "Openverse" if q.get("source") == "openverse" else "Wikimedia Commons"
                return self._send(502, {"error": f"{name} isn't answering right now ({type(e).__name__}). "
                                                 "Try the other source, or search in your browser."})
        if p.startswith("/api/jobs/"):
            job = jobs.get(p[len("/api/jobs/"):])
            if not job:
                return self._send(404, {"error": "no such job"})
            return self._send(200, {k: v for k, v in job.items() if k not in ("upload", "cutout", "mask")})
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
        if not self._allowed():
            return
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if self.remote and url.path in PC_ONLY:
            return self._send(403, {"error": "That only works on the PC itself."})
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
                    upscaled = make_cutout(upload, UPLOADS / f"{cut_id}.png")
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": str(e) if isinstance(e, pipeline.NoObjectError) else f"Couldn't cut it out: {e}"})
            finally:
                upload.unlink(missing_ok=True)
            return self._send(200, {"id": cut_id, "url": f"/uploads/{cut_id}.png", "upscaled": upscaled})
        if url.path == "/api/jobs":
            engine = q.get("engine") if q.get("engine") in ENGINES else "trellis-1024"
            if not engines()["triposr" if engine == "triposr" else "trellis"]:
                return self._send(400, {"error": f"{ENGINES[engine]} isn't installed. Run Setup.bat."})
            UPLOADS.mkdir(parents=True, exist_ok=True)
            job_id = uuid.uuid4().hex[:12]
            upload = UPLOADS / f"{job_id}-picture{ext}"
            upload.write_bytes(self.rfile.read(size))
            queue_job(
                id=job_id, kind="model", name=(q.get("name") or "model")[:60], engine=engine,
                size="full" if q.get("size") == "full" else "game", remove_bg=q.get("remove_bg", "1") != "0",
                upload=str(upload), source_name=q.get("filename", "")[:120], credit=q.get("credit", "")[:400],
                cutout=cutout_file(q.get("cutout")) if q.get("remove_bg", "1") != "0" else None)
            return self._send(200, {"id": job_id})
        if url.path == "/api/chromebook":
            if not cloud.settings.get("site"):
                return self._send(400, {"error": "Set up Cloudflare first (step 1)."})
            dest = pipeline.OUTPUT / CHROMEBOOK_FILE
            try:
                dest.write_text(chromebook.build(cloud.settings["site"]), encoding="utf-8")
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": f"Couldn't make the file: {e}"})
            return self._send(200, {"path": str(dest), "size": dest.stat().st_size})
        if url.path in ("/api/cloud/setup", "/api/cloud/publish"):
            if not cloud.installed():
                return self._send(400, {"error": "Cloudflare's tools aren't installed. Run Setup.bat."})
            if cloud.state["busy"]:
                return self._send(409, {"error": "Already working on it."})
            run = cloud.setup if url.path == "/api/cloud/setup" or not cloud.settings.get("site") else cloud.publish
            threading.Thread(target=run, args=(all_models(),), daemon=True).start()
            return self._send(200, cloud_info())
        if url.path == "/api/cloud/settings":
            size = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(size)) if 0 < size < 100000 else {}
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                return self._send(400, {"error": "Bad request."})
            if "auto" in body:
                cloud.settings["auto"] = bool(body["auto"])
            if isinstance(body.get("skip"), list):
                cloud.settings["skip"] = [str(s) for s in body["skip"] if model_folder(str(s))]
            cloud.save()
            if "make" in body:
                set_remote(bool(body["make"]))
            return self._send(200, cloud_info())
        if url.path == "/api/fetch":
            size = int(self.headers.get("Content-Length") or 0)
            try:
                link = json.loads(self.rfile.read(size)).get("url", "") if 0 < size < 10000 else ""
                data, kind = web_search.fetch_image(str(link))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                return self._send(502, {"error": f"Couldn't download that picture ({type(e).__name__})."})
            return self._send(200, data, kind)
        if url.path in ("/api/imagine", "/api/edit"):
            if not picture_ai.available():
                return self._send(400, {"error": "The picture AI isn't installed. Run Setup.bat."})
            size = int(self.headers.get("Content-Length") or 0)
            if not 0 < size < 80 * 2**20:
                return self._send(400, {"error": "Nothing to work with."})
            try:
                body = json.loads(self.rfile.read(size))
            except ValueError:
                return self._send(400, {"error": "Bad request."})
            prompt = str(body.get("prompt") or "").strip()[:800]
            if not prompt:
                return self._send(400, {"error": "Type what you want first."})
            seed = int(body.get("seed", -1))
            if url.path == "/api/imagine":
                return self._send(200, {"id": queue_job(kind="imagine", prompt=prompt, seed=seed)})
            UPLOADS.mkdir(parents=True, exist_ok=True)
            job_id = uuid.uuid4().hex[:12]
            picture = save_data_url(body.get("image"), UPLOADS / f"{job_id}-edit.png")
            if not picture:
                return self._send(400, {"error": "No picture to fix."})
            mask = save_data_url(body.get("mask"), UPLOADS / f"{job_id}-mask.png")
            return self._send(200, {"id": queue_job(id=job_id, kind="edit", prompt=prompt, seed=seed,
                                                    upload=str(picture), mask=str(mask) if mask else None)})
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
            cloud.publish_later(all_models)
            return self._send(200, summary(folder))
        self._send(404, {"error": "not found"})


def save_data_url(data, dest):
    """Save a 'data:image/png;base64,...' string from the page as a PNG; None if there isn't a picture in it."""
    import base64
    import io
    from PIL import Image
    if not isinstance(data, str) or "," not in data or not data.startswith("data:image/"):
        return None
    try:
        img = Image.open(io.BytesIO(base64.b64decode(data.split(",", 1)[1])))
        img.load()
    except Exception:
        return None
    img.save(dest, format="PNG")
    return dest


def cutout_file(cut_id):
    """The cut-out picture made by /api/cutout, if `cut_id` really is one."""
    if not cut_id or not all(c in "0123456789abcdef" for c in cut_id) or len(cut_id) != 12:
        return None
    f = UPLOADS / f"{cut_id}.png"
    return f if f.exists() else None


def make_cutout(picture, dest):
    """Cut the object out with TRELLIS.2's own remover (BiRefNet), or TripoSR's if TRELLIS.2 isn't installed.
    While a model is being made the GPU is busy, so the remover runs on the CPU instead (slower, same result).
    Small pictures get sharpened 4x first; returns whether that happened."""
    up = dest.with_name(dest.stem + "-up.png")
    big = picture_ai.upscale_if_small(picture, up)
    try:
        if trellis_engine.available():
            busy = any(j["state"] == "running" for j in jobs.values())
            trellis_engine.cutout_only(big, dest, cpu=busy)
        else:
            import rembg
            from PIL import Image, ImageOps
            img = ImageOps.exif_transpose(Image.open(big)).convert("RGBA")
            rembg.remove(img, session=rembg.new_session("u2net")).save(dest)
    finally:
        up.unlink(missing_ok=True)
    return Path(big) != Path(picture)


def model_folder(name):
    """output/<name> if that's really one of the model folders, else None."""
    if not name or name.startswith("."):
        return None
    f = (pipeline.OUTPUT / name).resolve()
    return f if f.parent == pipeline.OUTPUT.resolve() and f.is_dir() else None


NOT_HERE = b"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Image to 3D</title><body style="font:16px/1.5 system-ui,sans-serif;background:#0f1218;color:#e7ebf2;padding:24px">
<h1 style="font-size:20px">This is the way in to someone's Image to 3D</h1>
<p>It only works from their own <b>Image to 3D for Chromebook</b> file, with their passcode.</p></body>"""


class RemoteHandler(Handler):
    """Requests from the Chromebook file, which arrive through the Cloudflare tunnel. The same app, but every request
    needs the passcode (in an X-Key header: the file runs from file://, so cookies don't work)."""
    remote = True

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")  # the file's file:// page is another origin
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Image-To-3D, X-Key")
        self.send_header("Access-Control-Max-Age", "7200")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _allowed(self):
        path = urlparse(self.path).path
        if path == "/ping":  # the Cloudflare site checking that the PC is really there
            self._send(200, {"ok": True})
            return False
        if not path.startswith(("/api/", "/files/", "/uploads/")):
            self._send(404, NOT_HERE, "text/html; charset=utf-8")
            return False
        who = self.headers.get("Cf-Connecting-Ip") or self.client_address[0]
        now = time.time()
        tries = [t for t in wrong_codes.get(who, []) if now - t < 900]
        if len(tries) >= 8:
            self._send(429, {"error": "Too many wrong passcodes. Wait 15 minutes, then try again."})
            return False
        given = self.headers.get("X-Key") or ""
        if not cloud.same_code(given):
            if given:
                tries.append(now)
            wrong_codes[who] = tries
            self._send(401, {"error": "Type the passcode from your PC.", "passcode": "wrong" if given else "missing"})
            return False
        if self.command == "POST" and self.headers.get("X-Image-To-3D") != "1":
            self._send(403, {"error": "Not from the app's own page."})
            return False
        return True


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
    cloud.load()
    if cloud.settings.get("make") and os.environ.get("IMAGE_TO_3D_NO_CLOUD") != "1":
        set_remote(True)
    if os.environ.get("IMAGE_TO_3D_NO_BROWSER") != "1":
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if cloud.tunnel.on:
            cloud.tunnel.stop()


if __name__ == "__main__":
    main()
