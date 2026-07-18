#!/usr/bin/env python3
"""Build app icons from assets/app-icon.svg (active green RJ45 front-panel jack).

Writes:
  - src-tauri/icons/* via `tauri icon` (PNG/ICNS/ICO for the .app)
  - frontend/public/favicon.svg and favicon-*.png for the web UI
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "assets" / "app-icon.svg"
ICONS = ROOT / "src-tauri" / "icons"
PUBLIC = ROOT / "frontend" / "public"
MASTER = ICONS / "icon-source.png"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)


def rasterize(size: int, dest: Path) -> None:
    """Rasterize the SVG (prefer CairoSVG for fidelity; fall back to ImageMagick)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    svg_bytes = SVG.read_bytes()

    try:
        import cairosvg  # type: ignore

        png = cairosvg.svg2png(
            bytestring=svg_bytes,
            output_width=size,
            output_height=size,
        )
        dest.write_bytes(png)
        print(f"+ cairosvg → {dest} ({size}x{size})")
        return
    except Exception as exc:
        print(f"cairosvg unavailable ({exc}); trying ImageMagick", file=sys.stderr)

    magick = shutil.which("magick") or shutil.which("convert")
    if not magick:
        raise SystemExit(
            "Need CairoSVG (pip install cairosvg) or ImageMagick (brew install imagemagick)"
        )
    density = max(144, size // 2)
    run(
        [
            magick,
            "-background",
            "none",
            "-density",
            str(density),
            str(SVG),
            "-resize",
            f"{size}x{size}",
            str(dest),
        ]
    )


def main() -> None:
    if not SVG.is_file():
        raise SystemExit(f"Missing source icon: {SVG}")

    ICONS.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)

    rasterize(1024, MASTER)

    shutil.copy2(SVG, PUBLIC / "favicon.svg")
    rasterize(32, PUBLIC / "favicon-32.png")
    rasterize(180, PUBLIC / "apple-touch-icon.png")

    tauri = ROOT / "node_modules" / ".bin" / "tauri"
    if tauri.is_file():
        run([str(tauri), "icon", str(MASTER), "-o", str(ICONS)])
    else:
        for name, size in [
            ("32x32.png", 32),
            ("128x128.png", 128),
            ("128x128@2x.png", 256),
            ("icon.png", 512),
        ]:
            rasterize(size, ICONS / name)
        print("tauri CLI not found; wrote PNG fallbacks only", file=sys.stderr)

    print(f"Icons ready in {ICONS} and {PUBLIC}")


if __name__ == "__main__":
    main()
