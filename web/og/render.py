"""Render the link-preview image and video from og.html into web/public.

    uv run --with playwright python web/og/render.py   (needs ffmpeg and a Chromium browser)

og.html draws the live-call animation at any time t via render(t); the still is t=3.5
and the video is an 8 s loop captured frame by frame, so timing is exact.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
PUBLIC = HERE.parent / "public"
BROWSER = os.environ.get("CHROMIUM", "/Applications/Helium.app/Contents/MacOS/Helium")

with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
    b = p.chromium.launch(executable_path=BROWSER if Path(BROWSER).exists() else None)
    pg = b.new_page(viewport={"width": 1200, "height": 630})
    pg.goto((HERE / "og.html").as_uri())
    pg.wait_for_load_state("networkidle")
    pg.evaluate("document.fonts.ready")
    pg.evaluate("render(3.5)")
    pg.screenshot(path=str(PUBLIC / "og.png"))
    for f in range(240):
        pg.evaluate(f"render({f / 30})")
        pg.screenshot(path=f"{tmp}/f{f:04d}.png")
    b.close()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "30", "-i", f"{tmp}/f%04d.png",
                    "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-crf", "25", "-preset", "slow",
                    "-movflags", "+faststart", "-an", str(PUBLIC / "og.mp4")], check=True)
print("wrote", PUBLIC / "og.png", "and", PUBLIC / "og.mp4", file=sys.stderr)
