"""Original, labelled SVG charts that avoid unverified geography artwork."""

from __future__ import annotations

import html
from pathlib import Path


def write_bar_chart(
    title: str,
    labels: list[str],
    values: list[float],
    unit: str,
    source_label: str,
    destination: Path,
) -> None:
    if not labels or len(labels) != len(values):
        raise ValueError("Chart labels and values must be non-empty and have equal length.")
    if not source_label.strip():
        raise ValueError("Charts need a visible evidence/source label.")
    if any(value < 0 for value in values):
        raise ValueError("Bar chart values cannot be negative.")
    width, height, margin = 960, 540, 80
    max_value = max(values) or 1
    plot_width = width - 2 * margin
    plot_height = height - 2 * margin
    bar_width = plot_width / len(values) * 0.58
    bars = []
    for index, (label, value) in enumerate(zip(labels, values, strict=True)):
        x = margin + (index + 0.21) * (plot_width / len(values))
        bar_height = value / max_value * (plot_height - 45)
        y = height - margin - bar_height
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
            f'height="{bar_height:.1f}" rx="7" fill="#24a19c"/>'
            f'<text x="{x + bar_width / 2:.1f}" y="{height - margin + 28}" '
            f'text-anchor="middle" class="label">{html.escape(label)}</text>'
            f'<text x="{x + bar_width / 2:.1f}" y="{y - 10:.1f}" '
            f'text-anchor="middle" class="value">{value:g} {html.escape(unit)}</text>'
        )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#10212d"/>
<style>text{{font-family:Arial,sans-serif;fill:#fff}}.title{{font-size:30px;font-weight:700}}.label{{font-size:18px}}.value{{font-size:15px;fill:#b8d9d4}}.source{{font-size:14px;fill:#c8d3dc}}</style>
<text x="{margin}" y="48" class="title">{html.escape(title)}</text>
<line x1="{margin}" y1="{height - margin}" x2="{width - margin}" y2="{height - margin}" stroke="#91a5b4"/>
{''.join(bars)}
<text x="{margin}" y="{height - 20}" class="source">Illustrative chart — {html.escape(source_label)}</text>
</svg>"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(svg, encoding="utf-8")
