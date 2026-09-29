"""Generate presentation/presentation.pptx (pyflate + nbody) with speaker notes.

Run from anywhere:  python presentation/build_presentation.py

Design rules enforced by check_deck() at the end of the build:
  * every slide title is TITLE_FONT / TITLE_SIZE
  * every body-text run is BODY_FONT / BODY_SIZE
  * every slide has speaker notes
  * no shape extends past the slide edges
Code panels (Consolas) and the footer are the only other text styles.
"""
import os
import re
import sys

import numpy as np
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt
from pygments.lexers import BashLexer, PythonLexer
from pygments.token import Token

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flamegraph_crop import render_base  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(HERE, "assets")
OUT = os.path.join(HERE, "presentation.pptx")

# ---------------------------------------------------------------- design ----
SLIDE_W, SLIDE_H = 13.333, 7.5
MARGIN = 0.6
CONTENT_W = SLIDE_W - 2 * MARGIN
CONTENT_TOP = 1.35
CONTENT_BOTTOM = 6.85

TITLE_FONT, TITLE_SIZE = "Segoe UI Semibold", 30
BODY_FONT, BODY_SIZE = "Segoe UI", 18
CODE_FONT = "Consolas"
FOOTER_SIZE = 11
CONSOLAS_EM = 0.55  # advance width of one Consolas glyph, in em

NAVY = RGBColor(0x1F, 0x3A, 0x5F)
TEAL = RGBColor(0x0F, 0x76, 0x6E)
INK = RGBColor(0x22, 0x22, 0x22)
MUTED = RGBColor(0x6B, 0x72, 0x80)
PANEL = RGBColor(0xF3, 0xF4, 0xF6)
LINE = RGBColor(0xD9, 0xDD, 0xE3)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
RED = RGBColor(0xB4, 0x23, 0x18)
AMBER = RGBColor(0xB4, 0x5A, 0x09)
GREEN = RGBColor(0x1A, 0x7F, 0x37)
HL_BAD = RGBColor(0xFD, 0xE7, 0xE4)
HL_GOOD = RGBColor(0xDC, 0xF3, 0xE3)
CODE_BG = RGBColor(0xF6, 0xF8, 0xFA)

CODE_COLORS = [  # most specific token types first
    (Token.Comment, RGBColor(0x6E, 0x77, 0x81)),
    (Token.Literal.String, RGBColor(0x0A, 0x30, 0x69)),
    (Token.Literal.Number, RGBColor(0x05, 0x50, 0xAE)),
    (Token.Keyword, RGBColor(0xCF, 0x22, 0x2E)),
    (Token.Operator.Word, RGBColor(0xCF, 0x22, 0x2E)),
    (Token.Name.Builtin, RGBColor(0x82, 0x50, 0xDF)),
    (Token.Name.Function, RGBColor(0x82, 0x50, 0xDF)),
    (Token.Name.Class, RGBColor(0x82, 0x50, 0xDF)),
]
CODE_DEFAULT = RGBColor(0x24, 0x29, 0x2F)


def src(*parts):
    return os.path.join(ROOT, *parts)


# --------------------------------------------------------------- helpers ----
def _font(run, name, size, color=INK, bold=False, italic=False):
    f = run.font
    f.name = name
    f.size = Pt(size)
    f.bold = bold
    f.italic = italic
    f.color.rgb = color
    # make East-Asian / complex-script fallbacks use the same face
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is None:
            el = rpr.makeelement(qn(tag), {})
            rpr.append(el)
        el.set("typeface", name)


def _box(slide, x, y, w, h, name=None):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    for side in ("left", "right", "top", "bottom"):
        setattr(tf, f"margin_{side}", Inches(0.05))
    return tb, tf


def _rich(paragraph, text, color=INK, bold=False, size=BODY_SIZE, font=BODY_FONT):
    """Add runs to a paragraph; **text** is bold, [[text]] is accent-coloured bold,
    {{label|url}} is a hyperlink."""
    text = text.replace(" %", "\u00a0%")  # keep "%" next to its number
    for part in re.split(r"(\*\*.+?\*\*|\[\[.+?\]\]|\{\{.+?\}\})", text):
        if not part:
            continue
        r = paragraph.add_run()
        if part.startswith("{{"):
            label, url = part[2:-2].split("|", 1)
            r.text = label
            _font(r, font, size, TEAL)
            r.font.underline = True
            r.hyperlink.address = url
        elif part.startswith("**"):
            r.text = part[2:-2]
            _font(r, font, size, color, bold=True)
        elif part.startswith("[["):
            r.text = part[2:-2]
            _font(r, font, size, TEAL, bold=True)
        else:
            r.text = part
            _font(r, font, size, color, bold=bold)


def _bullet(paragraph, level=0, char="•", color=TEAL):
    ppr = paragraph._p.get_or_add_pPr()
    indent = 0.28 + 0.3 * level
    ppr.set("marL", str(Inches(indent)))
    ppr.set("indent", str(-Inches(0.26)))
    for tag in ("a:buClr", "a:buFont", "a:buChar", "a:buNone"):
        for el in ppr.findall(qn(tag)):
            ppr.remove(el)
    clr = ppr.makeelement(qn("a:buClr"), {})
    srgb = clr.makeelement(qn("a:srgbClr"), {"val": str(color)})
    clr.append(srgb)
    ppr.append(clr)
    ppr.append(ppr.makeelement(qn("a:buFont"), {"typeface": "Arial"}))
    ppr.append(ppr.makeelement(qn("a:buChar"), {"char": char}))


def add_title(slide, text):
    tb, tf = _box(slide, MARGIN, 0.38, CONTENT_W, 0.72, name="TITLE")
    tf.vertical_anchor = MSO_ANCHOR.BOTTOM
    tf.word_wrap = False
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = text
    _font(r, TITLE_FONT, TITLE_SIZE, NAVY)
    # accent rule: short teal bar over a full-width hairline
    add_rect(slide, MARGIN, 1.14, CONTENT_W, 0.012, fill=LINE)
    add_rect(slide, MARGIN, 1.12, 1.1, 0.05, fill=TEAL)
    return tb


def add_bullets(slide, items, x, y, w, h, space_after=10, name="BODY"):
    """items: str (level 0) or (str, level). Returns the textbox."""
    tb, tf = _box(slide, x, y, w, h, name=name)
    first = True
    for it in items:
        text, level = (it, 0) if isinstance(it, str) else it
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        _bullet(p, level, char="•" if level == 0 else "–",
                color=TEAL if level == 0 else MUTED)
        p.space_after = Pt(space_after)
        p.line_spacing = 1.08
        _rich(p, text)
    return tb


def add_text(slide, text, x, y, w, h, color=INK, bold=False, align=PP_ALIGN.LEFT,
             anchor=MSO_ANCHOR.TOP, name="BODY"):
    """Body-styled plain paragraph(s); '\n' starts a new paragraph."""
    tb, tf = _box(slide, x, y, w, h, name=name)
    tf.vertical_anchor = anchor
    for i, line in enumerate(text.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = 1.05
        _rich(p, line, color=color, bold=bold)
    return tb


def add_rect(slide, x, y, w, h, fill=PANEL, line=None, radius=None, shadow=False):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    s = slide.shapes.add_shape(shape_type, Inches(x), Inches(y), Inches(w), Inches(h))
    if radius:
        s.adjustments[0] = radius
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(1)
    if not shadow:
        sppr = s._element.spPr
        sppr.append(sppr.makeelement(qn("a:effectLst"), {}))
    s.text_frame.text = ""
    return s


def add_card(slide, x, y, w, h, heading, items, accent=TEAL):
    """Light panel with a coloured left edge, a bold heading and bullets."""
    add_rect(slide, x, y, w, h, fill=PANEL, radius=0.04)
    add_rect(slide, x, y + 0.12, 0.06, h - 0.24, fill=accent)
    add_text(slide, heading, x + 0.25, y + 0.14, w - 0.4, 0.45, color=accent, bold=True)
    add_bullets(slide, items, x + 0.2, y + 0.62, w - 0.35, h - 0.72, space_after=6)


def add_key_facts(slide, diagram, facts):
    """Block diagram as large as the content area allows, key facts in a side column."""
    col_w = 2.75
    _, (px, py, pw, ph) = add_image_fit(slide, diagram, MARGIN, CONTENT_TOP + 0.05,
                                        CONTENT_W - col_w - 0.25, 5.45, border=True,
                                        align="left")
    x = px + pw + 0.25
    w = SLIDE_W - MARGIN - x
    fh = (ph - 0.3) / len(facts)
    for i, (big, small) in enumerate(facts):
        y = py + i * (fh + 0.15)
        add_rect(slide, x, y, w, fh, fill=PANEL, radius=0.05)
        add_rect(slide, x, y + 0.12, 0.06, fh - 0.24, fill=TEAL)
        tb, tf = _box(slide, x + 0.2, y + 0.12, w - 0.3, 0.6, name="STAT")
        r = tf.paragraphs[0].add_run()
        r.text = big
        _font(r, TITLE_FONT, 26, NAVY)
        add_text(slide, small, x + 0.2, y + 0.72, w - 0.3, fh - 0.8, color=INK)


def add_arrow(slide, x1, y1, x2, y2, color=MUTED, width=1.75):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1),
                                   Inches(x2), Inches(y2))
    c.line.color.rgb = color
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))
    return c


def add_footer(slide, section, number):
    if section:
        tb, tf = _box(slide, MARGIN, 7.0, 6, 0.3, name="FOOTER")
        r = tf.paragraphs[0].add_run()
        r.text = section
        _font(r, BODY_FONT, FOOTER_SIZE, MUTED)
    tb, tf = _box(slide, SLIDE_W - MARGIN - 1.0, 7.0, 1.0, 0.3, name="FOOTER")
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.RIGHT
    r = p.add_run()
    r.text = str(number)
    _font(r, BODY_FONT, FOOTER_SIZE, MUTED)


def set_notes(slide, text):
    slide.notes_slide.notes_text_frame.text = " ".join(text.split())


