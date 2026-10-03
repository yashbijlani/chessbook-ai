"""Stage: notation stream -> validated move tree.

The central rule is beautifully simple and completely deterministic::

    a numbered token continues the main line iff
        token.move_number == board.fullmove_number  and  token.side == board.turn

Anything else is either a variation or a resync attempt.  Legality is always
checked by ``python-chess`` (#6), and every deviation from a straight
main-line walk is recorded as a signal, an issue, or a repair (#17).
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Literal

import chess

from ..models import Comment, Node, ParsedMove, Severity, SourceRef, ValidationIssue
from . import notation as nz

START_FEN = chess.STARTING_FEN

Context = Literal["move", "prose"]
Origin = Literal["mainline", "explicit_number", "repeated_number", "parenthesis", "indent", "resync"]


@dataclass
class Segment:
    """One ordered line/paragraph handed to the tree builder."""

    index: int
    page: int
    text: str
    bbox: list[float]
    kind: str = "prose"
    indent: float = 0.0
    column: int = 0
    font_size: float = 10.0
    is_heading: bool = False

    @property
    def x0(self) -> float:
        return self.bbox[0]


@dataclass
class PendingComment:
    node_id: str
    text: str
    source: SourceRef
    signals: dict[str, float]
    seg_index: int = -1


@dataclass
class InlineRef:
    """A move mentioned inside prose (charter #23 clickable references)."""

    page: int
    text: str
    san: str
    move_number: int | None
    side: Literal["w", "b"] | None
    node_id: str | None
    bbox: list[float]


@dataclass
class TreeResult:
    nodes: list[Node] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    inline_refs: list[InlineRef] = field(default_factory=list)
    root_id: str = ""
    final_fen: str = START_FEN
    result: str | None = None
    stats: dict[str, int] = field(default_factory=dict)
    mainline_legal: bool = True
    fen_consistent: bool = True
    node_by_id: dict[str, Node] = field(default_factory=dict)

    def mainline(self) -> list[Node]:
        out: list[Node] = []
        cur = self.node_by_id.get(self.root_id)
        while cur is not None:
            out.append(cur)
            nxt = cur.mainline_child_id
            if not nxt:
                break
            cur = self.node_by_id.get(nxt)
        return out


class MoveTreeBuilder:
    def __init__(self, game_id: str, root_fen: str = START_FEN) -> None:
        self.game_id = game_id
        self._ids = itertools.count(1)
        self._variations = itertools.count(1)
        self.nodes: dict[str, Node] = {}
        self.issues: list[ValidationIssue] = []
        self.comments: list[Comment] = []
        self.inline_refs: list[InlineRef] = []
        self.counter: dict[str, int] = {
            "moves_detected": 0,
            "valid": 0,
            "repaired": 0,
            "ambiguous": 0,
            "invalid": 0,
            "rejected_as_prose": 0,
        "skipped_after_failure": 0,
        "branches_merged": 0,
            "variations": 0,
            "comments": 0,
        }

        self.root_board = chess.Board(root_fen)
        root_id = self._new_id()
        self.root = Node(
            id=root_id,
            parent_id=None,
            ply=0,
            move_number=1,
            side="w",
            san=None,
            fen_before=self.root_board.fen(),
            fen_after=self.root_board.fen(),
            is_mainline=True,
            started_by="mainline",
            confidence=1.0,
        )
        self.nodes[root_id] = self.root
        self.root_id = root_id
        self.path: list[str] = [root_id]
        self.boards: list[chess.Board] = [self.root_board.copy()]
        self.paren_depth = 0
        self.pending_comment: list[PendingComment] = []
        self.result: str | None = None
        self.fen_consistent = True
        self.mainline_legal = True
        self._mainline_ids: set[str] = {root_id}
        #: (move_number, side) -> node id, for main-line nodes only.  Side lines
        #: are almost always hung off the main line, not off another variation.
        self._mainline_index: dict[tuple[int, str], str] = {}
        self._brace_node: str | None = None
        self._paren_anchors: list[str] = []
        self._paren_pending_branch = False
        self._paren_continues = False
        self._consecutive_failures = 0
        self._resyncs = 0
        self._aborted = False

    # -- helpers ---------------------------------------------------------

    def _new_id(self) -> str:
        return f"{self.game_id}_n{next(self._ids)}"

    def _issue(
        self,
        code: str,
        message: str,
        *,
        severity: str = "warning",
        page: int | None = None,
        node_id: str | None = None,
        source_text: str | None = None,
        bbox: list[float] | None = None,
        context: dict | None = None,
    ) -> None:
        self.issues.append(
            ValidationIssue(
                id=f"{self.game_id}_i{len(self.issues) + 1}",
                severity=severity,  # type: ignore[arg-type]
                code=code,
                message=message,
                page=page,
                game_id=self.game_id,
                node_id=node_id,
                source_text=source_text,
                source=SourceRef(page=page, bbox=bbox) if page else None,
                context=context or {},
            )
        )

    @property
    def board(self) -> chess.Board:
        return self.boards[-1]

    @property
    def current(self) -> Node:
        return self.nodes[self.path[-1]]

    # -- main entry ------------------------------------------------------

    def run(self, segments: list[Segment]) -> TreeResult:
        blocked = self._midgame_start(segments)
        if blocked is not None:
            return self._result(midgame_start=blocked)
        for seg in segments:
            if seg.is_heading:
                self._flush_comments(force=True)
                continue
            context = self.classify(seg)
            pieces = nz.tokenize(seg.text, page=seg.page)
            self._resyncs = 0
            self._consume(seg, pieces, context)

        self._flush_comments()
        return self._result()

    def _midgame_start(self, segments: list[Segment]) -> str | None:
        """A fragment that opens at move >1 has no derivable starting position.

        We refuse to invent one (#17): report it and let the correction UI
        supply a FEN.
        """
        if self.root_board.fen() != chess.Board().fen():
            return None  # caller supplied a position: nothing to complain about
        for seg in segments:
            for piece in nz.tokenize(seg.text, page=seg.page):
                if piece.kind == nz.PieceKind.MOVE and piece.move is not None:
                    n = piece.move.move_number
                    if n is not None and n > 1:
                        self._issue(
                            "game_starts_midgame",
                            f"Game text begins at move {n}.; without the earlier "
                            "position the tree cannot be reconstructed",
                            severity="error",
                            page=seg.page,
                            source_text=piece.move.raw,
                            bbox=seg.bbox,
                            context={"first_move": piece.move.raw, "move_number": n},
                        )
                        return f"starts at move {n}"
                    return None
        return None

    #: A block that *opens* with chess notation is a move run.  Prose that
    #: merely quotes a move ("After 2...Nc6 White can play 3.d4") opens with a
    #: word, and those quotes are references, not instructions.
    MOVE_RUN_DENSITY = 0.42

    def classify(self, seg: Segment) -> Context:
        """Decide whether a line's move tokens are *applied* or *referenced*."""
        text = nz.normalize_figurines(seg.text)
        moves, total = nz.token_density(text)
        if total == 0:
            return "prose"
        numbered = len(nz.MOVE_TOKEN_RE.findall(text))
        density = moves / total
        opens_with_notation = _opens_with_notation(text)

        if self.paren_depth > 0:
            return "move" if density >= 0.35 else "prose"
        if seg.kind == "move_run" and density >= self.MOVE_RUN_DENSITY:
            return "move"
        if not opens_with_notation and seg.kind != "move_run":
            # prose that happens to contain notation
            return "prose"
        if numbered >= 1 and density >= 0.35:
            return "move"
        if density >= 0.72:
            return "move"
        return "prose"

    # -- token consumption ----------------------------------------------

    def _consume(self, seg: Segment, pieces: list[nz.Piece], context: Context) -> None:
        first_move = True
        for piece in pieces:
            kind = piece.kind
            if kind == nz.PieceKind.OPEN:
                if piece.text in "([{":
                    self.paren_depth += 1
                    self._paren_anchors.append(self.path[-1])
                    self._paren_pending_branch = piece.text in "(["
                    if piece.text == "{" and self._brace_node is None:
                        self._brace_node = self.path[-1]
                continue
            if kind == nz.PieceKind.CLOSE:
                if piece.text == "}" and self._brace_node is not None:
                    self._flush_comments()
                    self._brace_node = None
                if piece.text in ")]}":
                    self.paren_depth = max(0, self.paren_depth - 1)
                    self._paren_pending_branch = False
                    self._paren_continues = False
                    if self._paren_anchors:
                        # a parenthesised side line never extends the main line
                        self._truncate_to(self._paren_anchors.pop())
                continue
            if kind == nz.PieceKind.RESULT:
                if self.result is None:
                    self.result = piece.text.strip()
                continue
            if kind == nz.PieceKind.TEXT:
                if self._noise_text(piece.text):
                    continue
                self.pending_comment.append(
                    PendingComment(
                        node_id=self.path[-1],
                        text=piece.text,
                        source=SourceRef(page=seg.page, bbox=seg.bbox),
                        signals={
                            "preceding_move": 1.0,
                            "same_line": 0.6 if seg.kind == "move_run" else 0.25,
                            "in_parentheses": 0.5 if self.paren_depth else 0.0,
                        },
                        seg_index=seg.index,
                    )
                )
                continue

            # a move token
            if context == "move" and self._brace_node is None:
                self._flush_comments_if_new_segment(seg)
                self._apply(piece, seg, first_move)
                first_move = False
            else:
                self._record_reference(piece, seg)

    def _noise_text(self, text: str) -> bool:
        t = text.strip()
        if not t:
            return True
        if t in {"...", "\u2026", "-", "\u2013"}:
            return True
        if set(t) <= set(". \u2026-\u2013\u2014|") and len(t) <= 4:
            return True
        if re.fullmatch(r"\d{1,3}\s*\.{1,3}", t):
            return True
        return False

    # -- applying a token ------------------------------------------------

    def _apply(self, piece: nz.Piece, seg: Segment, first_move: bool) -> None:
        """Apply one notation token.

        Order of attempts (each one strictly more invasive than the last, and
        every step after the first is recorded as an issue):

          1. plain continuation of the current position
          2. branch: rewind to a plausible variation anchor
          3. repair an ambiguous move against the board
          4. resync onto an ancestor position
          5. give up and report
        """
        token = piece.move
        assert token is not None
        self.counter["moves_detected"] += 1

        if self._aborted:
            # The stream is beyond repair: stop guessing (charter #17).
            self.counter["skipped_after_failure"] += 1
            return
        pending_paren_branch = self._paren_pending_branch

        board = self.boards[-1]
        turn = nz.side_of(board.turn)
        side = token.side or turn
        number = token.move_number if token.move_number is not None else board.fullmove_number
        body = token_body(token)

        # (1) straight continuation -------------------------------------
        # A printed token can only continue the main line when it agrees with
        # the board about *whose* move it is (and about the move number when
        # the author printed one).  Otherwise we are looking at a variation.
        agrees = side == turn and (
            token.move_number is None or token.move_number == board.fullmove_number
        )
        if self._paren_pending_branch:
            # A parenthesised run is a side line.  Whether it *replaces* the last
            # main line move or merely continues from it is decided by whose move
            # comes first -- the standard PGN rule.
            self._paren_continues = self._paren_continues_from_anchor(
                side, token.move_number
            )
            if not self._paren_continues:
                agrees = False
        probe = nz.set_side(board.copy(), side)
        resolution = nz.san_candidates(probe, body)
        if pending_paren_branch:
            self._paren_pending_branch = False
        self._paren_continues = False
        if resolution.move is not None and agrees:
            node = self._push(resolution, token, seg, side, number, "mainline")
            self._after_push(resolution, token, seg, node)
            self._consecutive_failures = 0
            return

        # (2) variation branch ------------------------------------------
        origin = "parenthesis" if pending_paren_branch else self._branch_origin(
            piece, seg, first_move
        )
        if origin:
            branch_point = self._branch_point(token, piece, side, seg, origin)
            if branch_point is not None:
                self._truncate_to(branch_point)
                probe = nz.set_side(self.board.copy(), side)
                resolution = nz.san_candidates(probe, body)
                if resolution.move is not None:
                    moved_number = self.board.fullmove_number
                    if token.move_number is not None and token.move_number != moved_number:
                        self._issue(
                            "variation_number_mismatch",
                            f"Variation printed at move {token.move_number} attached at "
                            f"move {moved_number}",
                            severity="info",
                            page=seg.page,
                            source_text=token.raw,
                            bbox=seg.bbox,
                        )
                    node = self._push(resolution, token, seg, side, moved_number, origin)
                    self._after_push(resolution, token, seg, node)
                    self._consecutive_failures = 0
                    if pending_paren_branch:
                        self._paren_pending_branch = False
                        self._paren_continues = False
                    return

        # (4)/(5)/(6) recovery ------------------------------------------
        node, handled = self._recover(piece, seg, token, side, number, origin or "mainline",
                                      resolution)
        if node is not None:
            self._after_push(resolution, token, seg, node)
            self._consecutive_failures = 0
        elif not handled:
            self._reject(piece, seg, resolution.reason or "unresolved", token)
        if node is None:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.FAILURE_LIMIT:
                self._aborted = True
                self._issue(
                    "stream_abandoned",
                    "Too many consecutive unresolvable moves; the rest of this "
                    "game was left untouched rather than guessed at",
                    severity=Severity.ERROR,
                    source_text=token.raw,
                )

    def _after_push(
        self, resolution: nz.Resolution, token: ParsedMove, seg: Segment, node: Node
    ) -> None:
        self.counter["valid"] += 1
        if resolution.status == "repaired":
            self.counter["repaired"] += 1
            self._issue(
                "move_repaired",
                f"{token.raw!r} normalised to {node.san} ({resolution.reason})",
                severity="info",
                page=seg.page,
                node_id=node.id,
                source_text=token.raw,
                bbox=seg.bbox,
                context=resolution.detail,
            )

    def _branch_origin(
        self, piece: nz.Piece, seg: Segment, first_move: bool
    ) -> Origin | None:
        """Which variation signal (if any) explains this token."""
        if self.paren_depth:
            return "parenthesis"
        if piece.explicit_number and piece.move is not None and piece.move.move_number is not None:
            if piece.number != self.board.fullmove_number:
                return "repeated_number"
        if first_move and seg.indent >= 14.0 and self._indent_suggests_variation(seg):
            return "indent"
        return None

    def _branch_point(
        self,
        token: ParsedMove,
        piece: nz.Piece,
        side: Literal["w", "b"],
        seg: Segment,
        origin: Origin,
    ) -> str | None:
        if origin == "parenthesis" and self._paren_anchors:
            anchor = self.nodes[self._paren_anchors[-1]]
            branch = self._paren_branch_target(anchor, side)
            if branch is not None:
                return branch
        number = token.move_number
        if number is not None:
            bp = self._find_branch_point(number, side)
            if bp is not None:
                return bp
        if origin == "parenthesis":
            return self._parenthesis_branch_point()
        if origin == "indent":
            return self.nodes[self.path[-1]].parent_id or self.root_id
        return None

    def _find_branch_point(self, number: int, side: Literal["w", "b"]) -> str | None:
        """Newest node whose *next* move would be ``number``/``side``.

        The main line is consulted first: when a book prints ``14...Nxe4`` after
        a finished game, it means "instead of Black's fourteenth move on the
        main line", not "instead of the fourteenth move of whatever variation we
        happen to be inside".
        """
        anchor = self._mainline_index.get((number, side))
        if anchor is not None:
            return self.nodes[anchor].parent_id or self.root_id
        for node_id in reversed(self.path[:-1]):
            node = self.nodes[node_id]
            if node.move_number == number and node.side == side:
                return node.parent_id or self.root_id
        # black move at ``number`` when the mainline only shows white's move
        for node_id in reversed(self.path[:-1]):
            node = self.nodes[node_id]
            if node.move_number == number and node.side == "w":
                return node_id
        return None

    def _paren_continues_from_anchor(
        self, side: Literal["w", "b"], number: int | None
    ) -> bool:
        """Does the first move inside a bracket simply continue the main line?"""
        if not self._paren_anchors:
            return False
        anchor = self.nodes[self._paren_anchors[-1]]
        return side == anchor.side and number == anchor.move_number + 1

    def _paren_branch_target(
        self, anchor: Node, side: Literal["w", "b"]
    ) -> str | None:
        """Where a bracketed side line attaches.

        Same side as the anchor -> it replaces the anchor's move, so branch from
        the anchor's parent.  Opposite side -> it replaces the move that would
        have come next, so branch from the anchor itself.
        """
        if side == anchor.side:
            return anchor.parent_id or self.root_id
        return anchor.id

    def _indent_suggests_variation(self, seg: Segment) -> bool:
        """An indented line that starts with an unnumbered move is a side line."""
        if self.current.parent_id is None:
            return False
        return True

    def _truncate_to(self, node_id: str) -> None:
        """Rewind the walk to ``node_id`` following parent links.

        The node need not still be on the current path (a variation may have
        been branched from one of *its* ancestors), so we rebuild the path and
        the corresponding board stack from the tree itself.
        """
        chain: list[str] = []
        cur: str | None = node_id
        seen: set[str] = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = self.nodes[cur].parent_id
        chain.reverse()
        if not chain:
            return
        self.path = chain
        self.boards = [self._board_at(nid) for nid in chain]

    def _board_at(self, node_id: str) -> chess.Board:
        node = self.nodes[node_id]
        fen = node.fen_after if node.parent_id is not None else node.fen_before
        try:
            return chess.Board(fen)
        except ValueError:  # pragma: no cover - defensive
            return self.root_board.copy()

    def _find_child(self, parent: Node, uci: str) -> Node | None:
        for cid in parent.child_ids:
            child = self.nodes.get(cid)
            if child is not None and child.move_uci == uci:
                return child
        return None

    def _push(
        self,
        resolution: nz.Resolution,
        token: ParsedMove,
        seg: Segment,
        side: Literal["w", "b"],
        number: int,
        origin: Origin,
    ) -> Node:
        move = resolution.move
        assert move is not None
        board = self.board.copy()
        parent_id = self.path[-1]
        parent = self.nodes[parent_id]
        nz.set_side(board, side)
        fen_before = board.fen()
        san = board.san(move)
        board.push(move)

        is_variation = origin in ("repeated_number", "parenthesis", "indent", "resync")
        # Two different questions: does this node belong to the book's main line,
        # and is it the *primary* continuation of its own parent?  A node inside
        # a variation is off the main line but still leads somewhere.
        parent_in_mainline = parent_id in self._mainline_ids
        is_primary = not is_variation
        on_mainline = is_primary and parent_in_mainline

        # A variation that replays a move we already have must *join* that
        # subtree, not fork a duplicate copy of it.
        existing = self._find_child(parent, move.uci())
        if existing is not None:
            self.path.append(existing.id)
            self.boards.append(board.copy())
            if is_primary:
                parent.add_child(existing.id, mainline=True)
            if parent_in_mainline and existing.id not in self._mainline_ids:
                self._mainline_ids.add(existing.id)
                self._mainline_index.setdefault((existing.move_number, existing.side), existing.id)
            self.counter["branches_merged"] += 1
            return existing

        node_id = self._new_id()

        if is_variation and parent.is_mainline and origin in ("repeated_number", "indent", "resync"):
            vdepth = parent.variation_depth + 1
        elif is_variation:
            vdepth = parent.variation_depth + 1
        else:
            vdepth = parent.variation_depth
        node = Node(
            id=node_id,
            parent_id=parent_id,
            ply=parent.ply + 1,
            move_number=number,
            side=side,
            san=san,
            lan=san,
            move_uci=move.uci(),
            fen_before=fen_before,
            fen_after=board.fen(),
            annotations=[a for a in token.annotation if a in "!?"] if token.annotation else [],
            nags=[nag_for(token.annotation)] if token.annotation else [],
            source_text=token.raw,
            source=SourceRef(page=seg.page, bbox=seg.bbox),
            confidence=_confidence_for(resolution, token, origin),
            is_mainline=on_mainline,
            variation_index=(next(self._variations) if is_variation else 0),
            variation_depth=vdepth,
            started_by=origin,
            move_origin="repaired" if resolution.status == "repaired" else "parsed",
        )
        self.nodes[node_id] = node
        parent.add_child(node_id, mainline=is_primary)
        if on_mainline:
            self._mainline_ids.add(node_id)
            self._mainline_index.setdefault((node.move_number, node.side), node_id)
        self.path.append(node_id)
        self.boards.append(board.copy())
        if is_variation:
            self.counter["variations"] += 1
        return node

    # -- recovery --------------------------------------------------------

    def _recover(
        self,
        piece: nz.Piece,
        seg: Segment,
        token: ParsedMove,
        side: Literal["w", "b"],
        number: int,
        origin: Origin,
        resolution: nz.Resolution,
    ) -> tuple[Node | None, bool]:
        """Illegal / ambiguous token: try legitimate recoveries, never invent.

        Returns ``(node, handled)``; ``handled`` means the token was accounted
        for (rejected as prose, or repaired) so it must not be counted twice.
        """
        if resolution.status == "ambiguous" and resolution.candidates:
            chosen = self._disambiguate(resolution, token, side)
            if chosen is not None:
                fixed = nz.Resolution(
                    chosen,
                    "repaired",
                    reason="ambiguity_resolved",
                    detail={
                        "as_written": token.raw,
                        "options": resolution.detail.get("options", []),
                        "canonical": self.board.san(chosen),
                    },
                    candidates=[chosen],
                    confidence=0.6,
                )
                node = self._push(fixed, token, seg, side, number, origin)
                self._issue(
                    "ambiguous_move",
                    f"{token.raw!r} was ambiguous; chose {fixed.detail['canonical']}",
                    severity="warning",
                    page=seg.page,
                    node_id=node.id,
                    source_text=token.raw,
                    bbox=seg.bbox,
                    context=fixed.detail,
                )
                return node, True
            self.counter["ambiguous"] += 1
            self._issue(
                "ambiguous_move",
                f"{token.raw!r} is ambiguous and no unique legal move could be chosen",
                severity="warning",
                page=seg.page,
                source_text=token.raw,
                bbox=seg.bbox,
                context=resolution.detail,
            )

        # (1) does the stream continue cleanly if we treat this as prose?
        if self._skip_keeps_stream_valid(piece, seg, token, side):
            self.counter["rejected_as_prose"] += 1
            self._issue(
                "move_rejected_as_prose",
                f"{token.raw!r} is not legal here; treated as prose (next move fits)",
                severity="info",
                page=seg.page,
                source_text=token.raw,
                bbox=seg.bbox,
                context=resolution.detail,
            )
            self.pending_comment.append(
                PendingComment(
                    node_id=self.path[-1],
                    text=token.raw,
                    source=SourceRef(page=seg.page, bbox=seg.bbox),
                    signals={"preceding_move": 0.5, "rejected_token": 1.0},
                    seg_index=seg.index,
                )
            )
            return None, True

        # (2) is it legal from a *nearby* ancestor position?  (author skipped moves)
        rescued = (
            self._resync_ancestor(token, side)
            if self._resyncs < self.MAX_RESYNCS_PER_SEGMENT
            else None
        )
        if rescued is not None:
            self._resyncs += 1
        if rescued is not None:
            node_id, board = rescued
            self._truncate_to(node_id)
            mv = nz.san_candidates(board, token_body(token)).move
            if mv is None:
                return None, True
            fixed = nz.Resolution(
                mv,
                "repaired",
                reason="resync_to_ancestor",
                detail={"as_written": token.raw},
                candidates=[mv],
                confidence=0.55,
            )
            node = self._push(fixed, token, seg, side, board.fullmove_number, "resync")
            self.mainline_legal = False
            self._issue(
                "resync",
                f"Move stream jumped; {token.raw!r} attached at move "
                f"{board.fullmove_number} after a gap",
                severity="warning",
                page=seg.page,
                node_id=node.id,
                source_text=token.raw,
                bbox=seg.bbox,
            )
            return node, True

        self.counter["invalid"] += 1
        self.mainline_legal = False
        self._issue(
            "illegal_move",
            f"{token.raw!r} could not be legally applied",
            severity="error",
            page=seg.page,
            source_text=token.raw,
            bbox=seg.bbox,
            context=resolution.detail,
        )
        return None, True

    def _disambiguate(
        self, resolution: nz.Resolution, token: ParsedMove, side: Literal["w", "b"]
    ) -> chess.Move | None:
        """Prefer the candidate that matches printed side / capture glyph."""
        cands = list(resolution.candidates)
        if not cands:
            return None
        board = self.board
        nz.set_side(board, side)
        same_side = list(cands)
        pool = same_side or cands
        if len(pool) == 1:
            return pool[0]
        if "x" in token.raw.lower():
            captures = [m for m in pool if board.is_capture(m)]
            if len(captures) == 1:
                return captures[0]
        # last resort: a piece that just moved can often repeat the square
        moved = [m for m in pool if board.piece_at(m.from_square)]
        return pool[0] if moved else None

    def _skip_keeps_stream_valid(
        self, piece: nz.Piece, seg: Segment, token: ParsedMove, side: Literal["w", "b"]
    ) -> bool:
        """Look ahead: if the *next* token fits, this one was noise."""
        board = self.board.copy()
        nz.set_side(board, side)
        probe = nz.tokenize(seg.text[piece.end :], page=seg.page)
        for nxt in probe:
            if nxt.kind != nz.PieceKind.MOVE or nxt.move is None:
                continue
            # The next move belongs to whoever is *actually* to move; the failed
            # token's printed side says nothing about that.  Try both.
            options = [nxt.side] if nxt.side else ["w", "b"]
            for candidate_side in options:
                nb = nz.set_side(board.copy(), candidate_side)
                if nz.san_candidates(nb, token_body(nxt.move)).move is not None:
                    return True
            return False
        return False

    #: How far back we are willing to rewind when the stream jumps.  Rewinding
    #: further almost always means we are chasing our own tail.
    RESYNC_WINDOW = 8
    #: Consecutive hard failures before we stop trusting the stream.
    FAILURE_LIMIT = 6
    #: How many times we may rewind the stream *within one line* before giving
    #: up.  Each rewind risks compounding OCR damage, but a single misread line
    #: must not poison the rest of the game, so the budget resets per segment.
    MAX_RESYNCS_PER_SEGMENT = 3

    def _resync_ancestor(
        self, token: ParsedMove, side: Literal["w", "b"]
    ) -> tuple[str, chess.Board] | None:
        """Rewind a *bounded* number of plies looking for the right position.

        A resync is only justified when the printed move number suggests the
        author skipped whole moves; otherwise we would happily jump to a
        completely different game.
        """
        here = self.board.fullmove_number
        for node_id in reversed(self.path[-(self.RESYNC_WINDOW + 1) : -1]):
            node = self.nodes[node_id]
            if token.move_number is not None and node.move_number > token.move_number:
                continue
            board = nz.set_side(chess.Board(node.fen_after), side)
            if board.fullmove_number > here and token.move_number is not None:
                pass
            if nz.san_candidates(board, token_body(token)).move is not None:
                return node_id, board
        return None

    def _reject(self, piece: nz.Piece, seg: Segment, reason: str, token: ParsedMove) -> None:
        self.counter["invalid"] += 1
        self._issue(
            "unparsed_move",
            f"{token.raw!r} could not be applied ({reason})",
            severity="warning",
            page=seg.page,
            source_text=token.raw,
            bbox=seg.bbox,
        )

    # -- commentary ------------------------------------------------------

    def _flush_comments_if_new_segment(self, seg: Segment) -> None:
        """Prose belongs to the move it follows -- close the run when the
        document moves on to another line."""
        if self.pending_comment and self.pending_comment[0].seg_index != seg.index:
            self._flush_comments()

    def _flush_comments(self, force: bool = False) -> None:
        if not self.pending_comment:
            return
        parts = [pc.text.strip() for pc in self.pending_comment if pc.text.strip()]
        joined = " ".join(" ".join(parts).split())
        if not joined:
            self.pending_comment.clear()
            return
        first = self.pending_comment[0]
        node_id = first.node_id
        signals: dict[str, float] = {}
        for pc in self.pending_comment:
            for k, v in pc.signals.items():
                signals[k] = max(signals.get(k, 0.0), v)
        signals["adjacent_move"] = 0.2 if force else 1.0
        self.counter["comments"] += 1
        self.comments.append(
            Comment(
                id=f"{self.game_id}_c{len(self.comments) + 1}",
                text=joined,
                seg_index=first.seg_index,
                source=first.source,
                attached_to=node_id,
                confidence=0.0,
                signals=signals,
                anchor_kind="preceding_move",
            )
        )
        self.pending_comment.clear()

    def flush_for_page(self) -> None:
        self._flush_comments()

    def _record_reference(self, piece: nz.Piece, seg: Segment) -> None:
        token = piece.move
        if token is None:
            return
        self.inline_refs.append(
            InlineRef(
                page=seg.page,
                text=token.raw,
                san=token.raw,
                move_number=token.move_number,
                side=token.side,
                node_id=None,
                bbox=seg.bbox,
            )
        )
        # The prose itself still belongs to the last move discussed.
        self.pending_comment.append(
            PendingComment(
                node_id=self.path[-1],
                text=piece.text,
                source=SourceRef(page=seg.page, bbox=seg.bbox),
                signals={"preceding_move": 0.8, "inline_move": 0.6},
                seg_index=seg.index,
            )
        )

    # -- result ----------------------------------------------------------

    def _result(self, midgame_start: str | None = None) -> TreeResult:
        main_ids = set(self._mainline_ids)
        ordered_nodes = sorted(self.nodes.values(), key=lambda n: (n.ply, n.id))
        for node in ordered_nodes:
            node.is_mainline = node.id in main_ids
        final = self.boards[-1].fen() if self.boards else START_FEN
        result = TreeResult(
            nodes=ordered_nodes,
            comments=self.comments,
            issues=self.issues,
            inline_refs=self.inline_refs,
            root_id=self.root_id,
            final_fen=final,
            result=self.result,
            stats=dict(self.counter),
            mainline_legal=self.mainline_legal,
            fen_consistent=self.fen_consistent,
            node_by_id=self.nodes,
        )
        if midgame_start:
            result.stats["midgame_start"] = 1
            result.stats["nodes"] = 0
        result.stats["nodes"] = len(self.nodes)
        return result


_OPENS_WITH_NOTATION = re.compile(
    r"^\s*(?:\d{1,3}\s*\.{1,3}\s*)?[KQRBNP]?[a-h]?[x×:]?[a-h][1-8]"
    r"|^\s*\d{1,3}\s*\.{1,3}"
    r"|^\s*[0OoQq]{1,2}\s*[-–—]",
    re.IGNORECASE,
)


def _opens_with_notation(text: str) -> bool:
    return bool(_OPENS_WITH_NOTATION.match(text.strip()))


def token_body(token: ParsedMove) -> str:
    """The SAN payload of a parsed token (never the printed move number)."""
    return token.body or token.raw


def nag_for(annotation: str) -> str:
    table = {
        "!": "$1", "??": "$3", "!?": "$5", "?!": "$6", "!!": "$2", "e": "$4",
    }
    return table.get(annotation, "")


def _confidence_for(resolution: nz.Resolution, token: ParsedMove, origin: Origin) -> float:
    base = resolution.confidence
    if base <= 0:
        base = 0.5
    if origin == "mainline":
        base += 0.1
    elif origin in ("repeated_number", "parenthesis"):
        base -= 0.05
    elif origin in ("indent", "resync"):
        base -= 0.2
    if token.move_number is not None:
        base += 0.05
    if token.annotation:
        base += 0.02
    return max(0.0, min(1.0, base))


def build_tree(
    segments: list[Segment],
    *,
    game_id: str = "game",
    root_fen: str = START_FEN,
) -> TreeResult:
    return MoveTreeBuilder(game_id, root_fen).run(segments)