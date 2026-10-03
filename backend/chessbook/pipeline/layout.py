"""Stage: pages -> layout -> reading order -> paragraphs.

This is where charter #5 Types B and C are solved.  We never trust the PDF's
internal text ordering; we rebuild it from geometry:

  * gutter detection -> column segmentation
  * column classification -> *reading* layout vs *parallel* (moves | prose) layout
  * running-head / page-number suppression
  * de-hyphenation and paragraph assembly
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Literal

from ..models import Paragraph, SourceRef, TextLine
from . import notation as nz
from .extract import RawPage

LayoutMode = Literal["single", "columns", "parallel"]

HEADER_BAND = 0.10  # fraction of page height considered a running-head band
FOOTER_BAND = 0.08
MIN_GUTTER_PT = 9.0
MIN_GUTTER_FRACTION = 0.018
MAX_CROSS_FRACTION = 0.28


@dataclass
class Column:
    index: int
    x0: float
    x1: float
    lines: list[TextLine] = field(default_factory=list)
    char_count: int = 0
    move_tokens: int = 0
    total_tokens: int = 0

    @property
    def move_score(self) -> float:
        return self.move_tokens / self.total_tokens if self.total_tokens else 0.0

    @property
    def prose_score(self) -> float:
        return 1.0 - self.move_score


@dataclass
class LaidOutPage:
    page: int
    width: float
    height: float
    mode: LayoutMode = "single"
    columns: list[Column] = field(default_factory=list)
    ordered: list[TextLine] = field(default_factory=list)
    body: list[TextLine] = field(default_factory=list)
    rows: list[list[TextLine]] = field(default_factory=list)
    #: every line, including the ones later tagged as headers/footers/folios
    all_lines: list[TextLine] = field(default_factory=list)
    removed: list[TextLine] = field(default_factory=list)

    @property
    def column_count(self) -> int:
        return len(self.columns)

    def prose_text(self) -> str:
        return "\n".join(ln.text for ln in self.body)


# ---------------------------------------------------------------------------
# Page analysis
# ---------------------------------------------------------------------------


def analyze_page(page: RawPage) -> LaidOutPage:
    lines = [ln for ln in page.lines if ln.text.strip()]
    out = LaidOutPage(page=page.number, width=page.width, height=page.height)
    if not lines:
        return out

    # Note: we deliberately do *not* discard the top/bottom bands here.  A real
    # running head is recognised by *repetition across pages*, not by position:
    # plenty of books put a game title right at the top of the page.
    #
    # Hanging move numbers are stitched back together *before* gutter analysis,
    # otherwise the margin column looks like a genuine second column and the
    # page would be read column-major (all the numbers, then all the moves).
    body = merge_hanging_margin(sorted(lines, key=lambda l: (round(l.bbox[1], 1), l.bbox[0])))
    columns = detect_columns(body, page.width)
    out.columns = columns
    out.mode = classify_mode(columns)
    ordered = order_lines(body, columns, out.mode)
    out.ordered = ordered
    out.body = ordered
    out.rows = build_rows(ordered)
    return out


def analyze_pages(pages: list[RawPage]) -> list[LaidOutPage]:
    laid_out = [analyze_page(p) for p in pages]
    mark_running_heads(laid_out)
    for lp in laid_out:
        lp.all_lines = list(lp.ordered)
        keep = [ln for ln in lp.ordered if ln.role in ("body", "caption", "heading")]
        lp.removed = [ln for ln in lp.ordered if ln not in keep]
        lp.ordered = keep
        lp.body = keep
        lp.rows = build_rows(keep)
    return laid_out


def _in_dead_band(line: TextLine, page_height: float) -> bool:
    y0 = line.bbox[1]
    y1 = line.bbox[3]
    return y1 <= page_height * HEADER_BAND or y0 >= page_height * (1 - FOOTER_BAND)


# ---------------------------------------------------------------------------
# Column detection
# ---------------------------------------------------------------------------


def detect_columns(lines: list[TextLine], page_width: float) -> list[Column]:
    if len(lines) < 4:
        return [Column(0, min(l.bbox[0] for l in lines), max(l.bbox[2] for l in lines), list(lines))]

    remaining = list(lines)
    gutters: list[tuple[float, float]] = []
    for _ in range(2):
        found = _find_gutters(remaining, page_width)
        if not found:
            break
        gutters = found
        kept: list[TextLine] = []
        for ln in remaining:
            if any(ln.bbox[0] < g0 - 0.5 and ln.bbox[2] > g1 + 0.5 for g0, g1 in gutters):
                continue
            kept.append(ln)
        if len(kept) < max(4, 0.4 * len(lines)):
            break
        remaining = kept

    if not gutters:
        col = Column(0, min(l.bbox[0] for l in lines), max(l.bbox[2] for l in lines), list(lines))
        _profile(col)
        return [col]

    edges = [-1e9, *sum(([g0, g1] for g0, g1 in gutters), []), 1e9]
    cols: list[Column] = []
    for i in range(0, len(edges) - 1, 2):
        lo, hi = edges[i], edges[i + 1]
        members = [ln for ln in lines if ln.bbox[0] >= lo - 0.5 and ln.bbox[0] < hi]
        if not members:
            continue
        col = Column(
            index=len(cols),
            x0=min(l.bbox[0] for l in members),
            x1=max(l.bbox[2] for l in members),
            lines=members,
        )
        _profile(col)
        cols.append(col)

    if not cols:
        col = Column(0, min(l.bbox[0] for l in lines), max(l.bbox[2] for l in lines), list(lines))
        _profile(col)
        return [col]

    cols = _drop_speck_columns(cols)
    for i, col in enumerate(cols):
        col.index = i
    return cols


def _find_gutters(lines: list[TextLine], page_width: float) -> list[tuple[float, float]]:
    """Find vertical whitespace corridors that no line crosses."""
    if not lines:
        return []
    left = min(l.bbox[0] for l in lines)
    right = max(l.bbox[2] for l in lines)
    span = right - left
    if span < 80:
        return []

    # Lines whose width is far from the median must not decide the columns:
    # a narrow hanging margin cannot *be* a column, and a full-width heading
    # spanning both columns cannot *bridge* the gutter between them.
    widths = sorted(l.bbox[2] - l.bbox[0] for l in lines)
    median_width = widths[len(widths) // 2] or 1.0
    usable = [
        l for l in lines
        if 0.35 * median_width <= (l.bbox[2] - l.bbox[0]) <= 1.35 * median_width
    ]
    if len(usable) >= max(4, 0.4 * len(lines)):
        lines = usable
    bins = max(80, min(600, int(span / 2.0)))
    step = span / bins
    coverage = [0.0] * bins
    heights: list[float] = []
    for ln in lines:
        h = max(1.0, ln.bbox[3] - ln.bbox[1])
        heights.append(h)
        a = int(max(0, min(bins - 1, (ln.bbox[0] - left) / step)))
        b = int(max(0, min(bins - 1, (ln.bbox[2] - left) / step)))
        for i in range(a, b + 1):
            coverage[i] += h
    total = sum(heights) or 1.0
    min_gutter = max(MIN_GUTTER_PT, MIN_GUTTER_FRACTION * page_width)

    runs: list[tuple[float, float]] = []
    i = 0
    while i < bins:
        if coverage[i] > 0.012 * total:
            i += 1
            continue
        j = i
        while j < bins and coverage[j] <= 0.012 * total:
            j += 1
        width = (j - i) * step
        g0, g1 = left + i * step, left + j * step
        if width >= min_gutter and g0 > left + 4 and g1 < right - 4:
            runs.append((g0, g1))
        i = j
    return runs


def _drop_speck_columns(cols: list[Column]) -> list[Column]:
    if len(cols) <= 2:
        return cols
    total = sum(c.char_count for c in cols) or 1
    kept = [c for c in cols if c.char_count >= 0.04 * total]
    if len(kept) < 2:
        return cols
    return kept


def _profile(col: Column) -> None:
    text = " ".join(ln.text for ln in col.lines)
    moves, total = nz.token_density(text)
    col.char_count = len(text.strip())
    col.move_tokens = moves
    col.total_tokens = total


def classify_mode(cols: list[Column]) -> LayoutMode:
    if len(cols) <= 1:
        return "single"
    if len(cols) == 2:
        a, b = cols[0].move_score, cols[1].move_score
        if (a >= 0.45 and b <= 0.25) or (b >= 0.45 and a <= 0.25):
            return "parallel"
    return "columns"


# ---------------------------------------------------------------------------
# Reading order
# ---------------------------------------------------------------------------


def order_lines(lines: list[TextLine], cols: list[Column], mode: LayoutMode) -> list[TextLine]:
    """Assign each line a column and produce a reading order.

    ``columns`` -> column-major (correct for prose laid out in newspaper
    columns).  ``parallel`` -> row-band order (moves on one side, commentary on
    the other must be interleaved by vertical position, not concatenated).
    """
    col_of: dict[int, int] = {}
    for col in cols:
        for ln in col.lines:
            col_of[id(ln)] = col.index

    for ln in lines:
        ln.column = col_of.get(id(ln), 0)

    if mode == "columns" and len(cols) > 1:
        return sorted(lines, key=lambda l: (l.column, round(l.bbox[1], 1), l.bbox[0]))
    # single / parallel: strict vertical order, left-to-right within a row band
    return sorted(lines, key=lambda l: (round(l.bbox[1], 1), l.bbox[0]))


_MARGIN_NUMBER_RE = re.compile(r"^\s*\d{1,3}\s*\.{1,3}\s*$")


def merge_hanging_margin(ordered: list[TextLine], max_gap: float = 44.0) -> list[TextLine]:
    """Re-join a move number that sits in a hanging margin with its moves.

    Plenty of chess books set move numbers in a separate left-hand column:
    ``1.`` at the far left and ``e4    e5`` indented beside it.  Extracted as two
    separate lines, a move number on its own is useless, so we glue the pair back
    together using vertical overlap.
    """
    merged: list[TextLine] = []
    used: set[int] = set()
    for i, line in enumerate(ordered):
        if i in used:
            continue
        if not _MARGIN_NUMBER_RE.match(line.text):
            merged.append(line)
            continue
        for j, other in enumerate(ordered):
            if j <= i or j in used:
                continue
            if other.bbox[0] < line.bbox[2]:
                continue
            if other.bbox[0] - line.bbox[2] > max_gap:
                continue
            overlap = min(line.bbox[3], other.bbox[3]) - max(line.bbox[1], other.bbox[1])
            height = max(1e-6, min(line.bbox[3] - line.bbox[1], other.bbox[3] - other.bbox[1]))
            if overlap < 0.5 * height:
                continue
            line = line.model_copy(
                update={
                    "text": f"{line.text.strip()} {other.text.strip()}",
                    "bbox": [line.bbox[0], min(line.bbox[1], other.bbox[1]),
                             other.bbox[2], max(line.bbox[3], other.bbox[3])],
                }
            )
            used.add(j)
            break
        merged.append(line)
    return merged


def build_rows(ordered: list[TextLine], overlap: float = 0.45) -> list[list[TextLine]]:
    """Group lines whose vertical extents overlap -- a visual 'row band'."""
    rows: list[list[TextLine]] = []
    current: list[TextLine] = []
    cur_bottom: float | None = None
    for ln in ordered:
        top, bottom = ln.bbox[1], ln.bbox[3]
        if cur_bottom is None:
            current = [ln]
            cur_bottom = bottom
            continue
        h = max(1e-6, bottom - top)
        inter = min(cur_bottom, bottom) - max(current[-1].bbox[1], top)
        if inter > overlap * min(h, max(1e-6, cur_bottom - current[-1].bbox[1])):
            current.append(ln)
            cur_bottom = max(cur_bottom, bottom)
        else:
            rows.append(current)
            current = [ln]
            cur_bottom = bottom
    if current:
        rows.append(current)
    for row in rows:
        row.sort(key=lambda l: l.bbox[0])
    return rows


# ---------------------------------------------------------------------------
# Running heads, footers, page numbers
# ---------------------------------------------------------------------------

_PAGE_NUM_RE = re.compile(r"^\s*(?:page\s+)?[-–—\.]?\s*(\d{1,4}|[ivxlcdmIVXLCDM]{1,7})\s*[-–—\.]?\s*$")


def mark_running_heads(
    pages: list[LaidOutPage],
    min_share: float = 0.3,
    min_pages: int = 2,
) -> None:
    """Tag running heads, footers and folios.

    Identification is by *repetition across pages*, never by position alone: a
    book may legitimately start a chapter title -- or even a game -- at the very
    top of the page.  Folios are the exception: a line in the bottom band that
    is nothing but a page number is always a page number.
    """
    total = len(pages)
    if total == 0:
        return
    cut = max(min_pages, int(min_share * total))

    top: dict[str, int] = {}
    bottom: dict[str, int] = {}
    keys: dict[tuple[int, int], str] = {}

    for lp in pages:
        head_lines = [ln for ln in lp.ordered if ln.bbox[3] <= lp.height * HEADER_BAND]
        foot_lines = [ln for ln in lp.ordered if ln.bbox[1] >= lp.height * (1 - FOOTER_BAND)]
        for band, counter in ((head_lines, top), (foot_lines, bottom)):
            grouped: list[list[TextLine]] = []
            for ln in sorted(band, key=lambda l: l.bbox[1]):
                if grouped and abs(grouped[-1][-1].bbox[1] - ln.bbox[1]) <= 8:
                    grouped[-1].append(ln)
                else:
                    grouped.append([ln])
            for group in grouped:
                key = " ".join(_normalize_head(ln.text) or "<num>" for ln in group)
                for ln in group:
                    keys[(lp.page, id(ln))] = key
                counter[key] = counter.get(key, 0) + 1

    for lp in pages:
        for ln in lp.ordered:
            in_head_band = ln.bbox[3] <= lp.height * HEADER_BAND
            in_foot_band = ln.bbox[1] >= lp.height * (1 - FOOTER_BAND)
            if (in_head_band or in_foot_band) and _PAGE_NUM_RE.match(ln.text):
                ln.role = "page_number"
                continue
            if not in_head_band and not in_foot_band:
                continue
            key = keys.get((lp.page, id(ln)))
            if not key:
                continue
            if in_head_band and top.get(key, 0) >= cut:
                ln.role = "header"
            elif in_foot_band and bottom.get(key, 0) >= cut:
                ln.role = "footer"


def _normalize_head(text: str) -> str:
    t = re.sub(r"\d+", "#", text.strip().lower())
    t = re.sub(r"\s+", " ", t)
    return t if len(t) >= 3 else ""


# ---------------------------------------------------------------------------
# Paragraph assembly
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"[.!?…][\"')\]]?$")


def merge_paragraphs(page: LaidOutPage) -> list[Paragraph]:
    """Group ordered lines into prose paragraphs / headings / move runs."""
    lines = [ln for ln in page.ordered if ln.role in ("body", "caption")]
    if not lines:
        return []

    body_size = _body_font_size(lines)
    column_left = _column_left_edges(lines)
    column_width = _column_widths(lines, column_left)
    paragraphs: list[Paragraph] = []
    buf: list[TextLine] = []

    def flush(kind: str, level: int = 0) -> None:
        nonlocal buf
        if not buf:
            return
        text = _join_lines(buf)
        if text.strip():
            paragraphs.append(
                Paragraph(
                    index=len(paragraphs),
                    page=page.page,
                    text=text,
                    bbox=[
                        round(min(l.bbox[0] for l in buf), 2),
                        round(min(l.bbox[1] for l in buf), 2),
                        round(max(l.bbox[2] for l in buf), 2),
                        round(max(l.bbox[3] for l in buf), 2),
                    ],
                    column=buf[0].column,
                    kind=kind,  # type: ignore[arg-type]
                    level=level,
                    source=SourceRef(
                        page=page.page,
                        bbox=[
                            round(min(l.bbox[0] for l in buf), 2),
                            round(min(l.bbox[1] for l in buf), 2),
                            round(max(l.bbox[2] for l in buf), 2),
                            round(max(l.bbox[3] for l in buf), 2),
                        ],
                        page_width=page.width,
                        page_height=page.height,
                    ),
                )
            )
        buf = []

    for ln in lines:
        is_heading = _is_heading(ln, body_size)
        is_move_run = _is_move_run(ln)
        left = column_left.get(ln.column, min(l.bbox[0] for l in lines))
        indent = ln.bbox[0] - left

        if is_heading:
            flush("prose")
            buf = [ln]
            flush("heading", level=_heading_level(ln, body_size))
            continue

        if not buf:
            buf = [ln]
            continue

        prev = buf[-1]
        prev_left = column_left.get(prev.column, left)
        prev_indent = prev.bbox[0] - prev_left
        prev_move = _is_move_run(prev)
        short_run = (
            column_width.get(ln.column, 1e9) > 0
            and _is_short_line(ln, column_width.get(ln.column, 1e9))
            and _is_short_line(prev, column_width.get(prev.column, 1e9))
        )
        starts_new = (
            short_run
            # contents / index lines ("CHAPTER 2 ... 5") are their own blocks
            or _is_leader_line(ln)
            or (is_move_run != prev_move)
            # only a *deeper* indent opens a new block; a wrapped line that
            # returns to the base indent is still part of the same paragraph
            or indent > prev_indent + 6.0
            or (prev.bbox[3] - ln.bbox[1] > 1.9 * max(1.0, prev.font_size))
            or (prev.font_size - ln.font_size > 1.6)
            or (_is_caption(prev) != _is_caption(ln))
        )
        if starts_new:
            kind = "caption" if _is_caption(ln) else ("move_run" if is_move_run else "prose")
            flush(kind)
            buf = [ln]
        else:
            buf.append(ln)

    flush("move_run" if buf and _is_move_run(buf[-1]) else "prose")
    return paragraphs


def _join_lines(lines: list[TextLine]) -> str:
    out = ""
    for i, ln in enumerate(lines):
        piece = ln.text
        if i > 0:
            if out.endswith("-") and not out.endswith("--") and piece[:1].islower():
                out = out[:-1]
            elif out and not out.endswith((" ", "-", "(", "[", "{", "/")) and piece[:1] not in ".,;:)]}":
                out += " "
        out += piece
    return re.sub(r"[ \t]+", " ", out).strip()


def _body_font_size(lines: list[TextLine]) -> float:
    sizes = [ln.font_size for ln in lines if ln.role == "body"]
    if not sizes:
        return 10.0
    try:
        return statistics.median(sizes)
    except statistics.StatisticsError:  # pragma: no cover
        return 10.0


_LEADER_LINE_RE = re.compile(r"^\S.{2,120}?[\s.]{2,}\d{1,4}\s*$")


def _is_leader_line(line: TextLine) -> bool:
    """A table-of-contents / index line: text, a dot leader, then a number."""
    return bool(_LEADER_LINE_RE.match(line.text.strip()))


def _is_short_line(line: TextLine, column_width: float) -> bool:
    """A line that uses well under its column's measure."""
    return (line.bbox[2] - line.bbox[0]) < 0.62 * column_width


