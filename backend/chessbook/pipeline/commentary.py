"""Stage: commentary association (charter #10).

Association is *scored*, never assumed.  Every comment keeps the signals that
produced its confidence so the debug view can explain the decision.

Signals (charter #10):
  * vertical proximity / same row band
  * the move immediately preceding it in the reading order
  * paragraph boundaries
  * presence of chess notation inside the comment
  * punctuation (a comment rarely ends mid-move)
  * typography (italic/smaller -> editorial aside)
  * indentation (indented text is usually a side note)
  * column geometry (parallel move/prose layouts)
  * explicit move references ("After 15...Qxd4 ...")
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import chess

from ..models import Comment
from . import notation as nz
from .layout import Paragraph
from .tree import InlineRef, TreeResult

#: Weights of the individual signals (they sum to 1.0).
WEIGHTS = {
    "precedes_no_move": 0.22,
    "adjacent_move": 0.20,
    "row_band": 0.16,
    "same_column": 0.08,
    "punctuation": 0.06,
    "paragraph_boundary": 0.08,
    "inline_reference": 0.14,
    "typography": 0.03,
    "indent": 0.03,
}

_INLINE_REF_RE = re.compile(
    r"(?:after|before|following|preceding|see|compare|instead of|rather than|note that)?\s*"
    r"\b(\d{1,3})\s*(\.{1,3})\s*([KQRBNP]?[a-h]?[x×:]?[a-h][1-8](?:[ ]?=[ ]?[QRBNP])?[+#]?|"
    r"[0OoQq]{1,2}[ ]?[-–—][ ]?[0OoQq]{1,2})\b"
)
_INLINE_SIDE_WORDS = {"after": 1, "before": 1, "following": 1, "preceding": 1, "see": 1,
                      "compare": 1, "instead of": 1, "rather than": 1, "note that": 1}


@dataclass
class LinkResult:
    linked: int = 0
    unlinked: int = 0


def score_comment(comment: Comment) -> float:
    """Turn a comment's signal dictionary into a 0..1 confidence."""
    if not comment.signals:
        return 0.3
    total = 0.0
    weight = 0.0
    for key, w in WEIGHTS.items():
        if key not in comment.signals:
            continue
        total += w * max(0.0, min(1.0, comment.signals[key]))
        weight += w
    if weight <= 0:
        return 0.3
    return round(max(0.0, min(1.0, total / weight)), 3)


def attach_sentences_to_nodes(result: TreeResult) -> None:
    """Split a comment that clearly talks about a later move.

    ``"... After 15...Qxd4 White has several possibilities."`` should be split
    so the first sentence stays with the preceding move and the second is
    attached to the referenced node when that node exists.
    """
    by_number_side: dict[tuple[int, str], list[str]] = {}
    for node in result.nodes:
        if node.san is None or node.parent_id is None:
            continue
        by_number_side.setdefault((node.move_number, node.side), []).append(node.id)

    for comment in result.comments:
        text = comment.text
        refs = _find_references(text, result)
        if not refs:
            continue
        first_ref = refs[0]
        if first_ref["node_id"] == comment.attached_to:
            comment.signals["inline_reference"] = 1.0
            continue
        lead, _, tail = text.partition(first_ref["text"])
        if len(lead.strip()) < 3 or len(tail.strip()) < 3:
            continue
        new_id = f"{comment.id}_b"
        head = Comment(
            id=comment.id,
            text=lead.strip(),
            seg_index=comment.seg_index,
            source=comment.source,
            attached_to=comment.attached_to,
            confidence=0.0,
            signals=dict(comment.signals),
            anchor_kind=comment.anchor_kind,
        )
        tail_comment = Comment(
            id=new_id,
            text=(first_ref["text"] + tail).strip(),
            seg_index=comment.seg_index,
            source=comment.source,
            attached_to=first_ref["node_id"],
            confidence=0.0,
            signals={**comment.signals, "inline_reference": 1.0, "adjacent_move": 0.0},
            anchor_kind="inline_reference",
        )
        comment.text = head.text
        comment.signals = head.signals
        result.comments.append(tail_comment)


