"""Finding pictures on the web, and downloading one the user picked.

Search runs on Wikimedia Commons (and Openverse when it answers): both have free official APIs, and every picture
comes with its license and creator, which go into the model's license.txt. Google and Bing don't offer free image
search to apps (Bing's API was retired in 2025, Google's is closed to new users), so the page links to them in the
browser instead, and pictures dragged or pasted from there come in through fetch_image().
"""
import html
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request

AGENT = "image-to-3d/1.0 (https://github.com/DanDan4-max/image-to-3d)"
MAX_BYTES = 30 * 2**20


def _get_json(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _text(value):
    """Plain text from Commons' HTML-ish metadata."""
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def _author(value):
    """Commons' author field, minus its boilerplate ("No machine-readable author provided. X assumed (...)")."""
    t = _text(value)
    t = re.sub(r"^No machine-readable author provided\.\s*", "", t)
    t = re.sub(r"\s*assumed \(based on.*$", "", t)
    return re.sub(r"~\w+$", "", t).strip()


def commons(query, limit=40):
    params = {
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": limit,
        "gsrsearch": f"filetype:bitmap {query}", "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime",
        "iiurlwidth": 330, "iiextmetadatafilter": "LicenseShortName|Artist|ObjectName",
    }
    data = _get_json("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params), 15)
    pages = sorted((data.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            continue
        meta = info.get("extmetadata", {})
        title = _text(meta.get("ObjectName", {}).get("value")) or re.sub(r"^File:|\.\w+$", "", p.get("title", ""))
        full = info.get("url")
        if info.get("width", 0) > 1280 and "/330px-" in (info.get("thumburl") or ""):
            full = info["thumburl"].replace("/330px-", "/1280px-")  # big enough for 3D, much smaller download
        out.append({
            "title": title[:120], "thumb": info.get("thumburl"), "url": full, "page": info.get("descriptionurl"),
            "creator": _author(meta.get("Artist", {}).get("value"))[:80], "source": "Wikimedia Commons",
            "license": _text(meta.get("LicenseShortName", {}).get("value")) or "see page",
            "width": info.get("width"), "height": info.get("height"),
        })
    return out


def openverse(query, limit=30):
    params = {"q": query, "page_size": limit}
    data = _get_json("https://api.openverse.org/v1/images/?" + urllib.parse.urlencode(params), 8)
    out = []
    for r in data.get("results", []):
        lic = (r.get("license") or "").upper()
        if r.get("license_version"):
            lic = f"CC {lic} {r['license_version']}" if lic not in ("CC0", "PDM") else lic
        out.append({
            "title": (r.get("title") or "")[:120], "thumb": r.get("thumbnail") or r.get("url"), "url": r.get("url"),
            "page": r.get("foreign_landing_url"), "creator": (r.get("creator") or "")[:80], "source": "Openverse",
            "license": lic or "see page", "width": r.get("width"), "height": r.get("height"),
        })
    return out


def search(query, source="commons"):
    query = query.strip()[:200]
    if not query:
        return []
    return openverse(query) if source == "openverse" else commons(query)


def _public_host(host):
    """True if every address the host name points to is out on the internet (not this PC or the home network)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return bool(infos)


class _PublicRedirectsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _public_host(urllib.parse.urlparse(newurl).hostname or ""):
            raise ValueError("That address isn't on the public internet.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_image(url):
    """Download a picture from a web address. Returns (bytes, content type). Only public http(s) addresses, only
    actual pictures, at most 30 MB."""
    parts = urllib.parse.urlparse(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("That isn't a web address.")
    if not _public_host(parts.hostname):
        raise ValueError("That address isn't on the public internet.")
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "image/*"})
    with urllib.request.build_opener(_PublicRedirectsOnly).open(req, timeout=20) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("That picture is too big (30 MB max).")
    import io
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
    except Exception:
        raise ValueError("That link isn't a picture. Open the picture itself and copy its address, or drag the "
                         "picture in.")
    kind = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif",
            "BMP": "image/bmp"}.get(img.format)
    if not kind:
        raise ValueError("That picture type isn't supported. Use a JPG, PNG or WEBP picture.")
    return data, kind
