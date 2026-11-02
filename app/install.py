"""One-time setup, run by Setup.bat: installs AMD's PyTorch and the helper libraries into .venv, then downloads
the AI models into models/. Safe to run again: anything already there is skipped, and half-finished downloads
start over.
"""
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
PY = sys.executable

ROCM = "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1"
TORCH = [f"{ROCM}/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl", f"{ROCM}/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl",
         f"{ROCM}/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl", f"{ROCM}/rocm-7.2.1.tar.gz"]
TORCH2 = [f"{ROCM}/torch-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl",
          f"{ROCM}/torchvision-0.24.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl"]
TRIPOSR_CODE = "https://github.com/VAST-AI-Research/TripoSR/archive/107cefdc244c39106fa830359024f6a2f1c78871.zip"
TRELLIS_EXE = "https://github.com/pwilkin/trellis.cpp/releases/download/v0.8.1/trellis-vulkan-windows-x64.zip"
SDCPP_EXE = ("https://github.com/leejet/stable-diffusion.cpp/releases/download/master-1030-0a06712/"
             "sd-master-0a06712-bin-win-vulkan-x64.zip")
TRELLIS_Q8 = "https://huggingface.co/ilintar/trellis2-gguf/resolve/main/q8/{}.gguf"
DOWNLOADS = [  # (url, where it goes)
    ("https://huggingface.co/stabilityai/TripoSR/resolve/main/config.yaml", MODELS / "TripoSR" / "config.yaml"),
    ("https://huggingface.co/stabilityai/TripoSR/resolve/main/model.ckpt", MODELS / "TripoSR" / "model.ckpt"),
    ("https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx", MODELS / "u2net" / "u2net.onnx"),
    *[(TRELLIS_Q8.format(f), MODELS / "trellis2-q8" / f"{f}.gguf") for f in
      ["birefnet", "dinov3", "ss_flow", "ss_dec", "shape_flow_512", "shape_flow_1024", "shape_dec",
       "tex_flow_512", "tex_flow_1024", "tex_dec"]],
    # the picture AI (prompt mode and highlight-and-fix): FLUX.2 [klein] 4B and its text reader, Apache 2.0
    ("https://huggingface.co/leejet/FLUX.2-klein-4B-GGUF/resolve/main/flux-2-klein-4b-Q8_0.gguf",
     MODELS / "flux2-klein" / "flux-2-klein-4b-Q8_0.gguf"),
    ("https://huggingface.co/unsloth/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q8_0.gguf",
     MODELS / "flux2-klein" / "Qwen3-4B-Q8_0.gguf"),
    ("https://huggingface.co/Comfy-Org/flux2-klein-4B/resolve/main/split_files/vae/flux2-vae.safetensors",
     MODELS / "flux2-klein" / "flux2-vae.safetensors"),
    # sharpens small pictures 4x before 3D, BSD-3-Clause
    ("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
     MODELS / "upscaler" / "RealESRGAN_x4plus.pth"),
]


def say(text):
    print(f"\n==> {text}", flush=True)


def fetch(url, dest):
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url) as r, open(part, "wb") as f:
        total, done = int(r.headers.get("Content-Length") or 0), 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r    {dest.name}: {done / 1e9:.2f} of {total / 1e9:.2f} GB", end="", flush=True)
    print()
    part.replace(dest)


def pip(*args):
    subprocess.check_call([PY, "-m", "pip", "install", "--disable-pip-version-check", *args])


def main():
    print("This downloads about 24 GB in total: AMD's PyTorch (2 GB), helper libraries (0.5 GB), TripoSR (1.9 GB),\n"
          "TRELLIS.2 (10 GB) and the FLUX.2 [klein] picture AI (9 GB). Everything stays in this folder.")
    if input("Continue? [Y/n] ").strip().lower().startswith("n"):
        return

    try:
        import torch  # noqa: F401
    except ImportError:
        say("Installing AMD's PyTorch for Windows (ROCm 7.2.1, needs AMD driver 26.2.2 or newer)")
        pip("--no-cache-dir", *TORCH)
        pip("--no-cache-dir", *TORCH2)
    say("Installing helper libraries")
    pip("-r", str(ROOT / "requirements.txt"))

    code = ROOT / "vendor" / "TripoSR"
    if not (code / "tsr").exists():
        say("Getting TripoSR's code")
        zpath = ROOT / "vendor" / "triposr.zip"
        fetch(TRIPOSR_CODE, zpath)
        with zipfile.ZipFile(zpath) as z:
            top = z.namelist()[0].split("/")[0]
            z.extractall(ROOT / "vendor")
        (ROOT / "vendor" / top).rename(code)
        zpath.unlink()

    exe = ROOT / "engines" / "trellis" / "trellis-cli.exe"
    if not exe.exists():
        say("Getting trellis.cpp (TRELLIS.2 for AMD/Vulkan)")
        zpath = ROOT / "engines" / "trellis-vulkan-windows-x64.zip"
        fetch(TRELLIS_EXE, zpath)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(exe.parent)
        zpath.unlink()

    sd = ROOT / "engines" / "sdcpp" / "sd-cli.exe"
    if not sd.exists():
        say("Getting stable-diffusion.cpp (the picture AI for prompt mode and fixes, Vulkan)")
        zpath = ROOT / "engines" / "sd-vulkan-windows-x64.zip"
        fetch(SDCPP_EXE, zpath)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(sd.parent)
        zpath.unlink()

    say("Downloading the AI models (resumes where it left off if you run Setup again)")
    for url, dest in DOWNLOADS:
        fetch(url, dest)

    say("Fetching a tiny config file for TripoSR's picture reader")
    import os
    os.environ["HF_HOME"] = str(MODELS / "hf-cache")
    from huggingface_hub import hf_hub_download
    hf_hub_download("facebook/dino-vitb16", "config.json")

    say("Checking the GPU")
    subprocess.call([PY, "-c", "import torch; print('PyTorch sees:', torch.cuda.get_device_name(0) "
                               "if torch.cuda.is_available() else 'no GPU (quick drafts will be slow)')"])
    say("All set. Double-click 'Start Image to 3D.bat' to use it.")


if __name__ == "__main__":
    main()
