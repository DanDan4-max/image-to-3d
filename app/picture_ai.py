"""Pictures from words, and fixing a highlighted part of a picture from words.

FLUX.2 [klein] 4B (Black Forest Labs, Apache 2.0) does both: it draws a picture from a prompt, and it redraws a
picture following an instruction ("make the tires whole"). It runs through stable-diffusion.cpp (MIT) on Vulkan,
like TRELLIS.2 runs through trellis.cpp. The drawn or fixed picture then goes through the normal picture-to-3D path.
"""
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from pipeline import MODELS, ROOT

EXE = ROOT / "engines" / "sdcpp" / "sd-cli.exe"
WEIGHTS = MODELS / "flux2-klein"
DIFFUSION, TEXT, VAE = "flux-2-klein-4b-Q8_0.gguf", "Qwen3-4B-Q8_0.gguf", "flux2-vae.safetensors"

# Added to every prompt so the picture suits image-to-3D: one whole object, nothing cut off, plain background.
STYLE = (". A single object, the whole object in view and centered, nothing cut off, on a plain light gray "
         "background, soft even studio lighting, high detail 3D game asset render.")
STEP = re.compile(r"\|\s*(\d+)/(\d+)")
STEPS = 4  # FLUX.2 [klein] is distilled to draw in 4 steps


UPSCALER = MODELS / "upscaler" / "RealESRGAN_x4plus.pth"  # Real-ESRGAN (Xintao Wang et al., BSD-3-Clause)
SMALL = 768  # pictures smaller than this get upscaled before 3D: TRELLIS.2 looks at about 512 px of the object


def available():
    return EXE.exists() and all((WEIGHTS / f).exists() for f in (DIFFUSION, TEXT, VAE))


def upscale_if_small(picture, dest):
    """Small pictures (like 256 px web thumbnails) make blurry, mushy models. Real-ESRGAN sharpens them 4x (about
    2 s on the GPU); pictures that are already big enough, or a missing upscaler, leave the picture as it is.
    Returns the picture to use."""
    if not (EXE.exists() and UPSCALER.exists()):
        return Path(picture)
    with Image.open(picture) as img:
        if max(img.size) >= SMALL:
            return Path(picture)
        rgba = "A" in img.getbands() or "transparency" in img.info
        src = Path(dest).with_name(Path(dest).stem + "-in.png")
        img.convert("RGBA" if rgba else "RGB").save(src)  # sd.cpp reads PNG/JPG, not WEBP
    r = subprocess.run([str(EXE), "-M", "upscale", "--upscale-model", str(UPSCALER), "-i", str(src), "-o", str(dest)],
                       cwd=str(EXE.parent), capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    src.unlink(missing_ok=True)
    if r.returncode != 0 or not Path(dest).exists():
        return Path(picture)  # upscaling is a bonus; carry on with the original
    with Image.open(dest) as up:
        if max(up.size) > 2048:  # 4x of a 700 px picture is more than the AIs use
            up.thumbnail((2048, 2048), Image.LANCZOS)
            up.save(dest)
    return Path(dest)


def _run(args, out, progress, text):
    # --type f16: this stable-diffusion.cpp build's Vulkan code turns Q8 weights into pure noise on Radeon RX 9000
    # cards (checked: CPU fine, Vulkan Q8 noise, Vulkan F16 fine). Converting to F16 while loading takes ~2 s.
    cmd = [str(EXE), "--diffusion-model", str(WEIGHTS / DIFFUSION), "--llm", str(WEIGHTS / TEXT),
           "--vae", str(WEIGHTS / VAE), "--type", "f16", "--cfg-scale", "1.0", "--steps", str(STEPS),
           "--sampling-method", "euler", "-o", str(out), *args]
    lines, drawn = [], False
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(EXE.parent),
                            creationflags=subprocess.CREATE_NO_WINDOW)
    buf = b""
    while chunk := proc.stdout.read(256):  # progress bars redraw with \r, so read raw rather than by line
        buf += chunk
        *done, buf = re.split(rb"[\r\n]", buf)
        for raw in done:
            line = raw.decode("utf-8", "replace")
            lines.append(line)
            if m := STEP.search(line):
                a, b = int(m.group(1)), int(m.group(2))
                if b == STEPS:  # the drawing itself; other bars are the models loading, or the final decode
                    drawn = True
                    progress(f"{text} (step {a} of {b})", 0.3 + 0.6 * a / b)
                elif a == b:
                    progress(*(("Finishing the picture", 0.95) if drawn else ("Loading the picture AI", 0.2)))
    proc.wait()
    if proc.returncode != 0 or not Path(out).exists():
        tail = "\n".join(l for l in lines if l.strip())[-600:]
        raise RuntimeError(f"The picture AI stopped (code {proc.returncode}):\n{tail}")
    return Path(out)