def _column_widths(lines: list[TextLine], lefts: dict[int, float]) -> dict[int, float]:
    out: dict[int, float] = {}
    for line in lines:
        col = line.column
        out[col] = max(out.get(col, 0.0), line.bbox[2] - lefts.get(col, line.bbox[0]))
    return out


def _column_left_edges(lines: list[TextLine]) -> dict[int, float]:
    """Left edge of each text column.

    Using the minimum rather than the median matters: in chess books the move
    block is often indented relative to the running prose, and *that* offset is
    exactly what distinguishes a main line from an indented variation.
    """
    out: dict[int, list[float]] = {}
    for ln in lines:
        if ln.role in ("body", "caption"):
            out.setdefault(ln.column, []).append(ln.bbox[0])
    return {col: min(vals) for col, vals in out.items()}


def _is_heading(line: TextLine, body_size: float) -> bool:
    if line.bold and len(line.text.strip()) < 120:
        return True
    if line.font_size >= body_size + 1.6 and len(line.text.strip()) < 120:
        return True
    if line.role == "heading":
        return True
    return False


def _heading_level(line: TextLine, body_size: float) -> int:
    if line.font_size >= body_size + 5:
        return 0
    if line.font_size >= body_size + 2.6:
        return 1
    return 2


def _is_move_run(line: TextLine) -> bool:
    """Does this line *read* like a move list rather than like prose?

    A printed move number is the strongest evidence; a run of bare SAN on a
    short line is not, because OCR and hyphenation produce plenty of short lines
    that merely look like notation.
    """
    text = normalize_for_moves(line.text)
    moves, total = nz.token_density(text)
    numbered = len(nz.MOVE_TOKEN_RE.findall(text))
    if numbered >= 1 and moves / total >= 0.35:
        return True
    if moves == total and total >= 1:
        # a short line that is *entirely* notation ("17. Rd8#", "Nf3 c5")
        return True
    if moves / total >= 0.8 and total >= 6:
        return True
    return False


_CAPTION_WORD = re.compile(r"^(?:fig(?:ure)?|diagram|chart|example|board)\b", re.I)


def _is_caption(line: TextLine) -> bool:
    """A caption is a short line that *starts* with a caption word.

    Matching the bare word "position" would classify half the prose in a chess
    book as a caption, so the label must come first and the line must be short.
    """
    t = line.text.strip()
    if len(t) > 96:
        return False
    return bool(_CAPTION_WORD.match(t))


def normalize_for_moves(text: str) -> str:
    return nz.normalize_figurines(text)