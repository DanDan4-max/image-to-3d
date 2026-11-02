"""Your models on Cloudflare, and making new ones from the Chromebook file through Cloudflare.

- The site: a free Cloudflare Worker (https://image-to-3d.<you>.workers.dev) that holds the models, so the Chromebook
  file can pull them any time, even with this PC off. `wrangler`, Cloudflare's own tool, puts it up.
- The link to this PC: `cloudflared` opens a free Cloudflare quick tunnel to the app's Chromebook door (port 7891, on
  this PC only, see server.RemoteHandler). Its address changes every time, so the PC tells the Worker where it is now,
  and the Chromebook file asks the Worker. Everything through it needs the passcode, so nobody else can use the PC.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from PIL import Image

import pipeline

ROOT = Path(__file__).resolve().parent.parent
WRANGLER = ROOT / "engines" / "wrangler" / "node_modules" / "wrangler" / "bin" / "wrangler.js"
CLOUDFLARED = ROOT / "engines" / "cloudflared" / "cloudflared.exe"
WORKER = Path(__file__).resolve().parent / "web" / "worker.js"
SITE = pipeline.OUTPUT / ".site"  # exactly what goes up to Cloudflare
CONFIG = pipeline.OUTPUT / ".cloudflare.json"  # the site's address and the secrets; never uploaded
NAME = "image-to-3d"
MAX_FILE = 25 * 2**20  # Cloudflare's limit for one file
UPLOAD = ("model.glb", "license.txt")
LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i: easy to type on the Chromebook

settings = {}
state = {"busy": None, "error": None, "tunnel": None, "tunnel_error": None, "login_url": None}
lock = threading.Lock()  # one wrangler at a time


def load():
    try:
        settings.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    settings.setdefault("auto", True)
    settings.setdefault("skip", [])


def save():
    CONFIG.write_text(json.dumps(settings, indent=1), encoding="utf-8")


def passcode():
    if not settings.get("passcode"):
        settings["passcode"] = "-".join("".join(secrets.choice(LETTERS) for _ in range(4)) for _ in range(3))
        save()
    return settings["passcode"]


def same_code(given):
    """Whether a typed passcode is the right one (any case, spaces and dashes ignored)."""
    import hmac
    clean = lambda s: re.sub(r"[^a-z0-9]", "", s.lower()).encode()
    return bool(given) and hmac.compare_digest(clean(given), clean(passcode()))


def installed():
    return WRANGLER.exists() and CLOUDFLARED.exists() and shutil.which("node") is not None


def info():
    return {"installed": installed(), "site": settings.get("site"), "auto": settings["auto"],
            "skip": settings["skip"], "make": bool(settings.get("make")), "passcode": passcode(),
            "online": settings.get("online", []), "busy": state["busy"], "error": state["error"], "login_url": state["login_url"],
            "tunnel": state["tunnel"], "tunnel_error": state["tunnel_error"]}


# ---------- wrangler: putting the site up ----------
def wrangler(*args, stdin=None, timeout=900):
    env = {**os.environ, "WRANGLER_SEND_METRICS": "false", "NO_COLOR": "1", "FORCE_COLOR": "0"}
    env["CI"] = "1"  # never sit waiting at a question nobody can see
    SITE.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["node", str(WRANGLER), *args], cwd=SITE, input=stdin, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _plain(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)  # wrangler colors its messages even when asked not to


def login():
    """Log in to Cloudflare: open its page in the browser (and show the link too), and wait for Allow.
    wrangler only waits 2 minutes for that click."""
    env = {**os.environ, "WRANGLER_SEND_METRICS": "false", "NO_COLOR": "1"}
    proc = subprocess.Popen(["node", str(WRANGLER), "login", "--browser=false"], cwd=SITE, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace")
    out = []
    for line in proc.stdout:
        out.append(line)
        found = re.search(r"https://dash\.cloudflare\.com/oauth2/auth\S+", _plain(line))
        if found and not state.get("login_url"):
            state["login_url"] = found.group(0)
            state["busy"] = ("A Cloudflare page opened in your browser: log in there and click Allow "
                             "(within 2 minutes). No page? Use the link below.")
            webbrowser.open(state["login_url"])
    proc.wait()
    state["login_url"] = None
    text = _plain("".join(out))
    if proc.returncode or "successfully" not in text.lower():
        if "timed out" in text.lower():
            raise RuntimeError("Cloudflare's page wasn't allowed within 2 minutes. Click Try again, then log in "
                               "and click Allow in the page that opens.")
        _fail(text, "Couldn't log in to Cloudflare")


def _fail(out, what):
    out = _plain(out)
    lines = [l.strip() for l in out.splitlines() if l.strip() and not set(l.strip()) <= set("-─═▲✘ ")]
    hint = next((l for l in lines if "ERROR" in l or "rror" in l), lines[-1] if lines else "no message")
    raise RuntimeError(f"{what}: {hint[:300]}")


def setup(models):
    """First time: log in to Cloudflare (a page opens; log in and click Allow), make the site, put the models up."""
    with lock:
        state.update(busy="Checking your Cloudflare login…", error=None)
        try:
            code, out = wrangler("whoami", timeout=120)
            if code or "not authenticated" in out.lower():
                state["busy"] = "Opening Cloudflare's login page…"
                login()
            state["busy"] = "Making the place that remembers where your PC is…"
            if not settings.get("kv"):
                settings["kv"] = _kv_namespace()
            settings.setdefault("pc_secret", secrets.token_urlsafe(32))
            save()
            _publish(models)
            state["busy"] = "Locking the site so only this PC can change it…"
            code, out = wrangler("secret", "put", "PC_SECRET", stdin=settings["pc_secret"], timeout=300)
            if code:
                _fail(out, "Couldn't save the site's secret")
            settings["secret_set"] = True
            save()
        except Exception as e:
            state["error"] = str(e)
        finally:
            state["busy"] = None
    if settings.get("make") and settings.get("site"):
        tunnel.tell_site()


def _kv_namespace():
    title = f"{NAME}-pc"
    code, out = wrangler("kv", "namespace", "list", timeout=120)
    if not code:
        try:
            for ns in json.loads(out[out.index("["):out.rindex("]") + 1]):
                if ns.get("title") == title:
                    return ns["id"]
        except ValueError:
            pass
    code, out = wrangler("kv", "namespace", "create", title, timeout=120)
    found = re.search(r"\b[0-9a-f]{32}\b", out)
    if code or not found:
        _fail(out, "Couldn't make the site's storage")
    return found.group(0)


def _thumb(src, dest):
    img = Image.open(src).convert("RGBA")
    if img.getbbox():
        img = img.crop(img.getbbox())
    img.thumbnail((256, 256), Image.LANCZOS)
    img.save(dest, format="WEBP", quality=82)


def build_site(models):
    """Fill output/.site/public with what goes online; returns the models that made it in."""
    import chromebook
    public = SITE / "public"
    shutil.rmtree(public, ignore_errors=True)
    (public / "m").mkdir(parents=True)
    online = []
    for m in models:
        folder = pipeline.OUTPUT / m["folder"]
        if m["folder"] in settings["skip"] or (folder / "model.glb").stat().st_size > MAX_FILE:
            continue
        dest = public / "m" / m["folder"]
        dest.mkdir()
        for f in UPLOAD:
            if (folder / f).exists():
                shutil.copyfile(folder / f, dest / f)
        if (folder / "cutout.png").exists():
            _thumb(folder / "cutout.png", dest / "thumb.webp")
        online.append({k: m.get(k) for k in ("folder", "size", "faces", "engine_label", "time", "credit", "mirrored")})
    (public / "models.json").write_text(json.dumps(online), encoding="utf-8")
    (public / "index.html").write_text(chromebook.build(settings.get("site") or ""), encoding="utf-8")
    return online


def _publish(models):
    import chromebook
    state["busy"] = "Getting the models ready to go online…"
    online = build_site(models)
    public = SITE / "public"
    shutil.copyfile(WORKER, SITE / "worker.js")
    (SITE / "wrangler.json").write_text(json.dumps({
        "name": NAME, "main": "worker.js", "compatibility_date": "2026-10-01", "workers_dev": True,
        "assets": {"directory": "public", "binding": "ASSETS", "run_worker_first": True},
        "kv_namespaces": [{"binding": "PC", "id": settings["kv"]}],
    }, indent=1), encoding="utf-8")
    state["busy"] = f"Putting {len(online)} model{'s' if len(online) != 1 else ''} online…"
    code, out = wrangler("deploy", timeout=1800)
    site = re.search(r"https://[\w.-]+\.workers\.dev", out)
    if code or not site:
        if "workers.dev subdomain" in out:
            raise RuntimeError("Cloudflare needs one more click first: open dash.cloudflare.com, click "
                               "Workers & Pages once (it picks your free workers.dev address), then try again.")
        _fail(out, "Couldn't put the site up")
    first = not settings.get("site")
    settings.update(site=site.group(0), online=[m["folder"] for m in online], published=time.time())
    save()
    if first:  # the file inside the site needs the site's own address, which is only known now
        (public / "index.html").write_text(chromebook.build(settings["site"]), encoding="utf-8")
        code, out = wrangler("deploy", timeout=900)
        if code:
            _fail(out, "Couldn't put the site up")


def publish(models):
    with lock:
        state.update(busy="Updating your Cloudflare site…", error=None)
        try:
            _publish(models)
        except Exception as e:
            state["error"] = str(e)
        finally:
            state["busy"] = None


_pending = threading.Event()


def publish_later(get_models):
    """Put new models online in the background, a few seconds after the last change."""
    if not settings.get("site") or not settings["auto"]:
        return
    first = not _pending.is_set()
    _pending.set()
    if first:
        def run():
            time.sleep(5)
            _pending.clear()
            publish(get_models())
        threading.Thread(target=run, daemon=True).start()


# ---------- cloudflared: the link from Cloudflare to this PC ----------
class Tunnel:
    def __init__(self):
        self.port = None
        self.proc = None
        self.on = False

    def start(self, port):
        if self.on:
            return
        self.on, self.port = True, port
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.on = False
        if self.proc:
            self.proc.terminate()
        state["tunnel"] = None
        self.tell_site()

    def _run(self):
        while self.on:
            state["tunnel_error"] = None
            try:
                # shares the app's console window, so closing that window closes the tunnel too
                self.proc = subprocess.Popen([str(CLOUDFLARED), "tunnel", "--no-autoupdate", "--url",
                                              f"http://127.0.0.1:{self.port}"], stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            except OSError as e:
                state["tunnel_error"] = f"Couldn't start cloudflared: {e}"
                return
            for line in self.proc.stdout:
                found = re.search(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com", line)
                if found and not state["tunnel"]:
                    state["tunnel"] = found.group(0)
                    print(f"  The Chromebook file can reach this PC now (through {state['tunnel']}).", flush=True)
                    self.tell_site()
            self.proc.wait()
            state["tunnel"] = None
            if self.on:
                state["tunnel_error"] = "The link to Cloudflare dropped; trying again…"
                time.sleep(10)

    def tell_site(self):
        """Tell the Cloudflare site where this PC is now (or that it's gone), so the Chromebook file can find it."""
        if not settings.get("site") or not settings.get("secret_set"):
            return
        url = state["tunnel"] if self.on else None

        def send():
            for wait in (0, 5, 20, 60):
                time.sleep(wait)
                try:
                    req = urllib.request.Request(settings["site"] + "/pc", data=json.dumps({"url": url}).encode(),
                                                 method="POST", headers={"Content-Type": "application/json",
                                                                         "Authorization": "Bearer " + settings["pc_secret"],
                                                                         "User-Agent": "image-to-3d"})
                    with urllib.request.urlopen(req, timeout=20):
                        return
                except OSError:
                    continue
        threading.Thread(target=send, daemon=True).start()


tunnel = Tunnel()