def imagine(prompt, out, seed=-1, size=1024, progress=None):
    """Draw a picture of `prompt`. seed -1 = a new random picture every time."""
    progress = progress or (lambda t, f: None)
    progress("Loading the picture AI", 0.05)
    return _run(["-p", prompt.strip().rstrip(".") + STYLE, "-W", str(size), "-H", str(size), "-s", str(seed)],
                out, progress, "Drawing the picture")


def edit(picture, mask, instruction, out, seed=-1, progress=None):
    """Redraw `picture` following `instruction`, changing only the highlighted area (white in `mask`).
    With a mask, just the area around the highlight is redrawn, at full resolution, then blended back in with a
    soft edge, so small fixes get the AI's full attention and the rest of the picture stays exactly as it was."""
    progress = progress or (lambda t, f: None)
    src = Image.open(picture)
    if "A" in src.getbands() or "transparency" in src.info:  # see-through parts on plain light gray, not black
        gray = Image.new("RGBA", src.size, (200, 200, 200, 255))
        gray.alpha_composite(src.convert("RGBA"))
        src = gray
    base = src.convert("RGB")
    W, H = base.size
    m = None
    if mask is not None:
        m = Image.open(mask).convert("L").resize((W, H), Image.BILINEAR)
        m = m.point(lambda v: 255 if v > 32 else 0)
        if not m.getbbox():
            m = None
    if m is not None:
        x0, y0, x1, y1 = m.getbbox()
        # the highlight plus a margin of context around it, square-ish, at least 40% of the picture
        side = max(x1 - x0, y1 - y0) * 1.6
        side = int(min(max(side, 0.4 * max(W, H)), max(W, H)))
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        bx0 = int(min(max(cx - side / 2, 0), max(W - side, 0)))
        by0 = int(min(max(cy - side / 2, 0), max(H - side, 0)))
        box = (bx0, by0, min(bx0 + side, W), min(by0 + side, H))
    else:
        box = (0, 0, W, H)
    crop = base.crop(box)
    cw, ch = crop.size
    scale = 1024 / max(cw, ch)
    tw, th = max(16, round(cw * scale / 16) * 16), max(16, round(ch * scale / 16) * 16)
    ref = Path(out).with_name(Path(out).stem + "-ref.png")
    crop.resize((tw, th), Image.LANCZOS).save(ref)

    progress("Loading the picture AI", 0.05)
    raw = Path(out).with_name(Path(out).stem + "-raw.png")
    try:
        _run(["-r", str(ref), "-p", instruction.strip(), "-W", str(tw), "-H", str(th), "-s", str(seed)],
             raw, progress, "Redrawing")
        drawn = Image.open(raw).convert("RGB")
        drawn.load()
    finally:
        ref.unlink(missing_ok=True)
        raw.unlink(missing_ok=True)

    if m is None:
        # the whole picture was redrawn: keep the AI's full resolution (it also sharpens small pictures)
        result = drawn
    else:
        fixed = drawn.resize((cw, ch), Image.LANCZOS)
        # grow the highlight a little and soften its edge so the fix blends in without a visible seam
        soft = m.crop(box).filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(max(3, cw // 80)))
        a = np.asarray(soft, np.float32)[..., None] / 255.0
        mixed = np.asarray(fixed, np.float32) * a + np.asarray(crop, np.float32) * (1 - a)
        result = base.copy()
        result.paste(Image.fromarray(mixed.round().astype(np.uint8)), box[:2])
    result.save(out)
    return Path(out)
