# Image to 3D

Turn a picture, or a few words, into a colored 3D model (`.glb`) on your own PC. It's free, has no limits, and works offline once it's set up (only the web search needs the internet).

## How to use it

1. Double-click **`Start Image to 3D.bat`**. A page opens in your browser. It only runs on your PC; nothing is uploaded anywhere.
2. Drop a picture onto the page (or click to pick one, or paste with Ctrl+V).
3. Pick the AI model and file size, then click **Make 3D model**.
4. When it's done, the model spins in the preview. Click **Download .glb** or **Open folder**. Every model is also saved at `output\<name>\model.glb`.

Keep the black window open while you use the page. Close it to quit.

Prefer no browser? Drag pictures onto **`Make 3D.bat`**, or double-click it to pick pictures.

### Finding a picture on the web

Click the **From the web** tab, type what you're looking for, and press **Search**. You get free-to-use pictures from Wikimedia Commons (or Openverse), each tagged with its license. Click one to use it, and its creator and license are written into the model's `license.txt`, so you can credit them.

Can't find it there? The **Google Images** and **Bing Images** links open the same search in your browser. Drag a picture from there onto the picture box, or right-click it, choose **Copy image**, and press Ctrl+V in the app. Pasting a link to a picture works too. Pictures from Google or Bing can be anyone's, so check you're allowed to use them. (The app can't show Google or Bing results itself: neither offers free image search to apps any more.)

### Cutting out one part of a picture

If a picture has more than you want (a statue on a busy street, two dragons when you want one), click **Cut out one part** under the picture and drag a box around just the thing you want. Only that part gets used, and its background is removed after.

### Making a model from words (prompt mode)

Click the **From words** tab, describe the thing (e.g. "a glowing blue dragon head", "a golden trophy with wings"), and click **Draw it**. In about 15 seconds the picture AI draws it on a plain background, ready for 3D. Don't like it? Click **Draw again** for a new one. Then click **Make 3D model** as usual.

### Highlight and fix

- **Fix part of a picture:** under the picture, click **Highlight and fix part of this picture**. Paint over what's wrong with the brush, type what it should be ("complete black tires, no grass", "make the ball glow blue"), and click **Fix it**. About 25 seconds later only the painted part has changed. **Undo fix** goes back. Click **Use this picture** when it looks right.
- **Fix part of a 3D model:** turn the model so the problem shows, click **Highlight and fix it**, and fix the snapshot the same way. **Use this picture** then makes a new model from the fixed view.

This is how to rescue a picture where something is hidden. For example, paint over tire bottoms hidden in grass and ask for complete tires.

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

### Fixing the side the AI couldn't see

The AI only sees one side of the object, so it has to guess the other side. Sometimes it guesses wrong, for example a car with wheels on one side only. For things that are the same on both sides (cars, robots, people, trophies), click **Fix it by mirroring** under the finished model. It makes two versions, each copying one half onto the other. Spin the model around, flip between **Side A**, **Side B** and **Original**, then click **Keep this one**. Side A is usually the right one. The untouched model is kept as `original.glb`, so you can always switch back.

Mirroring flips any writing on the object, so skip it for things with text or logos.

### Tips for good results

