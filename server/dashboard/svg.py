"""A single hand-written inline-SVG sparkline — deliberately no charting library, so the
dashboard has zero non-Python dependencies and stays fully openable offline (see CLAUDE.md
non-goals: no CDN-hosted JS)."""

from __future__ import annotations


def sparkline_svg(
    values: list[float], width: int = 160, height: int = 36, stroke: str = "#4f8cff"
) -> str:
    if not values:
        return ""
    if len(values) == 1:
        values = values * 2

    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(values)
    step = width / (n - 1)
    pad = 3

    points = []
    for i, v in enumerate(values):
        x = i * step
        y = pad + (1 - (v - lo) / span) * (height - 2 * pad)
        points.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(points)

    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="metric trend">'
        f'<polyline points="{polyline}" fill="none" stroke="{stroke}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )
