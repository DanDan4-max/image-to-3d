# Third-party parts

Image to 3D's own code (everything in `app/` except `app/web/vendor/`) is MIT licensed; see `LICENSE`. It uses
these parts, each under its own license. The AI models and engines are downloaded by `Setup.bat`, not stored in this
repository.

| Part | Made by | License | Where it lives |
|---|---|---|---|
| TRELLIS.2 (AI model) | Microsoft | MIT | `models/trellis2-q8/` (GGUF conversion by `ilintar/trellis2-gguf`) |
| trellis.cpp (runs TRELLIS.2 on Vulkan) | Piotr Wilkin and contributors | MIT | `engines/trellis/` |
| DINOv3 ViT-L (picture reader inside TRELLIS.2) | Meta | DINOv3 License | `models/trellis2-q8/dinov3.gguf` |
| BiRefNet (background removal for TRELLIS.2) | ZhengPeng7 | MIT | `models/trellis2-q8/birefnet.gguf` |
| FLUX.2 [klein] 4B (picture AI: prompt mode, highlight and fix) | Black Forest Labs | Apache-2.0 | `models/flux2-klein/` (GGUF by `leejet/FLUX.2-klein-4B-GGUF`, VAE from `Comfy-Org/flux2-klein-4B`) |
| Qwen3-4B (reads the prompt for FLUX.2 [klein]) | Alibaba Qwen team | Apache-2.0 | `models/flux2-klein/` (GGUF by `unsloth/Qwen3-4B-GGUF`) |
| Real-ESRGAN x4plus (sharpens small pictures before 3D) | Xintao Wang et al. | BSD-3-Clause | `models/upscaler/` |
| stable-diffusion.cpp (runs FLUX.2 [klein] and Real-ESRGAN on Vulkan) | leejet and contributors | MIT | `engines/sdcpp/` |
| TripoSR (quick-draft AI model and code) | Tripo AI and Stability AI | MIT | `vendor/TripoSR/`, `models/TripoSR/` |
| DINO ViT-B/16 config (used by TripoSR) | Meta | Apache-2.0 | `models/hf-cache/` |
| u2net (background removal for TripoSR), via rembg | Xuebin Qin et al.; Daniel Gatis | Apache-2.0; MIT | `models/u2net/` |
| PyTorch for Windows with ROCm | PyTorch Foundation; AMD | BSD-3-Clause; AMD's ROCm licenses | `.venv/` |
| three.js (3D preview, and the Chromebook file) | three.js authors | MIT | `app/web/vendor/three/` (`three-viewer.iife.js` is three.js with its glTF loader, orbit controls and room environment bundled into one script with esbuild) |
| cloudflared 2026.10.0 (the link from the Cloudflare site to this PC) | Cloudflare | Apache-2.0 | `engines/cloudflared/` |
| wrangler 4.149.0 (puts the models on the user's Cloudflare site) | Cloudflare | MIT OR Apache-2.0 | `engines/wrangler/` |
| trimesh, xatlas, moderngl, scikit-image, SciPy, Pillow and others | their authors | MIT / BSD / similar | `.venv/` |

`_rasterize_positions` in `app/pipeline.py` is adapted from TripoSR's `tsr/bake_texture.py` (MIT, Tripo AI and
Stability AI).
