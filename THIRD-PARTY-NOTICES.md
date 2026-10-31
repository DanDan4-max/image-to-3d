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
| TripoSR (quick-draft AI model and code) | Tripo AI and Stability AI | MIT | `vendor/TripoSR/`, `models/TripoSR/` |
| DINO ViT-B/16 config (used by TripoSR) | Meta | Apache-2.0 | `models/hf-cache/` |
| u2net (background removal for TripoSR), via rembg | Xuebin Qin et al.; Daniel Gatis | Apache-2.0; MIT | `models/u2net/` |
| PyTorch for Windows with ROCm | PyTorch Foundation; AMD | BSD-3-Clause; AMD's ROCm licenses | `.venv/` |
| three.js (3D preview) | three.js authors | MIT | `app/web/vendor/three/` |
| trimesh, xatlas, moderngl, scikit-image, SciPy, Pillow and others | their authors | MIT / BSD / similar | `.venv/` |

`_rasterize_positions` in `app/pipeline.py` is adapted from TripoSR's `tsr/bake_texture.py` (MIT, Tripo AI and
Stability AI).
