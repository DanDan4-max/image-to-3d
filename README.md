# Image to 3D

Turn a picture into a colored 3D model (`.glb`) on your own PC. It's free, has no limits, and works offline once it's set up.

## How to use it

1. Double-click **`Start Image to 3D.bat`**. A page opens in your browser. It only runs on your PC; nothing is uploaded anywhere.
2. Drop a picture onto the page (or click to pick one, or paste with Ctrl+V).
3. Pick the AI model and file size, then click **Make 3D model**.
4. When it's done, the model spins in the preview. Click **Download .glb** or **Open folder**. Every model is also saved at `output\<name>\model.glb`.

Keep the black window open while you use the page. Close it to quit.

Prefer no browser? Drag pictures onto **`Make 3D.bat`**, or double-click it to pick pictures.

### AI model choices

| Choice | AI | Time on an RX 9070 XT | Quality |
|---|---|---|---|
| **Best** | TRELLIS.2 at full detail | about 5 minutes | Sharp shape, real textures |
| **Faster** | TRELLIS.2 at half detail | 1½ to 3 minutes | Good, a bit softer |

Detailed objects take longer, because TRELLIS.2 spends about half its time cleaning up the mesh on the CPU.
| **Quick draft** | TripoSR | about 10 seconds | Rough and blobby; only for a quick idea |

### File size

- **Game-ready**: about 20,000 triangles and a 1024 px texture. Usually under 2 MB, so it's fine to embed in the game.
- **Full detail**: TRELLIS.2's own settings (up to 300,000 triangles, 2048 px texture). Much bigger files.

### Tips for good results

- Use one object, seen from the front or slightly above, with nothing covering it.
- A plain background helps, but the background is removed automatically.
- The AI has to guess the back of the object, so a 3/4 view shows it more than a flat side view.
- Don't like a result? Run it again. Each run is free.

## Putting a model in the Rocket League game

1. Copy the model's folder into the game as `assets\hq\explosions\<name>\`, so the file ends up at `assets/hq/explosions/<name>/model.glb`. Include its `license.txt`.
2. Ask the game's Claude session to hook up the new explosion model. The game only loads models it's told about.

Every model is saved standing on the ground (y = 0), centered, and 1 unit at its biggest side.

## Can we train the AI to get better?

Not on one PC. These models learned from hundreds of thousands of 3D objects on large server farms, and a home GPU can't add enough training to make a real difference. AMD also warns that training on Windows can reset RX 9070 cards. Better results come from better models, which is why this tool switched from TripoSR to TRELLIS.2. When a stronger open model comes out, it can be added here the same way.

## Which AI models this uses, and their licenses

Training an image-to-3D AI from scratch isn't something one PC can do, so this is your own open-source app built around existing open models:

- **TRELLIS.2** (Microsoft, **MIT license**), run through **trellis.cpp** (pwilkin, MIT), a C++ port that runs on AMD cards through Vulkan with no CUDA needed. Weights: the Q8 GGUF files from `ilintar/trellis2-gguf`. TRELLIS.2 reads pictures with Meta's **DINOv3** image encoder, which has its own DINOv3 License (free to use, including commercially, under Meta's terms), and removes backgrounds with **BiRefNet** (MIT).
- **TripoSR** (Tripo AI and Stability AI, **MIT license**) for quick drafts, run with AMD's PyTorch for Windows (ROCm 7.2.1). Background removal uses **u2net** through `rembg` (MIT and Apache-2.0).
- The 3D preview uses **three.js** (MIT).

Models I checked and left out: Hunyuan3D (Tencent license with region limits) and Stable Fast 3D (Stability Community License, not MIT).

The tool's own code is MIT licensed (see `LICENSE`). The models you make are yours, but you still need the right to use the pictures you put in.

## Setup

1. Get the code from https://github.com/DanDan4-max/image-to-3d: click **Code → Download ZIP** and unzip it, or run `git clone https://github.com/DanDan4-max/image-to-3d.git`.
2. Double-click **`Setup.bat`**. It needs Windows, an AMD Radeon card, Python 3.12, an AMD graphics driver 26.2.2 or newer, and about 25 GB of free disk space. It downloads about 15 GB.

Keep this folder outside OneDrive, because the model files are big.

What's where:
- `app\`: the tool's own code (the page, the server, and the two AI engines).
- `engines\trellis\`: trellis.cpp (TRELLIS.2 for Vulkan).
- `vendor\TripoSR\`: TripoSR's code.
- `models\`: the AI weights (about 12 GB).
- `output\`: your models.
