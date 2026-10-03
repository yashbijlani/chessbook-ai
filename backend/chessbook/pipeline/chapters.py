"""Stage: document structure -- chapters, sections, table of contents.

Deliberately conservative (charter #12): a big bold line is *not* automatically
a chapter.  We require corroboration -- an explicit numbering word, a TOC entry,
a font-size jump *and* surrounding structure, or a block of games beneath it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import SourceRef, TocEntry
from .layout import LaidOutPage, Paragraph

CHAPTER_WORD = re.compile(r"^\s*(chapter|part|lesson|session)\s+([0-9IVXLCDM]+|[a-z]+)\b", re.I)
NUMBERED_HEADING = re.compile(r"^\s*(\d{1,2})\s*[.)]\s+(\S.{0,90})$")
ROMAN_HEADING = re.compile(r"^\s*([IVXLCDM]{1,7})\s*[.)]?\s+(\S.{0,90})$")
ALL_CAPS = re.compile(r"^[A-Z0-9 ,.'&!?:\-()]{4,80}$")
#: "CHAPTER 1  THE CLASSICAL OPENINGS ...... 12"  (digits are allowed in titles)
TOC_LINE = re.compile(r"^(?P<title>\S.{3,110}?)[\s.]{0,6}(?:[.\u2026\s]){2,}(?P<page>\d{1,4})$")
PAGE_NUM_LINE = re.compile(r"^\s*[-–—.\s]*(\d{1,4})[-–—.\s]*$")
GAME_RESULT = re.compile(r"(?:^|\s)(?:0-1|1-0|1/2-1/2|½-½|\*)(?:\s|$)")
NUMBERED_MOVE = re.compile(r"(?:^|\s)\d{1,3}\.{1,3}")

# Words that mark a line as prose, not a heading.
PROSE_MARKERS = (
    " the ", " and ", " of ", " that ", " which ", " with ", " this ",
    " is ", " are ", " was ", " were ", " it ", " in ", " for ", " to ",
)


@dataclass
class StructureNode:
    id: str
    level: int
    kind: str
    title: str
    page: int
    seg_index: int
    confidence: float
    source: SourceRef | None = None
    parent_id: str | None = None
    game_ids: list[str] = field(default_factory=list)
    start_seg: int = 0
    end_seg: int = 0
    start_page: int | None = None
    end_page: int | None = None


def is_toc_page(paragraphs: list[Paragraph]) -> bool:
    if len(paragraphs) < 3:
        return False
    toc_hits = sum(1 for p in paragraphs if TOC_LINE.match(p.text.strip()))
    body_words = sum(len(p.text.split()) for p in paragraphs)
    # a TOC page: many "title ....... number" lines, little prose
    return toc_hits >= 3 and toc_hits >= 0.4 * len(paragraphs) and body_words < 900


def detect_toc(paragraphs_by_page: dict[int, list[Paragraph]], max_page: int) -> list[TocEntry]:
    """Collect TOC entries from the front matter."""
    entries: list[TocEntry] = []
    for page in sorted(paragraphs_by_page):
        if page > max_page:
            break
        paras = paragraphs_by_page[page]
        if not is_toc_page(paras):
            continue
        for p in paras:
            m = TOC_LINE.match(p.text.strip())
            if not m:
                continue
            title = m.group("title").strip(" .")
            if not title or len(title) < 3:
                continue
            level = _toc_level(title)
            entries.append(
                TocEntry(
                    level=level,
                    title=title,
                    page=int(m.group("page")),
                    target_id=None,
                )
            )
    return entries


def _toc_level(title: str) -> int:
    t = title.strip()
    if CHAPTER_WORD.match(t) or NUMBERED_HEADING.match(t):
        return 0
    return 1


def detect_structure(
    pages: list[LaidOutPage],
    paragraphs: list[Paragraph],
    body_font_size: float,
) -> list[StructureNode]:
    """Return an ordered list of chapter/section nodes."""
    nodes: list[StructureNode] = []
    parent_stack: list[StructureNode] = []

    for idx, para in enumerate(paragraphs):
        if para.kind not in ("heading", "prose", "move_run"):
            continue
        score, level, kind = _heading_score(para, body_font_size, idx, paragraphs)
        if score < 0.55:
            continue
        node = StructureNode(
            id=f"ch_{len(nodes) + 1}",
            level=level,
            kind=kind,
            title=_clean_title(para.text),
            page=para.page,
            seg_index=idx,
            confidence=round(min(1.0, score), 3),
            source=para.source,
        )
        while parent_stack and parent_stack[-1].level >= node.level:
            parent_stack.pop()
        if parent_stack:
            node.parent_id = parent_stack[-1].id
        parent_stack.append(node)
        nodes.append(node)

    _assign_ranges(nodes, len(paragraphs))
    return nodes


def _heading_score(
    para: Paragraph, body_size: float, idx: int, paragraphs: list[Paragraph]
) -> tuple[float, int, str]:
    text = para.text.strip()
    if not text or len(text) > 140:
        return 0.0, 0, "chapter"
    # A game line is not a heading, however much it starts with a number.
    if GAME_RESULT.search(text) or len(NUMBERED_MOVE.findall(text)) >= 2:
        return 0.0, 0, "chapter"
    lower = f" {text.lower()} "
    if any(m in lower for m in PROSE_MARKERS) and len(text.split()) > 9:
        return 0.0, 0, "chapter"

    score = 0.0
    level = 1
    kind = "section"

    if CHAPTER_WORD.match(text):
        score += 0.75
        level = 0
        kind = "chapter"
    elif NUMBERED_HEADING.match(text):
        score += 0.55
        level = 0 if len(text.split()[0].rstrip(".)")) <= 2 else 1
        kind = "chapter"
    elif ROMAN_HEADING.match(text) and text.split()[0].rstrip(".)") in (
        "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"
    ):
        score += 0.5
        level = 0
        kind = "chapter"

    if para.kind == "heading":
        score += 0.3
    if ALL_CAPS.match(text) and len(text.split()) <= 12:
        score += 0.25
        level = min(level, 1)

    # A heading is followed by games: look ahead a little.
    lookahead = paragraphs[idx + 1 : idx + 9]
    move_hits = sum(1 for p in lookahead if p.kind == "move_run")
    if move_hits >= 1:
        score += 0.2
    if idx > 0:
        prev = paragraphs[idx - 1]
        if prev.kind == "move_run" or prev.text.strip() == "":
            score += 0.05

    if len(text.split()) <= 3:
        score += 0.1
    if text.endswith((".", ",", ";")) and not text.endswith("..."):
        score -= 0.2
    return score, level, kind


def _clean_title(text: str) -> str:
    t = re.sub(r"\s+", " ", text.strip())
    return t.strip(" .:-–—")


def _assign_ranges(nodes: list[StructureNode], total: int) -> None:
    for i, node in enumerate(nodes):
        node.start_seg = node.seg_index
        node.end_seg = (nodes[i + 1].seg_index - 1) if i + 1 < len(nodes) else total - 1
        node.start_page = node.page
        node.end_page = nodes[i + 1].page - 1 if i + 1 < len(nodes) else None
        if node.end_page is not None and node.end_page < node.start_page:
            node.end_page = node.start_page


def reconcile_toc_with_pages(entries: list[TocEntry], printed_to_physical: int | None) -> None:
    """TOC page numbers are *printed* numbers; shift them to physical ones."""
    if not printed_to_physical:
        return
    shift = printed_to_physical
    for e in entries:
        if e.page is not None:
            e.page = max(1, e.page + shift)


def estimate_page_offset(pages: list[LaidOutPage]) -> int | None:
    """Find ``printed = physical + offset`` from detected page numbers."""
    from .layout import _PAGE_NUM_RE  # local import: shared rule

    pairs: list[tuple[int, int]] = []
    for lp in pages:
        # folios were removed from lp.ordered, so read them from all_lines
        for ln in lp.all_lines or lp.ordered:
            if ln.role != "page_number":
                continue
            m = _PAGE_NUM_RE.match(ln.text)
            if not m:
                continue
            printed = m.group(1)
            if not printed.isdigit():
                continue
            pairs.append((lp.page, int(printed)))
            break
    if len(pairs) < 2:
        return None
    votes: dict[int, int] = {}
    for physical, printed in pairs:
        votes[physical - printed] = votes.get(physical - printed, 0) + 1
    best, count = max(votes.items(), key=lambda kv: kv[1])
    return best if count >= max(2, 0.6 * len(pairs)) else None