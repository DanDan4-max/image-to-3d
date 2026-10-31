"""Make 3D models from pictures without the app window. With no pictures given, a file picker opens.

    make3d.py [pictures...] [--engine best|faster|draft] [--size game|full] [--keep-background]
"""
import argparse
import os

import pipeline
import trellis_engine


def main():
    ap = argparse.ArgumentParser(description="Picture in, colored 3D model (.glb) out.")
    ap.add_argument("images", nargs="*")
    ap.add_argument("--engine", choices=["best", "faster", "draft"], default="best",
                    help="best/faster = TRELLIS.2 at 1024/512, draft = TripoSR (seconds, rough)")
    ap.add_argument("--size", choices=["game", "full"], default="game")
    ap.add_argument("--keep-background", action="store_true")
    args = ap.parse_args()
    images = args.images
    if not images:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        images = filedialog.askopenfilenames(title="Pick pictures to turn into 3D models",
                                             filetypes=[("Pictures", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])
        root.destroy()
    if not images:
        return
    engine = args.engine
    if engine != "draft" and not trellis_engine.available():
        print("TRELLIS.2 isn't installed, so this makes a TripoSR draft instead. Run Setup.bat to add it.\n")
        engine = "draft"
    draft = pipeline.ImageTo3D() if engine == "draft" else None
    last = None
    for img in images:
        print(f"{img}")
        try:
            common = dict(remove_bg=not args.keep_background, progress=lambda text, frac: print(f"  {text}", flush=True))
            if draft:
                r = draft.generate(img, quality=args.size, **common)
            else:
                r = trellis_engine.generate(img, res=1024 if engine == "best" else 512, size=args.size, **common)
            print(f"Saved {r.glb}  ({r.faces:,} triangles, {r.size_bytes / 1e6:.1f} MB)\n")
            last = r.folder
        except Exception as e:
            print(f"Couldn't make {img}: {e}\n")
    if last:
        os.startfile(last)


if __name__ == "__main__":
    main()