def add_image_fit(slide, path, x, y, w, h, border=False, align="center"):
    """Place an image as large as possible inside (x, y, w, h), aspect preserved."""
    iw, ih = Image.open(path).size
    scale = min(w / iw, h / ih)
    pw, ph = iw * scale, ih * scale
    px = x + (w - pw) / 2 if align == "center" else x
    py = y + (h - ph) / 2
    pic = slide.shapes.add_picture(path, Inches(px), Inches(py), Inches(pw), Inches(ph))
    if border:
        pic.line.color.rgb = LINE
        pic.line.width = Pt(1)
    return pic, (px, py, pw, ph)


def add_table(slide, rows, x, y, col_widths, row_h=0.46, header=True,
              aligns=None, colors=None, bold_cells=(), highlight_rows=()):
    """rows[0] is the header. colors: {(r, c): RGBColor} for per-cell text colour."""
    colors = colors or {}
    n_rows, n_cols = len(rows), len(rows[0])
    gf = slide.shapes.add_table(n_rows, n_cols, Inches(x), Inches(y),
                                Inches(sum(col_widths)), Inches(row_h * n_rows))
    gf.name = "BODY_TABLE"
    tbl = gf.table
    # drop the built-in table style so only our formatting shows
    tbl_pr = tbl._tbl.tblPr
    for attr in ("bandRow", "firstRow"):
        tbl_pr.set(attr, "0")
    style = tbl_pr.find(qn("a:tableStyleId"))
    if style is None:
        style = tbl_pr.makeelement(qn("a:tableStyleId"), {})
        tbl_pr.append(style)
    style.text = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"  # "No Style, No Grid"
    for c, cw in enumerate(col_widths):
        tbl.columns[c].width = Inches(cw)
    for r in range(n_rows):
        tbl.rows[r].height = Inches(row_h)
        for c in range(n_cols):
            cell = tbl.cell(r, c)
            cell.margin_left = cell.margin_right = Inches(0.1)
            cell.margin_top = cell.margin_bottom = Inches(0.03)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            is_head = header and r == 0
            cell.fill.solid()
            if is_head:
                cell.fill.fore_color.rgb = NAVY
            elif r in highlight_rows:
                cell.fill.fore_color.rgb = HL_GOOD
            else:
                cell.fill.fore_color.rgb = WHITE if r % 2 else PANEL
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = (aligns[c] if aligns else PP_ALIGN.LEFT)
            color = WHITE if is_head else colors.get((r, c), INK)
            _rich(p, str(rows[r][c]), color=color,
                  bold=is_head or (r, c) in bold_cells or r in highlight_rows)
    return gf


# ------------------------------------------------------------ code panel ----
def read_lines(path, start, end):
    with open(path, encoding="utf-8") as f:
        lines = f.read().replace("\r\n", "\n").split("\n")
    return lines[start - 1:end]


def snippet(path, *ranges, dedent=0):
    """Verbatim lines from `path`. ranges: (start, end) tuples; '...' marks a gap.
    Returns list of (line_no | None, text)."""
    out = []
    for rg in ranges:
        if rg == "...":
            out.append((None, "..."))
            continue
        s, e = rg
        for i, t in enumerate(read_lines(path, s, e)):
            out.append((s + i, t[dedent:] if t[:dedent].strip() == "" else t))
    return out


def code_font_size(lines, width_in, max_size=14.0, min_size=11.0, pad_in=0.32):
    longest = max(len(t) for _, t in lines)
    fit = (width_in - pad_in) * 72 / (max(longest, 1) * CONSOLAS_EM)
    size = min(max_size, fit)
    if size < min_size:
        raise ValueError(f"code too wide: {longest} chars in {width_in}in -> {size:.1f}pt")
    return round(size * 2) / 2


def _token_color(ttype):
    for base, color in CODE_COLORS:
        if ttype in base:
            return color
    return CODE_DEFAULT


def _tokenize(lines, lexer):
    """Yield per-line lists of (text, color) using pygments on the joined source."""
    real = [t if n is not None else "" for n, t in lines]
    per_line = [[]]
    for ttype, value in lexer.get_tokens("\n".join(real)):
        parts = value.split("\n")
        for k, part in enumerate(parts):
            if k:
                per_line.append([])
            if part:
                per_line[-1].append((part, _token_color(ttype)))
    return per_line[: len(lines)]


def add_code(slide, lines, x, y, w, size, header, tag=None, tag_color=RED,
             highlight=(), hl_color=HL_BAD, lexer="python", h=None):
    """Code panel: header strip (file + lines) and syntax-coloured code body.
    highlight: iterable of source line numbers to shade."""
    leading = size * 1.27
    head_h = 0.36
    body_h = len(lines) * leading / 72 + 0.22
    h = h or head_h + body_h
    add_rect(slide, x, y, w, h, fill=CODE_BG, line=LINE, radius=0.025)
    add_rect(slide, x + 0.01, y + head_h, w - 0.02, 0.01, fill=LINE)

    tb, tf = _box(slide, x + 0.12, y + 0.04, w - 0.24, head_h - 0.06, name="CODEHEAD")
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.word_wrap = False
    r = tf.paragraphs[0].add_run()
    r.text = header
    _font(r, CODE_FONT, 11, MUTED)
    if tag:
        tag_w = min(4.48, w * 0.45)
        tb, tf = _box(slide, x + w - tag_w - 0.12, y + 0.04, tag_w, head_h - 0.06,
                      name="CODEHEAD")
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.RIGHT
        r = p.add_run()
        r.text = tag
        _font(r, BODY_FONT, 12, tag_color, bold=True)

    top = y + head_h + 0.1
    if not isinstance(highlight, dict):
        highlight = {n: hl_color for n in highlight}
    for i, (n, _) in enumerate(lines):
        if n is not None and n in highlight:
            add_rect(slide, x + 0.06, top + i * leading / 72, w - 0.12, leading / 72,
                     fill=highlight[n])

    tb, tf = _box(slide, x + 0.16, top, w - 0.24, body_h, name="CODE")
    tf.word_wrap = False
    for side in ("left", "right", "top", "bottom"):
        setattr(tf, f"margin_{side}", 0)
    lex = PythonLexer() if lexer == "python" else BashLexer()
    toks = _tokenize(lines, lex)
    for i, (n, text) in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.line_spacing = Pt(leading)
        p.space_before = p.space_after = Pt(0)
        if n is None:
            r = p.add_run()
            r.text = text
            _font(r, CODE_FONT, size, MUTED)
            continue
        if not toks[i]:
            r = p.add_run()
            r.text = " "
            _font(r, CODE_FONT, size, CODE_DEFAULT)
        for part, color in toks[i]:
            r = p.add_run()
            r.text = part
            _font(r, CODE_FONT, size, color, italic=(color == CODE_COLORS[0][1]))
    return h


# ---------------------------------------------------------------- assets ----
def trim_diagram(src_png, dst_png, drop_title=True, pad=24):
    """Crop white margins (and the diagram's own title line) from a block diagram."""
    im = Image.open(src_png).convert("RGB")
    ink = np.array(im.convert("L")) < 200
    rows = ink.any(axis=1)
    cols = np.where(ink.any(axis=0))[0]
    starts = [i for i in range(1, len(rows)) if rows[i] and not rows[i - 1]]
    top = starts[1] if drop_title and len(starts) > 1 else np.where(rows)[0][0]
    bottom = np.where(rows)[0][-1]
    box = (max(cols[0] - pad, 0), max(top - pad, 0),
           min(cols[-1] + pad, im.width), min(bottom + pad, im.height))
    im.crop(box).save(dst_png)
    return dst_png


def build_assets():
    os.makedirs(ASSETS, exist_ok=True)
    a = {}
    flames = {
        "pf_orig": src("pyflate", "original", "flamegraph_original.svg"),
        "pf_opt": src("pyflate", "optimized", "flamegraph_pyflate_optimized.svg"),
    }
    render_base(flames["pf_orig"], os.path.join(ASSETS, "fg_pyflate_original.png"),
                levels=6, width_in=7.1, row_in=0.5, fontsize=11)
    a["pf_orig_wide"] = os.path.join(ASSETS, "fg_pyflate_original.png")
    render_base(flames["pf_orig"], os.path.join(ASSETS, "fg_pyflate_original_half.png"),
                levels=6, width_in=5.9, row_in=0.44, fontsize=10)
    a["pf_orig_half"] = os.path.join(ASSETS, "fg_pyflate_original_half.png")
    render_base(flames["pf_opt"], os.path.join(ASSETS, "fg_pyflate_optimized_half.png"),
                levels=6, width_in=5.9, row_in=0.44, fontsize=10)
    a["pf_opt_half"] = os.path.join(ASSETS, "fg_pyflate_optimized_half.png")
    a["pf_block"] = trim_diagram(src("pyflate", "hardware", "block_diagram.png"),
                                 os.path.join(ASSETS, "block_pyflate.png"))

    # nbody's stacks are deep but only ~4 levels wide at the base
    nb_orig = src("nbody", "original", "flamegraph_original.svg")
    nb_opt = src("nbody", "optimized_unroll_experiment",
                 "flamegraph_optimized_unroll_experiment.svg")
    a["nb_orig_wide"] = render_base(nb_orig, os.path.join(ASSETS, "fg_nbody_original.png"),
                                    levels=5, width_in=7.1, row_in=0.55, fontsize=11)
    a["nb_orig_half"] = render_base(nb_orig,
                                    os.path.join(ASSETS, "fg_nbody_original_half.png"),
                                    levels=5, width_in=5.9, row_in=0.48, fontsize=10)
    a["nb_opt_half"] = render_base(nb_opt,
                                   os.path.join(ASSETS, "fg_nbody_unroll_half.png"),
                                   levels=5, width_in=5.9, row_in=0.48, fontsize=10)
    a["nb_block"] = trim_diagram(src("nbody", "hardware", "block_diagram.png"),
                                 os.path.join(ASSETS, "block_nbody.png"))
    return a


# ---------------------------------------------------------------- slides ----
class Deck:
    def __init__(self):
        self.prs = Presentation()
        self.prs.slide_width = Inches(SLIDE_W)
        self.prs.slide_height = Inches(SLIDE_H)
        self.blank = self.prs.slide_layouts[6]
        self.n = 0

    def slide(self, title=None, section=None):
        s = self.prs.slides.add_slide(self.blank)
        self.n += 1
        bg = s.background.fill
        bg.solid()
        bg.fore_color.rgb = WHITE
        if title:
            add_title(s, title)
        if self.n > 1:
            add_footer(s, section, self.n)
        return s


