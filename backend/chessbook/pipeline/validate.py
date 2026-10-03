"""Stage: independent validation (charter #16).

This module deliberately re-derives everything from the stored representation
instead of trusting the builder.  If the builder and the validator agree, the
model is probably right; if they disagree, the user gets a precise report.
"""

from __future__ import annotations

import chess
from collections import Counter

from ..models import (
    Diagram,
    GameReport,
    Node,
    PositionConfidence,
    Severity,
    ValidationIssue,
)
from .tree import TreeResult


def validate_game(
    game_id: str,
    root_fen: str,
    result: TreeResult,
    declared_result: str | None = None,
) -> GameReport:
    report = GameReport(game_id=game_id)
    by_id = result.node_by_id
    root = by_id.get(result.root_id)
    if root is None:
        report.issues.append(
            ValidationIssue(
                id=f"{game_id}_v0",
                severity=Severity.ERROR,
                code="missing_root",
                message="Move tree has no root node",
                game_id=game_id,
            )
        )
        report.mainline_legal = False
        return report

    # ---- mainline replay ------------------------------------------------
    board = chess.Board(root_fen)
    legal = True
    fen_ok = True
    seen_moves: list[str] = []
    for node in _mainline_chain(root, by_id)[1:]:
        report.moves_detected += 1
        if node.move_uci is None:
            report.invalid += 1
            report.issues.append(
                _issue(game_id, "missing_uci", f"{node.san!r} has no UCI move",
                       node=node, severity=Severity.ERROR)
            )
            legal = False
            continue
        try:
            move = chess.Move.from_uci(node.move_uci)
        except ValueError:
            report.invalid += 1
            report.issues.append(
                _issue(game_id, "malformed_uci", f"{node.move_uci!r} is not a valid UCI move",
                       node=node, severity=Severity.ERROR)
            )
            legal = False
            continue
        if move not in board.legal_moves:
            report.invalid += 1
            legal = False
            report.issues.append(
                _issue(
                    game_id,
                    "illegal_in_mainline",
                    f"{node.san!r} is not legal in the reconstructed position",
                    node=node,
                    severity=Severity.ERROR,
                    context={"fen": board.fen()},
                )
            )
            board.push_san("0000") if False else None
            continue
        expected_side = "w" if board.turn == chess.WHITE else "b"
        if node.side != expected_side:
            report.issues.append(
                _issue(
                    game_id,
                    "side_to_move_mismatch",
                    f"{node.san!r} is recorded as {'White' if node.side == 'w' else 'Black'}'s "
                    f"move but the board says otherwise",
                    node=node,
                    severity=Severity.WARNING,
                    context={"fen": board.fen()},
                )
            )
        if board.fen() != node.fen_before:
            fen_ok = False
            report.issues.append(
                _issue(
                    game_id,
                    "fen_mismatch",
                    f"FEN stored for {node.san!r} disagrees with the replayed position",
                    node=node,
                    severity=Severity.WARNING,
                    context={"stored": node.fen_before, "replayed": board.fen()},
                )
            )
        san = board.san(move)
        if node.san and san != node.san:
            report.issues.append(
                _issue(
                    game_id,
                    "san_mismatch",
                    f"SAN stored as {node.san!r} but python-chess reads it as {san!r}",
                    node=node,
                    severity=Severity.WARNING,
                )
            )
        if seen_moves and seen_moves[-1] == node.move_uci:
            report.issues.append(
                _issue(game_id, "duplicate_move",
                       f"{node.san!r} repeats the previous move with no opponent reply",
                       node=node, severity=Severity.WARNING)
            )
        seen_moves.append(node.move_uci)
        board.push(move)
        if board.fen() != node.fen_after:
            fen_ok = False
            report.issues.append(
                _issue(
                    game_id,
                    "fen_mismatch",
                    f"FEN stored after {node.san!r} disagrees with the replayed position",
                    node=node,
                    severity=Severity.WARNING,
                    context={"stored": node.fen_after, "replayed": board.fen()},
                )
            )
        report.valid += 1

    report.mainline_legal = legal
    report.fen_consistent = fen_ok

    # ---- result sanity --------------------------------------------------
    final = board
    if declared_result:
        if declared_result == "1-0" and not _is_mate(final, chess.BLACK):
            report.issues.append(
                _issue(
                    game_id,
                    "result_without_mate",
                    "Result 1-0 but the final position is not checkmate (normal for "
                    "resignation or time forfeit -- not an error)",
                    severity=Severity.INFO,
                )
            )
        if declared_result == "0-1" and not _is_mate(final, chess.WHITE):
            report.issues.append(
                _issue(
                    game_id,
                    "result_without_mate",
                    "Result 0-1 but the final position is not checkmate",
                    severity=Severity.INFO,
                )
            )
        if declared_result == "1/2-1/2" and final.is_checkmate():
            report.issues.append(
                _issue(
                    game_id,
                    "draw_but_mate",
                    "Result is a draw but the final position is checkmate",
                    severity=Severity.ERROR,
                )
            )

    # ---- variations -----------------------------------------------------
    for node in result.nodes:
        if node.parent_id is None:
            continue
        parent = by_id.get(node.parent_id)
        if parent is None:
            report.issues.append(
                _issue(game_id, "orphan_node", f"{node.san!r} references a missing parent",
                       node=node, severity=Severity.ERROR)
            )
            continue
        if node.move_uci is None:
            continue
        try:
            vb = chess.Board(parent.fen_after)
            mv = chess.Move.from_uci(node.move_uci)
        except ValueError:
            report.invalid += 1
            report.issues.append(
                _issue(game_id, "bad_parent_fen", f"Parent position of {node.san!r} is unreadable",
                       node=node, severity=Severity.ERROR)
            )
            continue
        if mv not in vb.legal_moves:
            report.invalid += 1
            report.issues.append(
                _issue(
                    game_id,
                    "illegal_variation_move",
                    f"Variation move {node.san!r} is not legal in its parent position",
                    node=node,
                    severity=Severity.ERROR,
                    context={"fen": parent.fen_after},
                )
            )
        if not node.is_mainline:
            report.variations += 1

    # ---- structural checks ----------------------------------------------
    _check_move_numbers(game_id, root, by_id, report)
    _check_mainline_pointers(game_id, root, by_id, report)

    # ---- carry over builder issues ---------------------------------------
    for issue in result.issues:
        report.issues.append(
            ValidationIssue(
                id=f"{game_id}_v{len(report.issues) + 1}",
                severity=issue.severity,
                code=issue.code,
                message=issue.message,
                page=issue.page,
                game_id=game_id,
                node_id=issue.node_id,
                source_text=issue.source_text,
                source=issue.source,
                context=issue.context,
            )
        )

    report.moves_detected = max(report.moves_detected, len(seen_moves))
    return report