- **Try "Clean up with AI".** Under the picture, it redraws your picture as a clean studio render of the same object: sharp, evenly lit, no reflections. Screenshots and photos have glare and blur that the 3D AI bakes into the model as blotches, so this usually gives a much cleaner model. It can change small details (in testing, a car's black roof came out silver), so look before you click Make, and use **Back to the original** if you don't like it.
- **Small pictures are sharpened automatically.** Anything under 768 pixels gets enlarged 4x with Real-ESRGAN first, since tiny web thumbnails make mushy models. The cut-out note tells you when that happened. Bigger, sharper pictures still work best.
- **Check the cut-out.** Right after you pick a picture, the page shows exactly what the AI will see, on a see-through checkerboard. Anything missing there comes out wrong in 3D. For example, in a Rocket League screenshot the grass hides the bottom of the tires, so the wheels come out mangled. Use a picture where the whole object shows: the car mid-jump, on a flat floor, or a clean render.
- Use one object, seen from the front or slightly above, with nothing covering it.
- A plain background helps, but the background is removed automatically.
- The AI has to guess the back of the object, so a 3/4 view shows it more than a flat side view.
- Don't like a result? Run it again. Each run is free.

## Taking your models to a Chromebook

The AI needs this PC's graphics card, but your models can go anywhere. Under **Your models**, click **Take models to a Chromebook**, tick the models you want, and click **Make the file**. You get one file, `output\3D models for Chromebook.html`, with a 3D viewer and the models inside.

Copy that file to the Chromebook (USB stick, Google Drive or OneDrive) and open it from the Files app. It runs straight from the file, with no internet, installs or commands. Click a model to spin it, click **Download .glb** to save it, and open **Where it came from and its license** for credits. **Open .glb files** shows any other `.glb` on the Chromebook too.

The file is about 1.3 times the size of the models in it. Game-ready models (about 1 MB each) open fastest on old Chromebooks. Full-detail ones (about 11 MB, 300,000 triangles) work too, but take a few seconds.

## Putting a model in the Rocket League game

1. Copy the model's folder into the game as `assets\hq\explosions\<name>\`, so the file ends up at `assets/hq/explosions/<name>/model.glb`. Include its `license.txt`.
2. Ask the game's Claude session to hook up the new explosion model. The game only loads models it's told about.

Every model is saved standing on the ground (y = 0), centered, and 1 unit at its biggest side.

## Can we train the AI to get better?

Not on one PC. These models learned from hundreds of thousands of 3D objects on large server farms, and a home GPU can't add enough training to make a real difference. AMD also warns that training on Windows can reset RX 9070 cards. Better results come from better models, which is why this tool switched from TripoSR to TRELLIS.2. When a stronger open model comes out, it can be added here the same way.

## Which AI models this uses, and their licenses

Training an image-to-3D AI from scratch isn't something one PC can do, so this is your own open-source app built around existing open models:

- **TRELLIS.2** (Microsoft, **MIT license**), run through **trellis.cpp** (pwilkin, MIT), a C++ port that runs on AMD cards through Vulkan with no CUDA needed. Weights: the Q8 GGUF files from `ilintar/trellis2-gguf`. TRELLIS.2 reads pictures with Meta's **DINOv3** image encoder, which has its own DINOv3 License (free to use, including commercially, under Meta's terms), and removes backgrounds with **BiRefNet** (MIT).
- **FLUX.2 [klein] 4B** (Black Forest Labs, **Apache 2.0**) draws pictures from words and redraws highlighted parts, with **Qwen3-4B** (Alibaba, Apache 2.0) reading the words. It runs through **stable-diffusion.cpp** (leejet, MIT) on Vulkan. On Radeon RX 9000 cards that build's Vulkan code turns the compressed (Q8) weights into noise, so the app has it convert them to F16 while loading.
- **TripoSR** (Tripo AI and Stability AI, **MIT license**) for quick drafts, run with AMD's PyTorch for Windows (ROCm 7.2.1). Background removal uses **u2net** through `rembg` (MIT and Apache-2.0).
- The 3D preview uses **three.js** (MIT).

Models I checked and left out: Hunyuan3D (Tencent license with region limits) and Stable Fast 3D (Stability Community License, not MIT).

The tool's own code is MIT licensed (see `LICENSE`). The models you make are yours, but you still need the right to use the pictures you put in.

## Setup

1. Get the code from https://github.com/DanDan4-max/image-to-3d: click **Code → Download ZIP** and unzip it, or run `git clone https://github.com/DanDan4-max/image-to-3d.git`.
2. Double-click **`Setup.bat`**. It needs Windows, an AMD Radeon card, Python 3.12, an AMD graphics driver 26.2.2 or newer, and about 35 GB of free disk space. It downloads about 24 GB.

Keep this folder outside OneDrive, because the model files are big.

What's where:
- `app\`: the tool's own code (the page, the server, and the two AI engines).
- `engines\trellis\`: trellis.cpp (TRELLIS.2 for Vulkan).
- `engines\sdcpp\`: stable-diffusion.cpp (the picture AI for Vulkan).
- `vendor\TripoSR\`: TripoSR's code.
- `models\`: the AI weights (about 21 GB).
- `output\`: your models.
