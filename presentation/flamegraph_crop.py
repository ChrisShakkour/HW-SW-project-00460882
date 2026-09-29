"""Redraw the bottom (widest) levels of a FlameGraph SVG as a slide-sized PNG.

flamegraph.pl truncates frame labels to fit at its 1200px width, and the full
SVG is ~2134px tall with almost all of the meaningful width in the bottom few
rows. This reads the exact frame geometry (<title> + <rect>) from the SVG and
redraws only the bottom `levels` rows on a white background, with full labels
wherever they fit at the output size. Colours, x-positions and widths are the
ones in the SVG, so the picture is the same data, just cropped and legible.
"""
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

FRAME_RE = re.compile(
    r'<title>(?P<title>[^<]*)</title><rect x="(?P<x>[\d.]+)" y="(?P<y>[\d.]+)" '
    r'width="(?P<w>[\d.]+)" height="(?P<h>[\d.]+)" fill="rgb\((?P<rgb>[\d,]+)\)"'
)
TITLE_RE = re.compile(r"^(?P<name>.*) \((?P<samples>[\d,]+) samples, (?P<pct>[\d.]+)%\)$")


def _unescape(s):
    return (s.replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&amp;", "&"))


def parse_frames(svg_path):
    text = open(svg_path, encoding="utf-8").read()
    frames = []
    for m in FRAME_RE.finditer(text):
        t = TITLE_RE.match(_unescape(m["title"]))
        if not t:
            continue
        frames.append({
            "name": t["name"],
            "pct": float(t["pct"]),
            "x": float(m["x"]),
            "y": float(m["y"]),
            "w": float(m["w"]),
            "rgb": tuple(int(c) / 255 for c in m["rgb"].split(",")),
        })
    return frames


def render_base(svg_path, out_png, levels=9, width_in=7.6, row_in=0.34,
                svg_width=1200.0, xpad=10.0, font="Segoe UI", fontsize=9.5,
                min_label_px=14):
    frames = parse_frames(svg_path)
    rows = sorted({f["y"] for f in frames}, reverse=True)  # bottom row first
    keep = {y: i for i, y in enumerate(rows[:levels])}

    height_in = row_in * levels
    fig = plt.figure(figsize=(width_in, height_in), dpi=220)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(xpad, svg_width - xpad)
    ax.set_ylim(0, levels)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    # data units -> points, for fitting label text inside a frame
    pts_per_unit = width_in * 72 / (svg_width - 2 * xpad)
    char_pts = fontsize * 0.56

    for f in frames:
        if f["y"] not in keep:
            continue
        level = keep[f["y"]]
        ax.add_patch(FancyBboxPatch(
            (f["x"], level + 0.06), f["w"], 0.88,
            boxstyle="round,pad=0,rounding_size=0.6",
            linewidth=0.3, edgecolor="white", facecolor=f["rgb"]))
        box_pts = f["w"] * pts_per_unit
        if box_pts < min_label_px:
            continue
        max_chars = int((box_pts - 4) / char_pts)
        name = f["name"]
        if max_chars < 3:
            continue
        label = name if len(name) <= max_chars else name[: max_chars - 2] + ".."
        ax.text(f["x"] + 2 / pts_per_unit * 2, level + 0.5, label,
                va="center", ha="left", fontsize=fontsize,
                fontname=font, color="#1a1a1a", clip_on=True)

    fig.savefig(out_png, dpi=220, facecolor="white")
    plt.close(fig)
    return out_png


def top_self_frames(svg_path, n=8):
    """Approximate self% per function name: frame width minus its children's width."""
    frames = parse_frames(svg_path)
    rows = sorted({f["y"] for f in frames})
    by_row = {}
    for f in frames:
        by_row.setdefault(f["y"], []).append(f)
    step = rows[1] - rows[0] if len(rows) > 1 else 16
    self_pct = {}
    for f in frames:
        above = by_row.get(f["y"] - step, [])
        child = sum(c["pct"] for c in above
                    if c["x"] >= f["x"] - 0.05 and c["x"] + c["w"] <= f["x"] + f["w"] + 0.05)
        self_pct[f["name"]] = self_pct.get(f["name"], 0) + max(f["pct"] - child, 0)
    return sorted(self_pct.items(), key=lambda kv: -kv[1])[:n]


if __name__ == "__main__":
    import sys
    render_base(sys.argv[1], sys.argv[2])
    for name, pct in top_self_frames(sys.argv[1]):
        print(f"{pct:6.2f}%  {name}")