def _mainline_chain(root: Node, by_id: dict[str, Node]) -> list[Node]:
    out = [root]
    cur = root
    seen = {root.id}
    while cur.mainline_child_id:
        nxt = by_id.get(cur.mainline_child_id)
        if nxt is None or nxt.id in seen:
            break
        seen.add(nxt.id)
        out.append(nxt)
        cur = nxt
    return out


def _check_move_numbers(
    game_id: str, root: Node, by_id: dict[str, Node], report: GameReport
) -> None:
    chain = _mainline_chain(root, by_id)[1:]
    expected = 1
    for node in chain:
        # Both halves of a full move share its number ("1. e4 e5"), so the
        # number only advances after Black has moved.
        want = expected
        if node.move_number != want:
            report.issues.append(
                _issue(
                    game_id,
                    "move_number_drift",
                    f"{node.san!r} is numbered {node.move_number} but replay expects {want}",
                    node=node,
                    severity=Severity.INFO,
                )
            )
        if node.side == "b":
            expected = node.move_number + 1


def _check_mainline_pointers(
    game_id: str, root: Node, by_id: dict[str, Node], report: GameReport
) -> None:
    for node in by_id.values():
        if node.mainline_child_id and node.mainline_child_id not in node.child_ids:
            report.issues.append(
                _issue(
                    game_id,
                    "mainline_pointer_orphan",
                    f"Main-line pointer of {node.san!r} is not in its child list",
                    node=node,
                    severity=Severity.WARNING,
                )
            )
        if node.mainline_child_id and node.mainline_child_id != node.child_ids[0] if node.child_ids else False:
            pass


def _is_mate(board: chess.Board, loser_turn: bool) -> bool:
    return board.is_checkmate() and board.turn == loser_turn


def _issue(
    game_id: str,
    code: str,
    message: str,
    node: Node | None = None,
    severity: Severity = Severity.WARNING,
    context: dict | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        id=f"{game_id}_v{id(code)}_{abs(hash(message)) % 99999}",
        severity=severity,
        code=code,
        message=message,
        game_id=game_id,
        node_id=node.id if node else None,
        page=node.source.page if node and node.source else None,
        source_text=node.source_text if node else None,
        source=node.source if node else None,
        context=context or {},
    )


def validate_diagrams(diagrams: list[Diagram], book_id: str = "book") -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if diagrams:
        low = sum(1 for d in diagrams if d.is_board_candidate)
        if low == 0:
            issues.append(
                ValidationIssue(
                    id=f"{book_id}_d0",
                    severity=Severity.INFO,
                    code="no_diagrams_detected",
                    message=(
                        f"{len(diagrams)} image/vector candidates were found but none "
                        "scored as a chessboard; position recognition is therefore "
                        "unavailable for this book"
                    ),
                    context={"candidates": len(diagrams)},
                )
            )
    for d in diagrams:
        if d.is_board_candidate and d.fen is None:
            issues.append(
                ValidationIssue(
                    id=f"{d.id}_nodep",
                    severity=Severity.INFO,
                    code="diagram_position_unknown",
                    message=(
                        f"Diagram on page {d.page} looks like a board (score "
                        f"{d.board_score:.2f}) but its position could not be derived"
                    ),
                    page=d.page,
                    source=__import__("chessbook.models", fromlist=["SourceRef"]).SourceRef(
                        page=d.page, bbox=d.bbox
                    ),
                    context={"board_score": d.board_score, "grid": d.grid_detected},
                )
            )
    return issues


def summarise(issues: list[ValidationIssue]) -> dict[str, int]:
    counts = Counter(i.severity.value for i in issues)
    return {
        "errors": counts.get("error", 0),
        "warnings": counts.get("warning", 0),
        "info": counts.get("info", 0),
        "total": len(issues),
    }


def confidence_for_diagram(fen: str | None, method: str | None) -> PositionConfidence:
    if not fen:
        return PositionConfidence.UNKNOWN
    if method == "inherited_from_preceding_moves":
        return PositionConfidence.EXACT
    if method == "mainline_node_on_page":
        return PositionConfidence.HIGH_CONFIDENCE
    if method == "nearest_preceding_move_run":
        return PositionConfidence.PROBABLE
    return PositionConfidence.UNKNOWN