def slide_title(d):
    s = d.slide()
    add_rect(s, 0, 0, 0.32, SLIDE_H, fill=NAVY)
    add_rect(s, 0.32, 0, 0.06, SLIDE_H, fill=TEAL)
    tb, tf = _box(s, 1.2, 2.3, 10.8, 1.3, name="TITLE")
    tf.vertical_anchor = MSO_ANCHOR.BOTTOM
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = "Profiling & Accelerating Python Benchmarks"
    _font(r, TITLE_FONT, TITLE_SIZE, NAVY)
    add_rect(s, 1.25, 3.72, 1.1, 0.05, fill=TEAL)
    add_text(s, "pyflate and nbody: from perf profiles to software fixes to hardware "
                "accelerator proposals", 1.2, 3.95, 10.5, 0.9, color=INK)
    add_text(s, "Chris Shakkour · Razan Jiryis\nCourse 00460882 · 06.10.2026", 1.2, 5.3, 8, 0.9,
             color=MUTED)
    set_notes(s, """
        Hi everyone. In this project we took two benchmarks from the pyperformance
        suite, pyflate and nbody, and asked the same questions about each one: where
        does the time actually go, why does it go there, how much can we win back in
        software, and what would dedicated hardware for the hot loop look like. Both
        parts of the talk follow the same six steps: what the benchmark does,
        profiling with perf, the bottlenecks we found, the optimization, the measured
        results, and finally a hardware accelerator proposal. We start with pyflate,
        where software alone gives a huge win, and then nbody, where the software
        story is much harder, which is exactly why the hardware proposal matters
        more there.
    """)


def slide_divider(d, part, name, subtitle, section, notes):
    s = d.slide(section=section)
    add_rect(s, 0, 2.35, SLIDE_W, 2.6, fill=PANEL)
    add_rect(s, 0, 2.35, 0.32, 2.6, fill=NAVY)
    add_text(s, part, 1.2, 2.65, 6, 0.45, color=TEAL, bold=True)
    tb, tf = _box(s, 1.2, 3.1, 11, 0.8, name="TITLE")
    r = tf.paragraphs[0].add_run()
    r.text = name
    _font(r, TITLE_FONT, TITLE_SIZE, NAVY)
    add_text(s, subtitle, 1.2, 3.95, 11, 0.8, color=INK)
    set_notes(s, notes)


# ------------------------------------------------------------ pyflate ----
PF = "pyflate"
PF_ORIG = src("pyflate", "original", "run_benchmark.py")
PF_OPT = src("pyflate", "optimized", "run_benchmark.py")