def _find_references(text: str, result: TreeResult) -> list[dict]:
    index: dict[tuple[int, str], list[str]] = {}
    for node in result.nodes:
        if node.san is None or node.parent_id is None:
            continue
        index.setdefault((node.move_number, node.side), []).append(node.id)
    by_id = result.node_by_id

    out: list[dict] = []
    for m in _INLINE_REF_RE.finditer(text):
        number = int(m.group(1))
        side = "b" if m.group(2).count(".") >= 3 else "w"
        san = m.group(3)
        for nid in index.get((number, side), []):
            node = by_id[nid]
            if _san_matches(san, node.san or ""):
                out.append({"node_id": nid, "text": m.group(0).strip(), "number": number})
                break
    return out


def _san_matches(written: str, canonical: str) -> bool:
    a = nz.strip_noise(written).lower()
    b = nz.strip_noise(canonical).lower()
    if a == b:
        return True
    ka, kb = nz.san_parts(written), nz.san_parts(canonical)
    if ka and kb:
        return ka.compatible_with(kb)
    return False


def link_inline_refs(result: TreeResult, refs: list[InlineRef]) -> LinkResult:
    """Make prose move references clickable (charter #23)."""
    index: dict[tuple[int, str], list[str]] = {}
    for node in result.nodes:
        if node.san is None or node.parent_id is None:
            continue
        index.setdefault((node.move_number, node.side), []).append(node.id)
    by_id = result.node_by_id

    out = LinkResult()
    for ref in refs:
        out.unlinked += 1
        if ref.move_number is None or ref.side is None:
            continue
        for nid in index.get((ref.move_number, ref.side), []):
            node = by_id[nid]
            if _san_matches(ref.san, node.san or ""):
                ref.node_id = nid
                out.linked += 1
                out.unlinked -= 1
                break
    return out


def apply_parallel_geometry(result: TreeResult, paragraphs: list[Paragraph]) -> None:
    """Boost/penalise confidence using row bands and column geometry.

    Used for the "moves in one column, prose in the other" layout where the
    PDF's own reading order interleaves them (charter #5 Type B).
    """
    node_rows: dict[str, tuple[int, int, int]] = {}
    for node in result.nodes:
        if node.source is None or node.source.bbox is None:
            continue
        b = node.source.bbox
        node_rows[node.id] = (node.source.page, int(b[1]), int(b[0]))

    for comment in result.comments:
        if comment.source is None or comment.source.bbox is None:
            continue
        page = comment.source.page
        cy = int(comment.source.bbox[1])
        cx = int(comment.source.bbox[0])
        target = comment.attached_to
        signals = comment.signals
        if target and target in node_rows:
            npage, ny, nx = node_rows[target]
            if npage == page:
                dy = abs(ny - cy)
                if dy <= 18:
                    signals["row_band"] = 1.0
                elif dy <= 40:
                    signals["row_band"] = 0.5
                else:
                    signals["row_band"] = 0.0
                signals["same_column"] = 1.0 if abs(nx - cx) <= 60 else 0.4
        signals["punctuation"] = 1.0 if comment.text.rstrip().endswith((".", "!", "?")) else 0.35
        seg = paragraphs[comment.seg_index] if 0 <= comment.seg_index < len(paragraphs) else None
        if seg is not None:
            if seg.kind == "caption":
                signals["paragraph_boundary"] = 1.0
            else:
                signals["paragraph_boundary"] = 0.7
        else:
            signals.setdefault("paragraph_boundary", 0.4)
        comment.confidence = score_comment(comment)


def finalise_confidence(result: TreeResult) -> None:
    for comment in result.comments:
        if not comment.signals:
            comment.confidence = 0.35
            continue
        comment.confidence = score_comment(comment)


def node_comment_index(result: TreeResult) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in result.comments:
        if c.attached_to:
            out.setdefault(c.attached_to, []).append(c.id)
    for node in result.nodes:
        for cid in out.get(node.id, []):
            if cid not in node.comment_ids:
                node.comment_ids.append(cid)
    return out


def board_at(result: TreeResult, node_id: str) -> chess.Board | None:
    node = result.node_by_id.get(node_id)
    if node is None:
        return None
    try:
        return chess.Board(node.fen_after)
    except ValueError:  # pragma: no cover
        return None