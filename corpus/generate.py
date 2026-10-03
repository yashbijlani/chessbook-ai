#!/usr/bin/env python3
"""Generate the test corpus.

Every PDF here is synthesised so that we control *which* structural problem the
document contains (charter #5 types A-H, charter #29 test list).  Nothing in the
pipeline may special-case these files: they are inputs, not fixtures with
special handling.

    python corpus/generate.py            # writes corpus/pdf/*.pdf
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import games as G  # noqa: E402

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "pdf"
ASSETS = ROOT.parent / "tests" / "assets"

FONT_FILES = {
    "serif": str(ASSETS / "DejaVuSerif.ttf"),
    "bold": str(ASSETS / "DejaVuSerif-Bold.ttf"),
    "sans": str(ASSETS / "DejaVuSans.ttf"),
    "mono": str(ASSETS / "DejaVuSansMono.ttf"),
}
_FONT_CACHE: dict[str, pymupdf.Font] = {}
_ALIAS = {"serif": "SerifText", "bold": "BoldText", "sans": "SansText", "mono": "MonoText"}


_PAGES: list["Page"] = []


def save(doc: pymupdf.Document, path: Path) -> None:
    """Flush every TextWriter belonging to ``doc``, then save."""
    for pg in _PAGES:
        if pg.doc is doc:
            pg.flush()
    doc.save(path)
    doc.close()
    _PAGES.clear()


def load_font(style: str) -> pymupdf.Font:
    if style not in _FONT_CACHE:
        _FONT_CACHE[style] = pymupdf.Font(fontfile=FONT_FILES[style])
    return _FONT_CACHE[style]


def text_width(value: str, style: str, size: float) -> float:
    return load_font(style).text_length(value, fontsize=size)


FIG_WHITE = {"K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙"}
FIG_BLACK = {"K": "♚", "Q": "♛", "R": "♜", "B": "♝", "N": "♞", "P": "♟"}

INTRO = (
    "The King's Indian Defence has been a mainstay of Black's repertoire since "
    "the 1930s. Its appeal lies in the sudden centralised counterattack it "
    "allows: Black concedes the centre in order to strike at the base of White's "
    "position with the thematic advance ...d5, supported by the queen knight."
)
INTRO2 = (
    "Morphy's combination is a perfect illustration of what an open file is "
    "worth when every piece on it belongs to one side. The rook sacrifice "
    "removes Black's last defender of the seventh rank, after which the queen "
    "sacrifice drives the knight away and the back rank collapses."
)


# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------


class Page:
    """A thin wrapper that makes laying out synthetic pages pleasant."""

    def __init__(self, doc: pymupdf.Document, width: float = 420, height: float = 620):
        # PyMuPDF invalidates outstanding Page handles whenever a new page is
        # added, so every writer must be flushed before we ask for another page.
        for other in _PAGES:
            if other.doc is doc and not other._written:
                other.flush()
        self.doc = doc
        self.page = doc.new_page(width=width, height=height)
        self.width = width
        self.height = height
        self.y = 56.0
        self._fonts: dict[str, str] = {}
        # TextWriter (rather than insert_text) keeps a correct ToUnicode map, so
        # figurine glyphs survive extraction -- exactly what a digital chess book
        # produced by a real typesetter looks like.
        self._writer = pymupdf.TextWriter(self.page.rect)
        self._written = False
        _PAGES.append(self)

    def font(self, key: str) -> str:
        if key not in self._fonts:
            self._fonts[key] = _ALIAS[key]
        return self._fonts[key]

    def text(self, value: str, x: float, y: float, *, size: float = 9.5, style: str = "serif") -> None:
        self._writer.append(
            (x, y), value, font=load_font(style), fontsize=size
        )

    def line(
        self, value: str, x: float = 54, *, size: float = 9.5, style: str = "serif",
        leading: float = 13.0,
    ) -> float:
        self.text(value, x, self.y, size=size, style=style)
        self.y += leading
        return self.y

    def wrapped(
        self, value: str, x: float = 54, width: float = 312, *, size: float = 9.5,
        style: str = "serif", leading: float = 12.5, first_indent: float = 0.0,
    ) -> float:
        line = ""
        indent = first_indent
        for word in value.split():
            trial = f"{line} {word}".strip()
            if line and text_width(trial, style, size) > (width - indent):
                self.line(line, x + indent, size=size, style=style, leading=leading)
                indent = 0.0
                line = word
            else:
                line = trial
        if line:
            self.line(line, x + indent, size=size, style=style, leading=leading)
        return self.y

    def space(self, amount: float = 10.0) -> None:
        self.y += amount

    def board(self, x: float, y: float, size: float = 132.0, label: str | None = None) -> None:
        """Draw a vector chessboard (8x8 alternating fills + piece discs)."""
        cell = size / 8
        shape = self.page.new_shape()
        for r in range(8):
            for c in range(8):
                fill = (0.87, 0.84, 0.76) if (r + c) % 2 else (0.55, 0.45, 0.33)
                shape.draw_rect(
                    pymupdf.Rect(
                        x + c * cell, y + r * cell, x + (c + 1) * cell, y + (r + 1) * cell
                    )
                )
                shape.finish(fill=fill, color=(0.3, 0.25, 0.2), width=0.4)
        for (r, c, dark) in [
            (0, 0, True), (0, 3, True), (1, 1, False), (3, 5, True),
            (4, 2, False), (6, 4, True), (7, 7, False), (2, 6, False),
        ]:
            cx = x + (c + 0.5) * cell
            cy = y + (r + 0.5) * cell
            shape.draw_circle((cx, cy), cell * 0.33)
            shape.finish(
                fill=(0.15, 0.15, 0.18) if dark else (0.96, 0.96, 0.96),
                color=(0.1, 0.1, 0.1), width=0.6,
            )
        shape.commit()
        if label:
            self.text(label, x, y - 5, size=7.5, style="sans")

    def flush(self) -> None:
        if self._written:
            return
        self._writer.write_text(self.page)
        self._written = True

    def footer(self, text: str, page_no: int | str) -> None:
        self.text(text, 54, self.height - 26, size=7.5, style="sans")
        self.text(str(page_no), self.width - 60, self.height - 26, size=7.5, style="sans")
        self.text(f"{page_no}", self.width / 2 - 4, self.height - 26, size=7.5, style="sans")


def wrap_moves(moves: str, width: float, size: float, style: str = "serif") -> list[str]:
    """Break a move list into lines that fit, never mid-token."""
    tokens = moves.split()
    lines: list[str] = []
    cur = ""
    for tok in tokens:
        trial = f"{cur} {tok}".strip()
        if cur and text_width(trial, style, size) > width:
            lines.append(cur)
            cur = tok
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def to_figurines(moves: str) -> str:
    out = []
    for tok in moves.split():
        if re.fullmatch(r"\d{1,3}\.{1,3}", tok) or tok in ("1-0", "0-1", "1/2-1/2", "*", "½-½"):
            out.append(tok)
            continue
        m = re.fullmatch(r"([NBRQK])([a-h]?[x×:]?[a-h][1-8][+#]?)", tok)
        if m:
            fig = FIG_WHITE if tok[0].isupper() else FIG_BLACK
            out.append(f"{fig[m.group(1)]}{m.group(2)}")
            continue
        out.append(tok)
    return " ".join(out)


# --------------------------------------------------------------------------
# Corpus builders
# --------------------------------------------------------------------------


def build_clean_single_column(path: Path) -> None:
    """Type A: perfectly digital, single column, running heads and folios."""
    doc = pymupdf.open()
    page = Page(doc)
    page.text("THE CLASSICAL CHESS OPENINGS", 54, 40, size=7.5, style="sans")
    page.space(14)
    page.line("CHAPTER 1  TACTICAL MOTIFS", 54, size=13, style="bold", leading=20)
    page.space(6)
    page.wrapped(INTRO, 54, 312, first_indent=12)
    page.space(8)
    page.line("Game 1: Morphy - Duke of Brunswick, Paris 1858", 54, size=10.5, style="bold",
              leading=16)
    page.space(3)
    for ln in wrap_moves(G.OPERA, 312, 9.5):
        page.line(ln, 66, leading=14)
    page.space(6)
    page.wrapped(INTRO2, 54, 312, first_indent=12)
    page.footer("The Classical Chess Openings", 1)

    page = Page(doc)
    page.text("THE CLASSICAL CHESS OPENINGS", 54, 40, size=7.5, style="sans")
    page.space(14)
    page.line("Game 2: Anonymous - Anonymous", 54, size=10.5, style="bold", leading=16)
    page.space(3)
    for ln in wrap_moves(G.FOOLS_MATE, 312, 9.5):
        page.line(ln, 66, leading=14)
    page.space(6)
    page.wrapped(
        "Fool's mate in two moves: 1. f3 opens the diagonal for the queen's "
        "lunge. The point of the game is that 1. f3? cannot be played by anyone "
        "who has read this page.",
        54, 312, first_indent=12,
    )
    page.space(10)
    page.line("The Evans Gambit", 54, size=11, style="bold", leading=18)
    page.space(4)
    page.line("Game 3: Anonymous - Anonymous", 54, size=10.5, style="bold", leading=16)
    page.space(3)
    for ln in wrap_moves(G.EVERGREEN, 312, 9.5):
        page.line(ln, 66, leading=14)
    page.footer("The Classical Chess Openings", 2)

    page = Page(doc)
    page.text("THE CLASSICAL CHESS OPENINGS", 54, 40, size=7.5, style="sans")
    page.space(14)
    page.line("Game 4: Anonymous - Anonymous", 54, size=10.5, style="bold", leading=16)
    page.space(3)
    for ln in wrap_moves(G.IMMORTAL, 312, 9.5):
        page.line(ln, 66, leading=14)
    page.space(6)
    page.wrapped(
        "Anderssen's masterpiece. After 4.Kf1 the king steps aside to let the "
        "queen come to h4, and the resulting attack is unstoppable from move 18 "
        "onwards. Note that 18.Bd6 is the move most players overlook.",
        54, 312, first_indent=12,
    )
    page.footer("The Classical Chess Openings", 3)
    save(doc, path)


def build_two_column(path: Path) -> None:
    """Type C: two-column prose with a full-width chapter heading."""
    body = (
        "The Wagner Attack looks harmless on the surface: White offers a knight "
        "for free on move six and, in most variations, simply wins the exchange. "
        "The point is not material but time. Every line Black plays must be "
        "checked, because White's pieces arrive first and a single unchecked "
        "queen move decides the game outright. Reti showed the idea at Barmen "
        "1905 and it has never been fully refuted. Later analysts, among them "
        "Alekhine, showed that the critical moment arrives on move fifteen, when "
        "the queen must retreat or accept a mating net on the seventh rank. "
        "Study the position after fourteen moves and decide for yourself "
        "whether the defence holds. The same tension runs through the whole "
        "hypermodern repertoire: material is cheap, but a tempo is not."
    )
    doc = pymupdf.open()
    page = Page(doc, width=560, height=620)
    page.line("THE ATTACK ON f7", 54, size=13, style="bold", leading=20)
    page.space(4)
    # Newspaper fill: column 0 top-to-bottom, then column 1.  The column is
    # deliberately short so the prose genuinely has to flow into both.
    cols = [(54, 232), (306, 232)]
    bottom = page.y + 110
    bufs = ["", ""]
    ys = [page.y, page.y]
    ci = 0

    def emit(col: int) -> None:
        if bufs[col]:
            page.text(bufs[col], cols[col][0], ys[col], size=9.0)
            ys[col] += 12.5
            bufs[col] = ""

    for word in body.split():
        if ys[ci] > bottom:
            emit(ci)
            ci = 1 - ci
        trial = f"{bufs[ci]} {word}".strip()
        if bufs[ci] and text_width(trial, "serif", 9.0) > cols[ci][1]:
            emit(ci)
        bufs[ci] = f"{bufs[ci]} {word}".strip()
    emit(ci)
    emit(1 - ci)
    page.footer("Attack on f7", 1)

    page2 = Page(doc, width=560, height=620)
    page2.line("Game 5: Anonymous - Anonymous", 54, size=10.5, style="bold", leading=16)
    page2.space(4)
    for ln in wrap_moves(G.SIMPLE_QGD, 452, 9.5):
        page2.line(ln, 60, leading=14)
    page2.space(6)
    page2.wrapped(
        "The critical position. If Black plays 14.Bg3 the knight on d5 is pinned "
        "in the wrong way and White obtains a decisive attack on g7. The move "
        "14...Nd4 is the only move that holds the position together.",
        54, 452, first_indent=12,
    )
    page2.footer("Attack on f7", 2)
    save(doc, path)


def build_parallel_columns(path: Path) -> None:
    """Type B: moves on the left, commentary on the right, same row bands.

    PyMuPDF writes the content stream in call order, so writing the comment
    first means the PDF's own reading order is deliberately useless -- the
    pipeline has to rebuild it from geometry.
    """
    doc = pymupdf.open()
    page = Page(doc, width=560, height=620)
    page.line("Game 6: Morphy - Duke of Brunswick, Paris 1858", 54, size=10.5,
              style="bold", leading=16)
    page.space(6)
    move_lines = wrap_moves(G.OPERA.replace(" 1-0", ""), 190, 9.0)
    comments = [
        "The first phase: both kings are free and the centre stays fluid.",
        "Morphy now grabs the centre and trades the f3 pawn.",
        "The pin on the e5 pawn is the whole point of the game.",
        "White's bishops command the long diagonal.",
        "This is the critical position of the entire game.",
        "The rook sacrifice removes Black's last defender.",
        "And the queen goes to b8, delivering mate.",
    ]
    y = page.y
    for i, ln in enumerate(move_lines):
        cmt = comments[i] if i < len(comments) else ""
        if cmt:
            page.text(cmt, 268, y, size=8.5)
        page.text(ln, 60, y, size=9.0)
        y += 22
    page.footer("Morphy", 1)
    save(doc, path)


def build_variations(path: Path) -> None:
    """Types G & H: annotated game, parenthesised variations, indented lines."""
    var_a = G.make_variation(G.OPERA, 9, length=6, seed=1)
    var_b = G.make_variation(G.OPERA, 21, length=6, seed=2)
    var_c = G.make_variation(G.OPERA, 27, length=6, seed=3)
    for v in (var_a, var_b, var_c):
        G.assert_legal(v, "variation", G.board_after(G.OPERA, 9 if v is var_a else
                                                     21 if v is var_b else 27))

    doc = pymupdf.open()
    page = Page(doc, width=430, height=640)
    page.line("CHAPTER 2", 54, size=11, style="bold", leading=18)
    page.line("THE ART OF CALCULATION", 54, size=13, style="bold", leading=20)
    page.space(6)
    page.wrapped(
        "The following game shows why calculation matters more than pattern "
        "recognition. Every move in the main line is the best move, and none of "
        "them is forced. The variations are the author's own.",
        54, 320, first_indent=12,
    )
    page.space(8)
    page.line("Game 7: Morphy - Duke of Brunswick", 54, size=10.5, style="bold", leading=16)
    page.space(3)
    for ln in wrap_moves(G.OPERA.replace(" 1-0", ""), 320, 9.5):
        page.line(ln, 66, leading=14)
    page.space(4)
    page.line(
        "Instead of 5...dxe5 Black may prefer the quieter line. " + var_a,
        54, leading=14,
    )
    page.space(2)
    page.line("    " + var_b, 78, leading=14)
    page.space(2)
    page.line("    " + var_c, 78, leading=14)
    page.space(4)
    page.wrapped(
        "The critical position is reached after 13.Rxd7. Only 13...Rxd7 ("
        + var_c
        + ") keeps Black alive, and even there White has the decisive continuation.",
        54, 320, first_indent=12,
    )
    page.footer("Calculation", 1)
    save(doc, path)


def build_figurine(path: Path) -> None:
    """Figurine notation with a solid/outline distinction (charter #7)."""
    doc = pymupdf.open()
    page = Page(doc)
    page.line("GAMES IN FIGURINE NOTATION", 54, size=11, style="bold", leading=18)
    page.space(6)
    page.line("Game 8: Anonymous - Anonymous", 54, size=10.5, style="bold", leading=16)
    page.space(3)
    for ln in wrap_moves(to_figurines(G.OPERA), 312, 10.5):
        page.line(ln, 66, size=10.5, leading=15)
    page.footer("Figurines", 1)
    save(doc, path)


def build_weird_typography(path: Path) -> None:
    """Move numbers in a separate margin column, loose spacing, hyphenation."""
    doc = pymupdf.open()
    page = Page(doc, width=460, height=640)
    page.line("GAMES WITH UNUSUAL TYPESETTING", 54, size=11, style="bold", leading=18)
    page.space(6)
    page.line("Game 9: Anonymous - Anonymous", 54, size=10.5, style="bold", leading=16)
    page.space(4)
    board = G.board_after(G.OPERA, 0)
    y = page.y
    number = 1
    side = "w"
    # the move number lives in a hanging margin; white and black share a row
    for san in G.san_tokens(G.OPERA):
        board.push_san(san)
        if side == "w":
            page.text(f"{number}.", 56, y, size=9.5)
        page.text(san, 88, y, size=9.5)
        y += 14
        if side == "w":
            side = "b"
        else:
            side = "w"
            number += 1
    page.space(6)
    page.text("The genius of this line is its sheer economy. Black's only real", 54, y + 6,
              size=9.5)
    page.text("chance is to attack on the queenside, but White has already", 54, y + 19,
              size=9.5)
    page.text("secured the centre and the initiative for the rest of the game.", 54, y + 32,
              size=9.5)
    page.footer("Typography", 1)
    save(doc, path)


def build_prose_heavy(path: Path) -> None:
    """Type F: moves mentioned inside running prose, plus one annotated game."""
    doc = pymupdf.open()
    page = Page(doc)
    page.line("MODERN CHESS STRATEGY", 54, size=12, style="bold", leading=18)
    page.space(4)
    paragraphs = [
        "The modern game is defined by the willingness of both sides to play in "
        "the best version of the position rather than the most human one. After "
        "13.Rxd7! Black has no good answer to 14.Rd1, which changes everything. "
        "Earlier, 11.Bxb5+ had been possible because of the pin on the knight, "
        "but once White played 4.dxe5 the exchange was justified.",
        "Consider the famous sequence from the Opera Game. After 12.O-O-O! "
        "White's rooks are lined up on the open d-file, and after 13.Rxd7! the "
        "queen has no comfortable square. Only 13...Rxd7 avoids mate, but it "
        "costs Black a piece. The continuation 14.Rd1 shows the idea: with the "
        "queen attacked from two directions, material becomes irrelevant.",
        "A final example. In the position after 17...Nxb8, the queen has been "
        "sacrificed for nothing more than a tempo, and 17.Rd8 is forced. Note "
        "that 17.Be2 would lose immediately to 17...Qxf5, a line most players "
        "never consider.",
    ]
    for p in paragraphs:
        page.wrapped(p, 54, 312, first_indent=12)
        page.space(6)
    page.footer("Strategy", 1)
    save(doc, path)


def build_diagram_heavy(path: Path) -> None:
    """Type E: vector boards between paragraphs and after moves."""
    doc = pymupdf.open()
    page = Page(doc)
    page.line("ENDGAME TECHNIQUE", 54, size=12, style="bold", leading=18)
    page.space(4)
    page.wrapped(
        "The diagram below shows the position after 13.Rxd7. Study the geometry "
        "of the attack before reading on.",
        54, 312, first_indent=12,
    )
    page.space(6)
    page.text("Diagram 1 - after 13.Rxd7", 150, page.y, size=8, style="sans")
    page.y += 6
    page.board(144, page.y, 132)
    page.y += 140
    page.wrapped(
        "The rook on d7 is the point of the position: it pins the knight and "
        "opens the seventh rank for the other rook. After 13...Rxd7 White has "
        "13...Kf8 as the only way to survive.",
        54, 312, first_indent=12,
    )
    page.space(8)
    page.line("Game 10: Anonymous - Anonymous", 54, size=10.5, style="bold", leading=16)
    page.space(3)
    for ln in wrap_moves(G.EVERGREEN, 312, 9.5):
        page.line(ln, 66, leading=14)
    page.y += 4
    page.board(60, page.y, 120, label="Diagram 2 - final position")
    page.y += 130
    page.text("A decorative rule that is definitely not a board.", 60, page.y, size=8,
              style="sans")
    page.y += 46
    page.board(200, page.y, 96, label="Diagram 3 - minor detail")
    page.footer("Endgames", 1)
    save(doc, path)


def build_with_toc_and_headers(path: Path) -> None:
    """TOC, roman front matter, arabic body pages, running heads."""
    doc = pymupdf.open()
    page = Page(doc)
    page.text("MODERN CHESS THEORY", 190, 260, size=22, style="bold")
    page.text("A Compendium of Annotated Games", 160, 290, size=11)
    page.text("Fourth Edition", 200, 320, size=10)
    page.text("i", 300, 590, size=8)

    page = Page(doc)
    page.line("CONTENTS", 54, size=13, style="bold", leading=22)
    for title, num in [
        ("CHAPTER 1  THE CLASSICAL CHESS OPENINGS", "1"),
        ("CHAPTER 2  THE ART OF CALCULATION", "5"),
        ("CHAPTER 3  ENDGAME TECHNIQUE", "11"),
        ("CHAPTER 4  THE ATTACK ON f7", "17"),
    ]:
        page.line(f"{title}  {'.' * 6}  {num}", 54, leading=16)

    page = Page(doc)
    page.text("MODERN CHESS THEORY", 54, 40, size=7.5, style="sans")
    page.space(12)
    page.line("CHAPTER 1", 54, size=11, style="bold", leading=18)
    page.line("THE CLASSICAL CHESS OPENINGS", 54, size=13, style="bold", leading=20)
    page.space(6)
    page.wrapped(INTRO, 54, 312, first_indent=12)
    page.space(8)
    page.line("Game 1: Morphy - Duke of Brunswick", 54, size=10.5, style="bold", leading=16)
    page.space(3)
    for ln in wrap_moves(G.OPERA, 312, 9.5):
        page.line(ln, 66, leading=14)
    page.footer("Chapter 1", 1)

    page = Page(doc)
    page.text("MODERN CHESS THEORY", 54, 40, size=7.5, style="sans")
    page.space(12)
    page.line("Game 2: Anonymous - Anonymous", 54, size=10.5, style="bold", leading=16)
    page.space(3)
    for ln in wrap_moves(G.EVERGREEN, 312, 9.5):
        page.line(ln, 66, leading=14)
    page.footer("Chapter 1", 2)
    save(doc, path)


def build_scanned(path: Path) -> None:
    """Type D: rasterise a real page, then rebuild a PDF with no text layer."""
    source = OUT / "clean_single_column.pdf"
    if not source.exists():
        build_clean_single_column(source)
    src = pymupdf.open(source)
    _PAGES.clear()
    doc = pymupdf.open()
    for i in range(min(2, src.page_count)):
        # ~300 DPI, i.e. what a competent book scanner produces
        pix = src.load_page(i).get_pixmap(matrix=pymupdf.Matrix(3.0, 3.0), alpha=False)
        newp = doc.new_page(width=420, height=620)
        newp.insert_image(pymupdf.Rect(0, 0, 420, 620), pixmap=pix)
    save(doc, path)
    src.close()


def build_ambiguous(path: Path) -> None:
    """Ambiguity, an illegal move, a mid-game fragment, and PGN headers."""
    doc = pymupdf.open()
    page = Page(doc)
    page.line("THE TRICKY CORNER", 54, size=12, style="bold", leading=18)
    page.space(4)

    page.line('[Event "Test Championship"]', 54, leading=13, style="mono", size=8)
    page.line('[White "Alpha, Ann"]', 54, leading=13, style="mono", size=8)
    page.line('[Black "Beta, Bob"]', 54, leading=13, style="mono", size=8)
    page.line('[Date "1998.05.12"]', 54, leading=13, style="mono", size=8)
    page.line('[Result "1-0"]', 54, leading=13, style="mono", size=8)
    page.space(4)
    for ln in wrap_moves(G.SIMPLE_QGD, 312, 9.5):
        page.line(ln, 66, leading=14)

    page.space(8)
    page.line("A game with one impossible move", 54, size=10, style="bold", leading=16)
    page.space(3)
    # the same game, but with 12. O-O replaced by an illegal 12. O-O-O
    # exactly one illegal move (the c8 bishop is still on c8), so the parser
    # has to report it and then carry on with the rest of the game
    broken = G.SIMPLE_QGD.replace("11. Rc1 Be6 1-0", "11. Rc1 Be6 12. O-O-O 1-0")
    for ln in wrap_moves(broken, 312, 9.5):
        page.line(ln, 66, leading=14)

    page.space(8)
    page.line("A game that starts in the middle of nowhere", 54, size=10, style="bold",
              leading=16)
    page.space(3)
    page.line("12...Nxd4 13. Bxd4 Qxd4 14. Nc3 Qc5 15. Nb5 a6", 66, leading=14)

    page.space(8)
    page.line("A line that ends on a genuinely ambiguous knight move", 54, size=10,
              style="bold", leading=16)
    page.space(3)
    amb = G.ambiguity_line()
    if not amb:
        raise RuntimeError("corpus: no ambiguous knight position found; refusing to ship")
    print(f"  verified ambiguity line: {amb}")
    for ln in wrap_moves(amb, 312, 9.5):
        page.line(ln, 66, leading=14)
    page.footer("Tricky", 1)
    save(doc, path)


BUILDERS = {
    "clean_single_column.pdf": build_clean_single_column,
    "two_column.pdf": build_two_column,
    "parallel_columns.pdf": build_parallel_columns,
    "variations.pdf": build_variations,
    "figurine.pdf": build_figurine,
    "weird_typography.pdf": build_weird_typography,
    "prose_heavy.pdf": build_prose_heavy,
    "diagram_heavy.pdf": build_diagram_heavy,
    "with_toc_and_headers.pdf": build_with_toc_and_headers,
    "scanned.pdf": build_scanned,
    "ambiguous.pdf": build_ambiguous,
}


def verify_content() -> None:
    for name, pgn in G.ALL_GAMES:
        n = G.assert_legal(pgn, name)
        print(f"  verified {name}: {n} plies legal")
    # every variation the variations.pdf will contain
    for ply, seed in ((9, 1), (21, 2), (27, 3)):
        line = G.make_variation(G.OPERA, ply, length=6, seed=seed)
        G.assert_legal(line, f"variation@{ply}", G.board_after(G.OPERA, ply))
        print(f"  verified variation@{ply}: {line}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    missing = [f for f in FONT_FILES.values() if not Path(f).exists()]
    if missing:
        print(f"missing fonts: {missing}", file=sys.stderr)
        return 1
    print("verifying chess content:")
    verify_content()
    print("writing PDFs:")
    for name, fn in BUILDERS.items():
        _PAGES.clear()
        target = OUT / name
        fn(target)
        print(f"  wrote {target.relative_to(ROOT)} ({target.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())