def pyflate_slides(d, a):
    sec = "Part A · pyflate"

    # 2 ─ divider
    slide_divider(d, "PART A", "pyflate", "A pure-Python bzip2 decompressor", sec, """
        Part A: pyflate. The name suggests gzip, but as we'll see, the file it
        actually decompresses is bzip2, so everything in this part is about the bzip2
        decode path. The one thing to keep in mind for the next few slides is that
        the whole decompressor is written in plain Python, down to reading one bit
        at a time.
    """)

    # 3 ─ analysis
    s = d.slide("What pyflate does", sec)
    add_bullets(s, [
        "pyperformance benchmark: a stand-alone, pure-Python bzip2 / gzip decoder "
        "(Paul Sladen, 2006)",
        "Input: **interpreter.tar.bz2**, a snapshot of the CPython source tree",
        "Output is verified with an **MD5 checksum** on every run",
        "No compression library at all: every bit is decoded by Python code",
    ], MARGIN, CONTENT_TOP + 0.1, 8.1, 3.0)
    add_rect(s, 9.25, CONTENT_TOP + 0.15, 3.48, 2.3, fill=PANEL, radius=0.05)
    add_text(s, "Baseline", 9.45, CONTENT_TOP + 0.3, 3.1, 0.4, color=MUTED,
             align=PP_ALIGN.CENTER)
    tb, tf = _box(s, 9.45, CONTENT_TOP + 0.7, 3.1, 0.9, name="STAT")
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = "3.17 s"
    _font(r, TITLE_FONT, 40, NAVY)
    add_text(s, "± 0.02 s per run (pyperf)", 9.45, CONTENT_TOP + 1.65, 3.1, 0.45,
             color=MUTED, align=PP_ALIGN.CENTER)

    add_text(s, "**Decode pipeline, once per bzip2 block** (bzip2_main)",
             MARGIN, 4.45, 10, 0.45)
    stages = [
        ("Bit reader", "byte by byte"),
        ("Huffman decode", "per symbol"),
        ("Move-to-front", "per symbol"),
        ("Inverse BWT", "per block"),
        ("RLE decode", "per output byte"),
        ("MD5 check", "once per run"),
    ]
    hot = {1, 2, 4}
    bw, gap, by, bh = 1.78, 0.285, 5.0, 1.25
    for i, (name, sub) in enumerate(stages):
        bx = MARGIN + i * (bw + gap)
        is_hot = i in hot
        add_rect(s, bx, by, bw, bh, fill=HL_BAD if is_hot else PANEL,
                 line=RED if is_hot else None, radius=0.08)
        add_text(s, f"**{name}**\n{sub}", bx + 0.05, by + 0.08, bw - 0.1, bh - 0.16,
                 color=INK, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        if i < len(stages) - 1:
            add_arrow(s, bx + bw + 0.03, by + bh / 2, bx + bw + gap - 0.03, by + bh / 2)
    add_text(s, "Red = stages that run once per symbol or per byte, which the profile "
                "later points to", MARGIN, 6.35, CONTENT_W, 0.45, color=MUTED)
    set_notes(s, """
        pyflate is a pure-Python decompressor. The benchmark decompresses a real
        bzip2 file, a tarball of the CPython source, and checks the result against a
        known MD5, so we know any optimization still produces byte-identical output.
        The important point is that there's no compression library involved at all.
        Every stage you see at the bottom is Python code: a bit reader that pulls one
        byte at a time from the file, Huffman decoding, move-to-front, the inverse
        Burrows-Wheeler transform and a final run-length decode. I've marked in red
        the stages that run once per symbol or per output byte. Those run millions of
        times, so they're our suspects before we even open the profiler. One run takes
        about 3.17 seconds. Let's see what perf says.
    """)

    # 4 ─ perf stat
    s = d.slide("Profiling: hardware counters (perf stat)", sec)
    add_table(s, [
        ["Counter", "Value", "What it tells us"],
        ["Instructions", "1,433,334,987,236", "~1.4 trillion over the whole pyperf run"],
        ["Cycles", "659,081,747,854", "the CPU is busy the whole time"],
        ["IPC", "2.17 insn / cycle", "high: the core is rarely stalled"],
        ["Cache-miss rate", "7.0 %", "highest of our two benchmarks"],
        ["Branch-miss rate", "0.43 %", "the interpreter loop predicts well"],
    ], MARGIN, CONTENT_TOP + 0.1, [2.6, 3.3, 6.23], row_h=0.5,
        aligns=[PP_ALIGN.LEFT, PP_ALIGN.RIGHT, PP_ALIGN.LEFT],
        colors={(4, 1): RED}, bold_cells={(4, 1)})
    cmd = [
        (1, "perf stat -e cycles,instructions,cache-references,cache-misses,\\"),
        (2, "             branch-instructions,branch-misses,bus-cycles,ref-cycles \\"),
        (3, "          -- python3-dbg run_benchmark.py"),
        (4, "perf record -F 999 -g -e cpu-clock -- python3-dbg run_benchmark.py"),
    ]
    add_code(s, cmd, MARGIN, 4.6, 7.3, code_font_size(cmd, 7.3, max_size=12),
             "commands (run inside the VM)", lexer="bash", h=1.95)
    add_card(s, 8.15, 4.6, 4.58, 1.95, "Why cpu-clock?", [
        "The VM's virtual PMU never raises sampling interrupts, so "
        "perf record on cycles gets 0 samples",
    ])
    set_notes(s, """
        First we measured the whole benchmark with perf stat, which just counts
        hardware events. The full pyperf run, with all its repetitions, executes about
        1.4 trillion instructions. The IPC of 2.17
        tells us the core is busy and not waiting on memory most of the time: the
        problem is that there's simply a huge amount of work. The cache-miss rate of
        7 percent is the highest of our two benchmarks, and that's a hint we'll come
        back to, because it points to lots of small objects being allocated all over
        memory. At the bottom are the exact commands. One practical detail: for the
        sampling profile we used the cpu-clock software event instead of cycles. In
        our VM the virtual PMU never delivers the sampling interrupt, so a cycles-based
        perf record silently collects zero samples. perf stat reads the counters
        directly, so it works fine.
    """)

    # 5 ─ flame graph
    s = d.slide("Profiling: flame graph of the original run", sec)
    _, (px, py, pw, ph) = add_image_fit(s, a["pf_orig_wide"], MARGIN, CONTENT_TOP + 0.15,
                                        7.1, 3.6, border=True)
    add_text(s, "Bottom 6 stack levels of original/flamegraph_original.svg. Width = "
                "share of CPU samples; the call stack grows upward from the root.",
             MARGIN, py + ph + 0.15, 7.1, 0.9, color=MUTED)
    add_rect(s, 7.9, CONTENT_TOP + 0.15, 4.83, 5.1, fill=PANEL, radius=0.04)
    add_text(s, "**Widest self-time frames**", 8.1, CONTENT_TOP + 0.28, 4.5, 0.45)
    add_table(s, [
        ["23.5 %", "_PyEval_EvalFrameDefault"],
        ["4.1 %", "_PyMem_DebugCheckAddress"],
        ["3.0 %", "__memset_avx2_unaligned"],
        ["2.9 %", "read_size_t"],
        ["2.5 %", "list_dealloc"],
        ["2.3 %", "call_function"],
        ["2.1 %", "list_ass_slice"],
        ["1.5 %", "lookdict_unicode_nodummy"],
    ], 8.0, CONTENT_TOP + 0.85, [0.95, 3.68], row_h=0.5, header=False,
        aligns=[PP_ALIGN.RIGHT, PP_ALIGN.LEFT],
        colors={(0, 0): NAVY, **{(r, 0): RED for r in (1, 2, 3, 4, 6)},
                **{(r, 0): AMBER for r in (5, 7)}})
    set_notes(s, """
        This is the flame graph, cropped to the bottom levels where the wide frames
        are. How to read it: the x-axis is the share of CPU samples, not time order,
        and the stack grows upward from the root at the bottom. The biggest single
        frame is the interpreter's main loop, _PyEval_EvalFrameDefault, at about 23
        percent. That's the cost of running Python bytecode at all. What's more
        interesting is the rest: the right-hand panel lists the widest leaf frames,
        and most of them are memory functions: the debug allocator's address checks,
        memset, list_dealloc and list_ass_slice. There are also call_function and
        dictionary lookups, which are the cost of calling small Python functions and
        reading object attributes. The graph looks like a wide forest of thin towers,
        which means many small, separate costs rather than one obvious hot function.
    """)

    # 6 ─ hotspots
    s = d.slide("Bottlenecks: where the self-time goes", sec)
    cat = {"I": ("Interpreter", NAVY), "M": ("Memory / allocator", RED),
           "A": ("Algorithmic", AMBER)}
    hot_rows = [
        ("22.40", "_PyEval_EvalFrameDefault", "I"),
        ("4.06", "_PyMem_DebugCheckAddress", "M"),
        ("2.81", "read_size_t", "M"),
        ("2.74", "__memset_avx2_unaligned_erms", "M"),
        ("2.46", "call_function", "A"),
        ("2.32", "list_dealloc", "M"),
        ("1.92", "list_ass_slice", "M"),
        ("1.41", "lookdict_unicode_nodummy", "A"),
    ]
    rows = [["Self %", "Symbol", "Category"]]
    colors = {}
    for i, (pct, sym, c) in enumerate(hot_rows, start=1):
        rows.append([pct, sym, cat[c][0]])
        colors[(i, 2)] = cat[c][1]
    add_table(s, rows, MARGIN, CONTENT_TOP + 0.1, [1.2, 4.3, 2.6], row_h=0.5,
              aligns=[PP_ALIGN.RIGHT, PP_ALIGN.LEFT, PP_ALIGN.LEFT], colors=colors)
    add_text(s, "perf report --stdio --no-children -g none  (self time only)",
             MARGIN, CONTENT_TOP + 4.7, 8.1, 0.45, color=MUTED)
    add_card(s, 9.0, CONTENT_TOP + 0.1, 3.73, 2.35, "Memory: > 15 %", [
        "allocator, memset, list free and slice copy together",
    ], accent=RED)
    add_card(s, 9.0, CONTENT_TOP + 2.65, 3.73, 2.35, "Algorithmic", [
        "one Python call and several attribute lookups per step of a search",
    ], accent=AMBER)
    set_notes(s, """
        To rank the hotspots properly, we used perf report with no-children, which
        gives self time: the time spent in a function's own code, not in what it calls.
        I've grouped the top entries into three categories. Navy is the interpreter
        itself. Red is memory management: allocating, clearing, copying and freeing
        objects. Added up, the memory entries are more than 15 percent of the whole run.
        Amber is algorithmic overhead: call_function and lookdict are the price of
        calling a tiny Python function and looking up attributes like x.bits and
        x.code, over and over. Note that the allocator numbers are inflated a bit
        because we profile the debug build of Python, python3-dbg, which adds checks to
        every allocation. But the question is still: which lines of code produce all
        these allocations and calls? That's the next slide.
    """)

    # 7 ─ problem code
    s = d.slide("Bottlenecks: the code behind the hotspots", sec)
    top = snippet(PF_ORIG, (224, 235), dedent=4)
    rle = snippet(PF_ORIG, (448, 455), dedent=4)
    mtf = snippet(PF_ORIG, (288, 289))
    size = min(code_font_size(top, CONTENT_W, max_size=13),
               code_font_size(rle, 7.75, max_size=13),
               code_font_size(mtf, 4.18, max_size=13))
    h1 = add_code(s, top, MARGIN, CONTENT_TOP, CONTENT_W, size,
                  "run_benchmark.py · HuffmanTable.find_next_symbol · lines 224-235",
                  tag="linear scan, runs once per decoded symbol",
                  highlight={227, 231, 232}, tag_color=AMBER)
    y2 = CONTENT_TOP + h1 + 0.15
    add_code(s, rle, MARGIN, y2, 7.75, size,
             "run_benchmark.py · RLE decode · lines 448-455",
             tag="new bytes object per output byte", highlight={451, 454})
    add_code(s, mtf, MARGIN + 7.95, y2, 4.18, size,
             "move_to_front · 288-289", tag="new list per symbol",
             highlight={289})
    set_notes(s, """
        Here are the three pieces of code behind those numbers, straight from the
        benchmark source. At the top is find_next_symbol, which decodes one Huffman
        symbol. It loops over the entire symbol table and, for each entry, compares
        the next bits of the stream to that entry's code. That's a linear search,
        and it runs for every single symbol in the file. Each iteration touches
        attributes like x.bits and x.reverse_symbol, which is the lookdict and
        call_function overhead we saw. Bottom left is the run-length decode: every
        output byte creates a new one-byte bytes object with a slice. Bottom right is
        move_to_front. That one line builds three new list slices and concatenates
        them into a fourth list, on every symbol, instead of moving one element in
        place.
    """)

    # 8 ─ root cause
    s = d.slide("Bottlenecks: root cause", sec)
    add_card(s, MARGIN, CONTENT_TOP + 0.1, 5.95, 3.1,
             "1 · Algorithmic: O(n) Huffman search", [
                 "Every decoded symbol scans the symbol table from the start",
                 "Cost grows with table size × symbols decoded",
                 "Shows up as call_function 2.46 %, lookdict 1.41 %, "
                 "PyTuple_GetItem 1.39 %, binary_op1 1.29 %",
             ], accent=AMBER)
    add_card(s, MARGIN + 6.18, CONTENT_TOP + 0.1, 5.95, 3.1,
             "2 · Memory: allocation churn", [
                 "move_to_front rebuilds the list on every symbol",
                 "RLE creates one tiny bytes object per output byte",
                 "Allocator frames add up to more than 15 % of runtime",
                 "7.0 % cache misses, ~863K page-fault samples",
             ], accent=RED)
    add_rect(s, MARGIN, 4.85, CONTENT_W, 0.95, fill=NAVY, radius=0.06)
    add_text(s, "Both costs exist only because bzip2 is re-implemented in pure Python, "
                "one object at a time.", MARGIN + 0.3, 4.9, CONTENT_W - 0.6, 0.85,
             color=WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    set_notes(s, """
        So we have two root causes. The first is algorithmic. Real bzip2 decoders use
        lookup tables, so one symbol costs a constant amount of work. This one does a
        linear search, so the cost per symbol grows with the size of the table. The
        second is memory churn. Python lists and bytes are heap objects, so every
        slice and every concatenation allocates, copies and later frees memory. Doing
        that once per symbol and once per output byte is what fills the profile with
        allocator functions. It also explains the 7 percent cache-miss rate: we keep
        touching freshly allocated, scattered memory instead of a small, hot working
        set. The key insight is the bar at the bottom. Neither problem is part of
        bzip2 itself. They exist only because the decoder is written in Python,
        object by object.
    """)

    # 9 ─ optimization idea
    s = d.slide("Optimization: use the C bz2 module", sec)
    add_bullets(s, [
        "Python's standard library already ships **bz2**, a thin wrapper around the "
        "compiled C library libbz2",
        "Same algorithm, done with lookup tables and in-place buffers in C",
        "Removes the Huffman scan, the move-to-front list churn and the per-byte RLE "
        "loop in **one change**",
        "Correctness: the same MD5 check still passes, so the output is byte-identical",
    ], MARGIN, CONTENT_TOP + 0.1, 6.6, 4.6)
    bx, bw = 7.75, 4.98
    add_rect(s, bx, CONTENT_TOP + 0.15, bw, 1.9, fill=HL_BAD, line=RED, radius=0.06)
    add_text(s, "**Before**\n~600 lines of Python decoder: bit reader, Huffman "
                "tables, MTF, BWT, RLE", bx + 0.2, CONTENT_TOP + 0.25, bw - 0.4, 1.7,
             anchor=MSO_ANCHOR.MIDDLE)
    add_arrow(s, bx + bw / 2, CONTENT_TOP + 2.15, bx + bw / 2, CONTENT_TOP + 2.75,
              color=NAVY, width=2.5)
    add_rect(s, bx, CONTENT_TOP + 2.85, bw, 1.9, fill=HL_GOOD, line=GREEN, radius=0.06)
    add_text(s, "**After**\none call: bz2.decompress(data), which runs inside libbz2 "
                "(C)", bx + 0.2, CONTENT_TOP + 2.95, bw - 0.4, 1.7,
             anchor=MSO_ANCHOR.MIDDLE)
    set_notes(s, """
        Since both bottlenecks come from implementing bzip2 in Python, the most
        effective fix is not to do that. Python's standard library already has a bz2
        module, which is a thin wrapper around libbz2, the same C library everyone
        uses. It performs exactly the same decompression, but with table-driven
        Huffman decoding and in-place buffers, so it removes both bottlenecks at once.
        We considered fixing the two problems separately, with a dictionary-based
        Huffman lookup and an in-place move-to-front, and that would be a nice exercise.
        But the library swap is what you would actually do in real code. To prove it's
        still correct, the optimized version runs exactly the same MD5 check, and it
        passes, so the output is byte-for-byte identical.
    """)

    # 10 ─ code comparison
    s = d.slide("Optimization: original vs. optimized code", sec)
    left = snippet(PF_ORIG, (632, 645), "...", (650, 650))
    right = snippet(PF_OPT, (28, 38))
    pw_ = (CONTENT_W - 0.25) / 2
    size = min(code_font_size(left, pw_), code_font_size(right, pw_))
    h_ = max(len(left), len(right)) * size * 1.27 / 72 + 0.58
    add_code(s, left, MARGIN, CONTENT_TOP, pw_, size,
             "original/run_benchmark.py · lines 632-650",
             tag="pure-Python decoder", highlight={639, 645}, h=h_)
    add_code(s, right, MARGIN + pw_ + 0.25, CONTENT_TOP, pw_, size,
             "optimized/run_benchmark.py · lines 28-38",
             tag="stdlib bz2 (C)", tag_color=GREEN, highlight={29, 30, 36},
             hl_color=HL_GOOD, h=h_)
    add_text(s, "Same timing harness and MD5 check on both sides. Only the decode call "
                "changes: **bzip2_main(field)** becomes **bz2.decompress(compressed)**.",
             MARGIN, CONTENT_TOP + h_ + 0.2, CONTENT_W, 0.8, color=INK)
    set_notes(s, """
        Here's the change side by side. On the left is the original benchmark loop:
        for each iteration it wraps the open file in a bit reader, reads the 16-bit
        magic number to decide between gzip and bzip2, and calls bzip2_main, which is
        the entry into the six hundred lines of Python decoder. On the right is the
        optimized version: read the compressed file into memory once, and each
        iteration makes a single call to bz2.decompress. The timing harness around it
        is identical, and after the loop both versions compute the same MD5 and raise
        an error if it doesn't match. One fair question is whether reading the file
        up front is cheating. It isn't really: the original also reads the whole file
        on every loop, just one byte at a time, so the I/O cost is tiny in both cases.
    """)

    # 11 ─ results
    s = d.slide("Results: ~244× faster", sec)
    add_rect(s, MARGIN, CONTENT_TOP + 0.1, 4.1, 3.55, fill=PANEL, radius=0.05)
    add_text(s, "Mean ± std dev (pyperf)", MARGIN + 0.2, CONTENT_TOP + 0.25, 3.7, 0.4,
             color=MUTED, align=PP_ALIGN.CENTER)
    for i, (val, lab, col) in enumerate([("3.17 s", "original, ± 0.02 s", INK),
                                         ("13.0 ms", "optimized, ± 0.3 ms", GREEN)]):
        yy = CONTENT_TOP + 0.75 + i * 1.35
        tb, tf = _box(s, MARGIN + 0.2, yy, 3.7, 0.75, name="STAT")
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = val
        _font(r, TITLE_FONT, 40, col)
        add_text(s, lab, MARGIN + 0.2, yy + 0.72, 3.7, 0.4, color=MUTED,
                 align=PP_ALIGN.CENTER)
    add_table(s, [
        ["Counter", "Original", "Optimized", "Change"],
        ["Instructions", "1.43 T", "41.7 G", "34.4× fewer"],
        ["Cycles", "659 G", "33.0 G", "20.0× fewer"],
        ["IPC", "2.17", "1.26", "lower"],
        ["Cache-miss rate", "7.0 %", "1.72 %", "4.1× better"],
        ["Branch-miss rate", "0.43 %", "3.19 %", "higher"],
    ], MARGIN + 4.35, CONTENT_TOP + 0.1, [2.4, 1.55, 1.7, 2.13], row_h=0.59,
        aligns=[PP_ALIGN.LEFT, PP_ALIGN.RIGHT, PP_ALIGN.RIGHT, PP_ALIGN.LEFT],
        colors={(1, 3): GREEN, (2, 3): GREEN, (4, 3): GREEN, (3, 3): MUTED,
                (5, 3): MUTED})
    add_card(s, MARGIN, CONTENT_TOP + 3.9, CONTENT_W, 1.5,
             "Why did IPC go down and branch misses go up?", [
                 "58.7 % of the new run is libbz2's bit-level decode, which is harder "
                 "to predict than the repetitive bytecode loop. What matters is 34× "
                 "fewer instructions for the same work.",
             ])
    set_notes(s, """
        The result: one run went from 3.17 seconds to 13 milliseconds, about 244 times
        faster. The assignment target was 7 percent, so this is far past it. The
        counters explain why: the same job now needs 34 times fewer instructions and
        20 times fewer cycles, and the cache-miss rate dropped from 7 percent to under
        2, because we stopped creating millions of tiny objects. The line you might
        ask about is IPC, which went down, and branch misses, which went up. That
        sounds like a regression, but it isn't. Most of the new run is spent in
        libbz2's tight bit-manipulation code, whose branches depend on the data and
        are genuinely hard to predict. The old interpreter loop looked great to the
        branch predictor because it repeated the same pattern, but it was doing 34
        times more work. So total instructions is the metric that matters here.
    """)

    # 12 ─ flame graphs before / after
    s = d.slide("Results: flame graphs before and after", sec)
    half = (CONTENT_W - 0.3) / 2
    for i, (key, label, lines, col) in enumerate([
        ("pf_orig_half", "**Before**: a wide forest of thin Python towers",
         ["23.5 %  _PyEval_EvalFrameDefault", "4.1 %  _PyMem_DebugCheckAddress",
          "2.5 %  list_dealloc · 2.1 % list_ass_slice"], RED),
        ("pf_opt_half", "**After**: two C plateaus in libbz2",
         ["37.9 %  BZ2_decompress", "20.9 %  BZ2_bzDecompress",
          "3.1 %  _PyEval_EvalFrameDefault (thin wrapper)"], GREEN),
    ]):
        x = MARGIN + i * (half + 0.3)
        add_text(s, label, x, CONTENT_TOP + 0.05, half, 0.45)
        _, (px, py, pw, ph) = add_image_fit(s, a[key], x, CONTENT_TOP + 0.6, half, 2.8,
                                            border=True)
        add_rect(s, x, py + ph + 0.2, 0.06, 1.4, fill=col)
        add_bullets(s, lines, x + 0.15, py + ph + 0.15, half - 0.2, 1.6, space_after=4)
    add_text(s, "Compare shape and percentages, not raw sample counts: the optimized "
                "run is ~244× shorter.", MARGIN, 6.35, CONTENT_W, 0.45, color=MUTED)
    set_notes(s, """
        The flame graphs confirm the change visually. On the left is the original:
        lots of narrow, differently coloured towers across the whole width, each one a
        small Python-level cost like list_dealloc or list_ass_slice. On the right is
        the optimized run: almost everything collapses into two wide, flat plateaus,
        BZ2_decompress and BZ2_bzDecompress, together about 59 percent. That's one
        compiled C function doing sustained work instead of thousands of short Python
        calls. The interpreter loop is still there, but it's down to 3 percent, which
        is just the thin wrapper around the call. One caution when comparing these:
        the sample counts are very different because the second run is so much
        shorter, so compare the shape and percentages, not the raw widths in samples.
    """)

    # 13 ─ hardware concept
    s = d.slide("Hardware proposal: Huffman Fast-Decode Unit (HFDU)", sec)
    add_key_facts(s, a["pf_block"], [
        ("1 cycle", "per symbol on a level-1 table hit"),
        ("6 banks", "one per Huffman group"),
        ("200-400 MHz", "FPGA target · hardware/hfdu.sv"),
    ])
    set_notes(s, """
        For the hardware proposal we targeted the algorithmic bottleneck, the Huffman
        symbol search, because that's the part that maps naturally onto hardware. We
        call it the Huffman Fast-Decode Unit, written in SystemVerilog. On the left,
        software still parses each block header, but instead of building Python
        objects it builds lookup tables and sends them to the accelerator by DMA,
        together with the bitstream. Inside the unit, a 20-bit shift register holds
        the next bits of the stream. The top 8 bits index a 256-entry level-1 table,
        which directly returns the symbol and its code length in a single cycle for
        most symbols. Longer codes fall through to a small level-2 table. There are
        six banks because bzip2 switches between up to six Huffman tables every 50
        symbols, so the selector just changes the bank index with no reload stall.
    """)

    # 14 ─ hardware trade-offs
    s = d.slide("Hardware proposal: benefit and trade-offs", sec)
    qw, qh = (CONTENT_W - 0.25) / 2, 2.55
    quads = [
        ("Performance", TEAL, [
            "Decode goes from an O(n) scan to ~1 cycle per symbol",
            "Removes ~6-7 % of self-time in call / lookup overhead, plus the scan itself",
        ]),
        ("Area", NAVY, [
            "6 × (256-entry L1 + small L2) SRAM",
            "A few KB in total: cheap in FPGA BRAM or ASIC SRAM",
        ]),
        ("Power", GREEN, [
            "One table read and compare per symbol",
            "No fetch, decode or dispatch overhead as on a CPU",
        ]),
        ("Trade-off we made", AMBER, [
            "Keep all 6 banks loaded: more SRAM, zero switch stalls",
            "New bottleneck: MTF, BWT and RLE stay in software",
        ]),
    ]
    for i, (head, col, items) in enumerate(quads):
        x = MARGIN + (i % 2) * (qw + 0.25)
        y = CONTENT_TOP + 0.1 + (i // 2) * (qh + 0.2)
        add_card(s, x, y, qw, qh, head, items, accent=col)
    set_notes(s, """
        What does it buy us? In performance, symbol decoding goes from a linear scan
        to about one cycle per symbol, independent of the table size. In the profile,
        the call and lookup overhead of the scan alone was 6 to 7 percent, and the
        scan's own comparisons come on top of that. Area is modest: six small SRAM
        tables add up to a few kilobytes. Power is low because each symbol costs one
        table read and one compare, without any of a CPU's instruction fetch and decode
        overhead. The main design decision was keeping all six table banks loaded at
        once. It costs a bit more SRAM, but a design with one reloadable bank would
        stall on every table switch, which happens every 50 symbols. Honestly, once
        Huffman decoding is fast, the software stages become the new bottleneck. That
        is the natural next step, and the same approach is used in real hardware
        decompression engines like Intel QAT. That's pyflate. Now let's move to nbody.
    """)


# -------------------------------------------------------------- nbody ----
NB_ORIG = src("nbody", "original", "run_benchmark.py")
NB_UNROLL = src("nbody", "optimized_unroll_experiment", "run_benchmark.py")
HL_POW = RGBColor(0xFD, 0xF0, 0xD5)


def nbody_slides(d, a):
    sec = "Part B · nbody"

    # 15 ─ divider
    slide_divider(d, "PART B", "nbody", "A 5-body gravitational simulation in pure Python",
                  sec, """
        Part B: nbody. Here the situation is the opposite of pyflate. The algorithm
        is already the right one and there's no library we can swap in, so the
        question becomes how much we can win inside Python itself, and what that
        tells us about where hardware helps.
    """)

    # 16 ─ analysis
    s = d.slide("What nbody does", sec)
    add_bullets(s, [
        "From the Computer Language Benchmarks Game: the **Sun, Jupiter, Saturn, "
        "Uranus and Neptune** under mutual gravity",
        "Plain Python lists and floats; no numpy",
        "Each loop: report_energy() → **advance(0.01, 20000)** → report_energy()",
        "20,000 timesteps × 10 body pairs = **200,000 pair updates** per loop",
    ], MARGIN, CONTENT_TOP + 0.1, 8.1, 3.0)
    add_rect(s, 9.25, CONTENT_TOP + 0.15, 3.48, 2.3, fill=PANEL, radius=0.05)
    add_text(s, "Baseline", 9.45, CONTENT_TOP + 0.3, 3.1, 0.4, color=MUTED,
             align=PP_ALIGN.CENTER)
    tb, tf = _box(s, 9.45, CONTENT_TOP + 0.7, 3.1, 0.9, name="STAT")
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = "483 ms"
    _font(r, TITLE_FONT, 40, NAVY)
    add_text(s, "± 4 ms per loop (pyperf)", 9.45, CONTENT_TOP + 1.65, 3.1, 0.45,
             color=MUTED, align=PP_ALIGN.CENTER)

    add_text(s, "**One timestep of advance()**, repeated 20,000 times per loop",
             MARGIN, 4.45, 10, 0.45)
    stages = [
        ("10 pairs", "dx, dy, dz"),
        ("Distance²", "r² = dx²+dy²+dz²"),
        ("Force factor", "mag = dt · r^-1.5"),
        ("Update velocities", "6 list writes"),
        ("5 bodies", "pos += dt · vel"),
    ]
    hot = {2, 3}
    bw, gap, by, bh = 2.2, 0.28, 5.0, 1.25
    for i, (name, sub) in enumerate(stages):
        bx = MARGIN + i * (bw + gap)
        is_hot = i in hot
        add_rect(s, bx, by, bw, bh, fill=HL_BAD if is_hot else PANEL,
                 line=RED if is_hot else None, radius=0.08)
        add_text(s, f"**{name}**\n{sub}", bx + 0.05, by + 0.08, bw - 0.1, bh - 0.16,
                 color=INK, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        if i < len(stages) - 1:
            add_arrow(s, bx + bw + 0.03, by + bh / 2, bx + bw + gap - 0.03, by + bh / 2)
    add_text(s, "Red = the power operator and the list writes, which the profile later "
                "points to", MARGIN, 6.35, CONTENT_W, 0.45, color=MUTED)
    set_notes(s, """
        nbody simulates the Sun and the four gas giants attracting each other. It uses
        nothing but Python lists and floats. Each benchmark loop computes the system's
        energy, advances the simulation 20,000 timesteps, and computes the energy
        again. With five bodies there are ten pairs, so one loop does 200,000 pairwise
        force updates. At the bottom is one timestep. For every pair, compute the
        distance vector, its squared length, a force factor using a power of minus
        1.5, and then update both bodies' velocities. Then move every body by its
        velocity. The math per pair is about two dozen floating-point operations, which a
        CPU can do in nanoseconds. Yet one loop takes 483 milliseconds. So the
        question for this part is: where does all that time go if not into the math?
    """)

    # 17 ─ perf stat
    s = d.slide("Profiling: hardware counters (perf stat)", sec)
    add_table(s, [
        ["Counter", "Value", "What it tells us"],
        ["Instructions", "260,503,357,966", "~260 billion over the whole pyperf run"],
        ["Cycles", "107,660,144,120", "compute-bound, like pyflate"],
        ["IPC", "2.42 insn / cycle", "even higher than pyflate's 2.17"],
        ["Cache-miss rate", "2.83 %", "tiny working set: 5 bodies fit in L1"],
        ["Branch-miss rate", "0.49 %", "the interpreter loop predicts well"],
    ], MARGIN, CONTENT_TOP + 0.1, [2.6, 3.3, 6.23], row_h=0.5,
        aligns=[PP_ALIGN.LEFT, PP_ALIGN.RIGHT, PP_ALIGN.LEFT],
        colors={(3, 1): NAVY}, bold_cells={(3, 1)})
    cmd = [
        (1, "perf stat -e cycles,instructions,cache-references,cache-misses,\\"),
        (2, "             branch-instructions,branch-misses,bus-cycles,ref-cycles \\"),
        (3, "          -- python3-dbg run_benchmark.py"),
        (4, "perf record -F 999 -g -e cpu-clock -- python3-dbg run_benchmark.py"),
    ]
    add_code(s, cmd, MARGIN, 4.6, 7.3, code_font_size(cmd, 7.3, max_size=12),
             "same commands as pyflate", lexer="bash", h=1.95)
    add_card(s, 8.15, 4.6, 4.58, 1.95, "Reading the numbers", [
        "High IPC with low misses: the CPU is fast; it just has too many "
        "instructions to run",
    ])
    set_notes(s, """
        Same measurement method as for pyflate. The counters look almost perfect.
        IPC is 2.42, even higher than pyflate, and the cache-miss rate is under 3
        percent, which makes sense: the whole simulation state is five bodies, a few
        hundred bytes, and it sits in the L1 cache. Branch prediction is also good.
        So this is not a memory problem and not a branch problem. The CPU is running
        at full speed. It just has far too many instructions to execute for such a
        small amount of math. That tells us the cost is in the instructions around
        the arithmetic, and to find them we need the sampling profile.
    """)

    # 18 ─ flame graph
    s = d.slide("Profiling: flame graph of the original run", sec)
    _, (px, py, pw, ph) = add_image_fit(s, a["nb_orig_wide"], MARGIN, CONTENT_TOP + 0.15,
                                        7.1, 3.6, border=True)
    add_text(s, "Bottom 5 stack levels of original/flamegraph_original.svg. Above them the "
                "stacks are ~129 levels of recursive interpreter frames.",
             MARGIN, py + ph + 0.15, 7.1, 0.9, color=MUTED)
    add_rect(s, 7.9, CONTENT_TOP + 0.15, 4.83, 5.1, fill=PANEL, radius=0.04)
    add_text(s, "**Widest self-time frames**", 8.1, CONTENT_TOP + 0.28, 4.5, 0.45)
    add_table(s, [
        ["41.7 %", "_PyEval_EvalFrameDefault"],
        ["5.3 %", "binary_op1"],
        ["3.6 %", "PyFloat_FromDouble"],
        ["3.4 %", "float_mul"],
        ["3.0 %", "float_dealloc"],
        ["2.5 %", "list_ass_item"],
        ["1.9 %", "float_add"],
        ["1.7 %", "__ieee754_pow_fma"],
    ], 8.0, CONTENT_TOP + 0.85, [0.95, 3.68], row_h=0.5, header=False,
        aligns=[PP_ALIGN.RIGHT, PP_ALIGN.LEFT],
        colors={(0, 0): NAVY, (1, 0): NAVY, **{(r, 0): RED for r in (2, 3, 4, 6)},
                (5, 0): AMBER, (7, 0): AMBER})
    set_notes(s, """
        Here is nbody's flame graph, again cropped to the base. Unlike pyflate it's
        very simple at the bottom: almost everything sits under the interpreter loop,
        and above what you see here the stacks go up about 129 levels of nested
        interpreter frames. The right-hand panel shows the widest frames. The
        interpreter loop dominates. After it comes binary_op1, which is CPython's
        generic dispatcher for arithmetic operators. Then come the float functions:
        creating a float object, multiplying, freeing, adding. List item assignment
        and the pow function are there too. One detail in case you compare with the
        next slide: the flame graph gives the interpreter loop about 42 percent, while
        perf report's self-time ranking gives 29 percent. The two views count that
        frame differently, but the order of the hotspots is the same.
    """)

    # 19 ─ hotspots
    s = d.slide("Bottlenecks: where the self-time goes", sec)
    cat = {"I": ("Interpreter", NAVY), "F": ("Float boxing", RED),
           "L": ("List writes", AMBER)}
    hot_rows = [
        ("29.40", "_PyEval_EvalFrameDefault", "I"),
        ("3.37", "binary_op1", "I"),
        ("2.51", "PyFloat_FromDouble", "F"),
        ("2.35", "float_mul", "F"),
        ("2.01", "float_dealloc", "F"),
        ("1.65", "list_ass_item", "L"),
        ("1.44", "validate_list", "L"),
        ("1.33", "float_add", "F"),
    ]
    rows = [["Self %", "Symbol", "Category"]]
    colors = {}
    for i, (pct, sym, c) in enumerate(hot_rows, start=1):
        rows.append([pct, sym, cat[c][0]])
        colors[(i, 2)] = cat[c][1]
    add_table(s, rows, MARGIN, CONTENT_TOP + 0.1, [1.2, 4.3, 2.6], row_h=0.5,
              aligns=[PP_ALIGN.RIGHT, PP_ALIGN.LEFT, PP_ALIGN.LEFT], colors=colors)
    add_text(s, "perf report --stdio --no-children -g none  (self time only)",
             MARGIN, CONTENT_TOP + 4.7, 8.1, 0.45, color=MUTED)
    add_card(s, 9.0, CONTENT_TOP + 0.1, 3.73, 2.35, "Float boxing: ~9.3 %", [
        "create, compute and free a heap object for every float result",
    ], accent=RED)
    add_card(s, 9.0, CONTENT_TOP + 2.65, 3.73, 2.35, "List writes: 4.17 %", [
        "v1[0] -= … goes through list_ass_item and debug checks",
    ], accent=AMBER)
    set_notes(s, """
        With the self-time ranking we can group the costs. Navy is the interpreter:
        the main loop, plus binary_op1, the generic function every plus and times goes
        through to find out what types it's dealing with. Red is what we call float
        boxing. In CPython a float is not a register value, it's a heap object, so
        every arithmetic result allocates a new object and the old one gets freed.
        PyFloat_FromDouble, float_mul, float_add, float_sub and float_dealloc together
        are about 9.3 percent. Amber is list writes: every velocity update like v1[0]
        minus-equals goes through list item assignment, plus validate_list, which is
        a check that exists only in the debug build. That's 4.17 percent. Let's map
        these to the code.
    """)

    # 20 ─ problem code
    s = d.slide("Bottlenecks: the code behind the hotspots", sec)
    adv = snippet(NB_ORIG, (78, 97))
    cw = 7.45
    size = code_font_size(adv, cw, max_size=13)
    hl = {**{n: HL_BAD for n in (82, 83, 84, 86, 87)},
          85: HL_POW, **{n: HL_BAD for n in range(88, 94)}}
    add_code(s, adv, MARGIN, CONTENT_TOP, cw, size,
             "original/run_benchmark.py · advance() · lines 78-97",
             tag="runs 200,000× per loop", highlight=hl)
    x = MARGIN + cw + 0.25
    w = SLIDE_W - MARGIN - x
    add_card(s, x, CONTENT_TOP, w, 1.65, "Line 85: ** (-1.5)", [
        "a pow() call per pair",
    ], accent=AMBER)
    add_card(s, x, CONTENT_TOP + 1.8, w, 1.95, "Lines 82-93", [
        "~24 float operations per pair; each result is a new heap object",
    ], accent=RED)
    add_card(s, x, CONTENT_TOP + 3.9, w, 1.6, "Lines 88-93", [
        "6 list item writes per pair",
    ], accent=RED)
    set_notes(s, """
        This is the whole of advance(), exactly as in the benchmark. The outer loop
        runs 20,000 times, and the inner loop over ten pairs is where all the time
        goes. Line 85 is the force factor. It uses the power operator with minus 1.5,
        which becomes a call into the C math library's pow function. That's the
        __ieee754_pow_fma frame. Lines 82 to 93 are about two dozen floating-point
        operations per pair. In C each one would be a single instruction on a
        register. In CPython, each result is a new heap-allocated float object, the
        operator is dispatched through binary_op1, and the old value is freed. Lines
        88 to 93 also write back into lists, which is the list_ass_item cost. None of
        this is a bad algorithm. It's the direct, correct way to write the physics.
    """)

    # 21 ─ root cause
    s = d.slide("Bottlenecks: root cause", sec)
    add_card(s, MARGIN, CONTENT_TOP + 0.1, 5.95, 3.1,
             "1 · Float boxing and operator dispatch", [
                 "Every float result is a heap object: PyFloat_FromDouble 2.51 %, "
                 "float_mul/add/sub 4.82 %, float_dealloc 2.01 %",
                 "Every operator is a generic, type-checked call: binary_op1 3.37 %",
             ], accent=RED)
    add_card(s, MARGIN + 6.18, CONTENT_TOP + 0.1, 5.95, 3.1,
             "2 · Interpreter work per operation", [
                 "_PyEval_EvalFrameDefault 29.40 %: bytecode dispatch for each step",
                 "List writes 4.17 %, pow() 1.08 %",
                 "The algorithm is already optimal: O(pairs) per step",
             ], accent=NAVY)
    add_rect(s, MARGIN, 4.85, CONTENT_W, 0.95, fill=NAVY, radius=0.06)
    add_text(s, "The arithmetic is trivial for a CPU. The cost is CPython's object model "
                "around every single operation.", MARGIN + 0.3, 4.9, CONTENT_W - 0.6,
             0.85, color=WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    set_notes(s, """
        So the root cause here is different from pyflate. In pyflate we had a bad
        algorithm and object churn that came from reimplementing a library. In nbody
        the algorithm is already optimal, a fixed amount of work per pair per step.
        The problem is the execution model. Every float is boxed as a heap object,
        every operator goes through a generic dispatch that checks types first, and
        every line is several bytecodes interpreted one by one. Together that's
        about 46 percent of the self-time in the interpreter, boxing and list
        writes. The important consequence is that this cost is structural. We can't
        remove it by writing better Python. We can only try to pay it fewer times,
        and that's exactly what our optimization attempts tried to do.
    """)

    # 22 ─ experiments
    s = d.slide("Optimization: four attempts", sec)
    add_table(s, [
        ["Attempt", "Change", "Result", "Verdict"],
        ["1 · numpy", "numpy arrays, vectorized over the 10 pairs", "848 ms",
         "75 % slower"],
        ["2 · sqrt()", "r2 ** -1.5 → dt / (r2 * sqrt(r2))", "495 ms", "2.5 % slower"],
        ["3 · flat indices", "integer indices into flat tuples", "579 ms", "20 % slower"],
        ["4 · unroll", "unpack bodies once, write out all 10 pairs", "439 ms",
         "9.1 % faster"],
    ], MARGIN, CONTENT_TOP + 0.1, [2.2, 6.1, 1.6, 2.23], row_h=0.6,
        aligns=[PP_ALIGN.LEFT, PP_ALIGN.LEFT, PP_ALIGN.RIGHT, PP_ALIGN.LEFT],
        colors={(1, 3): RED, (2, 3): RED, (3, 3): RED, (4, 3): GREEN},
        highlight_rows={4})
    add_text(s, "Baseline 483 ms ± 4 ms. All results are full calibrated pyperf runs.",
             MARGIN, CONTENT_TOP + 3.1, CONTENT_W, 0.45, color=MUTED)
    add_card(s, MARGIN, CONTENT_TOP + 3.6, CONTENT_W, 1.85, "Why the first three lost", [
        "numpy: per-call dispatch and scatter-add overhead is far larger than the math "
        "for only 5 bodies",
        "sqrt(): the global lookup and function call cost as much as the pow() it "
        "replaced; flat indices add more subscripts than they save",
    ], accent=RED)
    set_notes(s, """
        We tried four things, each aimed at a hotspot, and measured each one with a
        full pyperf run. First, numpy, the obvious answer for numeric Python. It was
        75 percent slower, because with only five bodies the arrays are tiny, and
        numpy's per-call overhead is much larger than the arithmetic it saves. Second,
        replacing the power operator with sqrt, to remove the pow call. Slightly
        slower, because looking up and calling sqrt costs about as much as the pow it
        replaced. Third, iterating with integer indices instead of unpacking tuples.
        20 percent slower, because it adds more subscript operations than it removes.
        The fourth one worked: manually unrolling the loop, 9.1 percent faster, above
        the 7 percent target. The lesson from the failures is that in CPython you win
        by executing fewer bytecodes, not by using a cleverer formula.
    """)

    # 23 ─ code comparison
    s = d.slide("Optimization: original vs. unrolled code", sec)
    left = snippet(NB_ORIG, (78, 97))
    right = snippet(NB_UNROLL, (94, 106), "...", (171, 175))
    # split the width in proportion to each panel's longest line
    lc, rc = (max(len(t) for _, t in side) for side in (left, right))
    usable = CONTENT_W - 0.25 - 2 * 0.32
    lw, rw = 0.32 + usable * lc / (lc + rc), 0.32 + usable * rc / (lc + rc)
    size = min(code_font_size(left, lw), code_font_size(right, rw))
    h_ = max(len(left), len(right)) * size * 1.27 / 72 + 0.58
    add_code(s, left, MARGIN, CONTENT_TOP, lw, size,
             "original · lines 78-97", tag="generic loops",
             highlight={79, 80, 94}, h=h_)
    add_code(s, right, MARGIN + lw + 0.25, CONTENT_TOP, rw, size,
             "optimized_unroll_experiment · lines 94-175", tag="10 pairs written out",
             tag_color=GREEN, highlight={95, 96, 97, 98, 99, 171, 172, 173, 174, 175},
             hl_color=HL_GOOD, h=h_)
    add_text(s, "Before the loop, all bodies are unpacked once (line 92: (p0, v0, m0), …, "
                "(p4, v4, m4) = bodies). The ** power call is unchanged.",
             MARGIN, CONTENT_TOP + h_ + 0.15, CONTENT_W, 0.8, color=INK)
    set_notes(s, """
        Here's what unrolling means concretely. On the left, the original: every
        timestep it loops over the pairs list, and every iteration destructures two
        nested tuples to get the positions, velocities and masses. That's highlighted
        at lines 79 and 80. The body loop at line 94 does the same again. On the
        right, the unrolled version. The five bodies are unpacked once before the
        loop even starts. Each timestep unpacks the five positions once, then the ten
        pairs are written out by hand, so there's no loop and no tuple unpacking per
        pair. You see pair zero-one here, and the other nine follow the same pattern.
        The five position updates at the end are also written out. We deliberately
        left the power operator alone, so we're measuring only the effect of removing
        loop and unpacking bytecodes.
    """)

    # 24 ─ results
    s = d.slide("Results: 9.1 % faster", sec)
    add_rect(s, MARGIN, CONTENT_TOP + 0.1, 4.1, 3.55, fill=PANEL, radius=0.05)
    add_text(s, "Mean ± std dev (pyperf)", MARGIN + 0.2, CONTENT_TOP + 0.25, 3.7, 0.4,
             color=MUTED, align=PP_ALIGN.CENTER)
    for i, (val, lab, col) in enumerate([("483 ms", "original, ± 4 ms", INK),
                                         ("439 ms", "unrolled, ± 2 ms", GREEN)]):
        yy = CONTENT_TOP + 0.75 + i * 1.35
        tb, tf = _box(s, MARGIN + 0.2, yy, 3.7, 0.75, name="STAT")
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = val
        _font(r, TITLE_FONT, 40, col)
        add_text(s, lab, MARGIN + 0.2, yy + 0.72, 3.7, 0.4, color=MUTED,
                 align=PP_ALIGN.CENTER)
    add_table(s, [
        ["Counter", "Original", "Unrolled", "Change"],
        ["Instructions", "260.5 G", "244.0 G", "6.8 % fewer"],
        ["Cycles", "107.7 G", "100.0 G", "7.7 % fewer"],
        ["IPC", "2.42", "2.44", "about the same"],
        ["Cache-miss rate", "2.83 %", "3.25 %", "slightly worse"],
        ["Branch-miss rate", "0.49 %", "0.49 %", "unchanged"],
    ], MARGIN + 4.35, CONTENT_TOP + 0.1, [2.4, 1.55, 1.6, 2.23], row_h=0.59,
        aligns=[PP_ALIGN.LEFT, PP_ALIGN.RIGHT, PP_ALIGN.RIGHT, PP_ALIGN.LEFT],
        colors={(1, 3): GREEN, (2, 3): GREEN, (3, 3): MUTED, (4, 3): RED,
                (5, 3): MUTED})
    add_card(s, MARGIN, CONTENT_TOP + 3.9, CONTENT_W, 1.5,
             "Above the ≥ 7 % target, but only just", [
                 "Cache misses rose slightly because the unrolled function body is much "
                 "larger. The 7-8 % drop in instructions and cycles still wins overall.",
             ])
    set_notes(s, """
        The result: 483 milliseconds down to 439, 9.1 percent faster, which passes the
        7 percent target. The counters tell a consistent story. We execute 6.8 percent
        fewer instructions and 7.7 percent fewer cycles, and IPC is unchanged. So we
        didn't make anything run more efficiently. We just removed work, the loop and
        unpacking bytecodes. The cache-miss rate went up slightly, because the
        unrolled function is a lot more bytecode and code objects, but the reduction
        in instructions clearly outweighs it. Compare that with pyflate's 244 times.
        The difference is the whole point of this part. When the cost is the execution
        model itself, software can only shave the edges.
    """)

    # 25 ─ flame graphs before / after
    s = d.slide("Results: flame graphs before and after", sec)
    half = (CONTENT_W - 0.3) / 2
    for i, (key, label, lines, col) in enumerate([
        ("nb_orig_half", "**Before**: original",
         ["41.7 %  _PyEval_EvalFrameDefault", "5.3 %  binary_op1",
          "3.6 %  PyFloat_FromDouble"], NAVY),
        ("nb_opt_half", "**After**: unrolled",
         ["36.2 %  _PyEval_EvalFrameDefault", "5.7 %  binary_op1",
          "3.8 %  PyFloat_FromDouble"], GREEN),
    ]):
        x = MARGIN + i * (half + 0.3)
        add_text(s, label, x, CONTENT_TOP + 0.05, half, 0.45)
        _, (px, py, pw, ph) = add_image_fit(s, a[key], x, CONTENT_TOP + 0.6, half, 2.8,
                                            border=True)
        add_rect(s, x, py + ph + 0.2, 0.06, 1.4, fill=col)
        add_bullets(s, lines, x + 0.15, py + ph + 0.15, half - 0.2, 1.6, space_after=4)
    add_text(s, "Same shape: unrolling cut the amount of work (~45K → ~42K samples), not "
                "the kind of work.", MARGIN, 6.35, CONTENT_W, 0.45, color=MUTED)
    set_notes(s, """
        The flame graphs make the same point visually. Before and after look almost
        the same: the interpreter loop at the base with binary_op1 and the float
        functions next to it. The interpreter loop's share went down from about 42 to
        36 percent, because we removed loop and unpacking bytecodes. Float creation
        and binary_op1 actually take a slightly larger share now. That's not because
        they got slower. They're the part we couldn't remove, so they're a bigger
        fraction of a smaller total. Total samples dropped from about 45 thousand to
        42 thousand, in line with the 9 percent. So unrolling changed how much work we
        do, not what kind of work. Every float operation is still boxed and
        dispatched, and that's what the hardware proposal attacks.
    """)

    # 26 ─ hardware concept
    s = d.slide("Hardware proposal: Gravity Force Pipeline (GFP)", sec)
    add_key_facts(s, a["nb_block"], [
        ("10 lanes", "one per body pair, all in parallel"),
        ("35 doubles", "all state on-chip, ~280 bytes"),
        ("1 start", "per advance() call; all n steps run on-chip"),
    ])
    set_notes(s, """
        The Gravity Force Pipeline is a SystemVerilog block that runs the entire
        advance call in hardware, all 20,000 timesteps. On the left, software loads
        the state once: positions, velocities and masses of five bodies, 35 doubles in
        total, plus dt and the iteration count. Then it pulses start. On the right,
        the state sits in a register file. Ten force lanes, one per pair, compute the
        distance, r squared, the reciprocal square root cubed and the velocity deltas,
        all in parallel. This is our unrolling idea taken to its end: instead of
        writing the ten pairs out in Python, they're ten pieces of wired hardware. A
        fixed adder tree accumulates the deltas per body, since each body is in
        exactly four pairs. The integrate stage updates positions, and the sequencer
        loops back without the CPU, which only reads the result when done asserts.
    """)

    # 27 ─ hardware trade-offs
    s = d.slide("Hardware proposal: benefit and trade-offs", sec)
    qw, qh = (CONTENT_W - 0.25) / 2, 2.55
    quads = [
        ("Performance", TEAL, [
            "No interpreter and no boxing: dx = x1 - x2 is a subtractor, not a heap object",
            "~12-cycle critical path per timestep (an estimate, not measured)",
        ]),
        ("Area", NAVY, [
            "10 lanes = 10× one lane's FP units (rsqrt³ is the largest)",
            "Lanes grow as O(N²): large N needs shared, time-multiplexed lanes",
        ]),
        ("Power", GREEN, [
            "Higher instantaneous power: 10 FP pipelines every cycle",
            "Much shorter active time, with no fetch or dispatch overhead",
        ]),
        ("Scope and limits", AMBER, [
            "report_energy() stays in software",
            "Not tapeout-ready: no FP exception handling, reset sync or CDC",
        ]),
    ]
    for i, (head, col, items) in enumerate(quads):
        x = MARGIN + (i % 2) * (qw + 0.25)
        y = CONTENT_TOP + 0.1 + (i // 2) * (qh + 0.2)
        add_card(s, x, y, qw, qh, head, items, accent=col)
    set_notes(s, """
        Why is this worth it when software only got 9 percent? Because hardware
        removes the cost itself rather than paying it fewer times. A subtraction is a
        subtractor, not a heap allocation plus a type dispatch. Each timestep's
        critical path is the force-lane pipeline, about 12 cycles, dominated by the
        reciprocal square root unit. I want to be clear that this is an architectural
        estimate. We wrote and syntax-checked the RTL but didn't synthesize or time it.
        The main trade-off is area. Ten parallel lanes means ten sets of
        floating-point units, and the number of pairs grows with the square of the
        number of bodies. That's fine for five bodies, but a bigger simulation would
        share fewer lanes over time. Power per cycle is higher, but the active time is
        far shorter. And to wrap up: pyflate's lesson was to fix the algorithm, and
        nbody's is that when the execution model is the bottleneck, that's where
        hardware pays off.
    """)


# ------------------------------------------------------------ closing ----
REPO = "https://github.com/ChrisShakkour/HW-SW-project-00460882"


def slide_thanks(d):
    s = d.slide(section="Thank you")
    add_rect(s, 0, 0, 0.32, SLIDE_H, fill=NAVY)
    add_rect(s, 0.32, 0, 0.06, SLIDE_H, fill=TEAL)
    tb, tf = _box(s, 1.2, 0.9, 10.8, 0.8, name="TITLE")
    tf.vertical_anchor = MSO_ANCHOR.BOTTOM
    r = tf.paragraphs[0].add_run()
    r.text = "Thank you"
    _font(r, TITLE_FONT, TITLE_SIZE, NAVY)
    add_rect(s, 1.25, 1.82, 1.1, 0.05, fill=TEAL)
    add_text(s, "Chris Shakkour · Razan Jiryis", 1.2, 2.0, 10.5, 0.5, color=MUTED)

    add_text(s, "**Resources**", 1.2, 2.85, 10.5, 0.45)
    add_bullets(s, [
        f"Project repository: {{{{github.com/ChrisShakkour/HW-SW-project-00460882|{REPO}}}}}",
        "Reports: pyflate/report_pyflate.docx · nbody/report_nbody.docx",
        "RTL: pyflate/hardware/hfdu.sv · nbody/hardware/gravity_force_pipeline.sv",
        "Benchmarks: {{pyperformance|https://github.com/python/pyperformance}} "
        "(bm_pyflate, bm_nbody)",
        "Flame graphs: {{Brendan Gregg's FlameGraph|"
        "https://github.com/brendangregg/FlameGraph}} scripts",
    ], 1.2, 3.35, 11.2, 3.3, space_after=10)
    set_notes(s, """
        Thank you for listening. Everything we showed is in the GitHub repository on
        the slide: the profiling data, both flame graphs for every variant, the
        original and optimized benchmark code, the full reports, and the
        SystemVerilog for both accelerators. The benchmarks come from pyperformance,
        and the flame graphs were made with Brendan Gregg's FlameGraph scripts. We're
        happy to take questions.
    """)


# ------------------------------------------------------------- checks ----
def check_deck(path):
    prs = Presentation(path)
    sw, sh = prs.slide_width, prs.slide_height
    problems = []
    for idx, slide in enumerate(prs.slides, start=1):
        if not slide.has_notes_slide or not slide.notes_slide.notes_text_frame.text.strip():
            problems.append(f"slide {idx}: missing speaker notes")
        for shp in slide.shapes:
            if shp.left < 0 or shp.top < 0 or shp.left + shp.width > sw + Emu(10) \
                    or shp.top + shp.height > sh + Emu(10):
                problems.append(f"slide {idx}: '{shp.name}' extends past slide edge")
            if shp.name == "TITLE":
                for p in shp.text_frame.paragraphs:
                    for r in p.runs:
                        if (r.font.name, r.font.size) != (TITLE_FONT, Pt(TITLE_SIZE)):
                            problems.append(f"slide {idx}: title run '{r.text}' off-style")
            texts = []
            if shp.name == "BODY":
                texts = [shp.text_frame]
            elif shp.name == "BODY_TABLE":
                texts = [c.text_frame for row in shp.table.rows for c in row.cells]
            for tf in texts:
                for p in tf.paragraphs:
                    for r in p.runs:
                        if (r.font.name, r.font.size) != (BODY_FONT, Pt(BODY_SIZE)):
                            problems.append(f"slide {idx}: body run '{r.text}' off-style")
    return len(prs.slides), problems


def main():
    a = build_assets()
    d = Deck()
    slide_title(d)
    pyflate_slides(d, a)
    nbody_slides(d, a)
    slide_thanks(d)
    d.prs.save(OUT)
    n, problems = check_deck(OUT)
    print(f"wrote {OUT} ({n} slides)")
    for p in problems:
        print("  CHECK:", p)
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
