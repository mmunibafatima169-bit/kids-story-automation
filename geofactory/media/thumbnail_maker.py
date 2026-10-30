"""Generate original SVG thumbnail artwork; raster conversion requires FFmpeg."""

from __future__ import annotations

import html
import shutil
import subprocess
from pathlib import Path

from geofactory.core.config import FactoryConfig


def make_thumbnail(topic: str, short_text: str, destination: Path, config: FactoryConfig) -> Path:
    if len(short_text) > 40:
        raise ValueError("Thumbnail text must be 40 characters or fewer.")
    svg_path = destination.with_suffix(".svg")
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
<defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#10212d"/><stop offset="1" stop-color="#246f73"/></linearGradient></defs>
<rect width="1280" height="720" fill="url(#bg)"/>
<circle cx="1000" cy="270" r="220" fill="#e1ad4b" opacity=".92"/>
<path d="M770 425 Q1000 80 1210 425 Q1030 535 770 425" fill="#2f5660"/>
<text x="76" y="130" fill="#b9e2dd" font-family="Arial,sans-serif" font-size="26">GEOGRAPHY DOCUMENTARY</text>
<text x="76" y="330" fill="white" font-family="Arial,sans-serif" font-size="68" font-weight="700">{html.escape(short_text)}</text>
<text x="76" y="625" fill="#d6e5e3" font-family="Arial,sans-serif" font-size="25">{html.escape(topic[:70])}</text>
</svg>"""
    svg_path.write_text(svg, encoding="utf-8")
    executable = shutil.which(config.ffmpeg)
    if not executable:
        return svg_path
    png_path = destination.with_suffix(".png")
    result = subprocess.run(
        [executable, "-y", "-v", "error", "-i", str(svg_path), str(png_path)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"FFmpeg thumbnail conversion failed: {result.stderr[-1000:]}")
    return png_path
