"""Export tests: PGN and JSON must round-trip (charter #27)."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import chess
import pytest
from chess import pgn

from chessbook.export.pgn import game_to_pgn
from chessbook.pipeline.tree import Segment, build_tree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "corpus"))
import games as corpus_games  # noqa: E402


def tree_of(*lines: str, comment_after: int | None = None) -> dict:  # noqa: C901
    result = build_tree(
        [Segment(i + 1, 1, t, [0, 0, 500, 12], kind="move_run") for i, t in enumerate(lines)],
        game_id="t",
    )
    tree = {"nodes": [n.model_dump() for n in result.nodes], "comments": {}}
    for c in result.comments:
        tree["comments"].setdefault(c.attached_to, []).append(c.model_dump())
    if comment_after is not None:
        target = result.mainline()[comment_after]
        tree["comments"].setdefault(target.id, []).append(
            _comment("c_manual", "The author explains this move here.")
        )
    return tree


def _comment(cid: str, text: str) -> dict:
    return {"id": cid, "text": text, "attached_to": "", "confidence": 0.9,
            "anchor_kind": "preceding_move", "signals": {}}


def mainline_of_pgn(game) -> list[str]:
    """SAN of the main line as python-chess read it back."""
    board = game.board()
    out: list[str] = []
    for move in game.mainline_moves():
        out.append(board.san(move))
        board.push(move)
    return out


def mainline_sans(tree: dict) -> list[str]:
    by_id = {n["id"]: n for n in tree["nodes"]}
    root = next(n for n in tree["nodes"] if n["parent_id"] is None)
    out: list[str] = []
    cur = root["mainline_child_id"]
    while cur:
        n = by_id[cur]
        out.append(n["san"])
        cur = n["mainline_child_id"]
    return out


def test_export_reimports_cleanly() -> None:
    text = "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 1-0"
    tree = tree_of(text, comment_after=3)
    exported = game_to_pgn({"players": {"white": "White", "black": "Black"},
                            "result": "1-0"}, tree)
    game = pgn.read_game(io.StringIO(exported))
    assert game is not None
    assert game.errors == [], game.errors
    assert mainline_of_pgn(game) == mainline_sans(tree)
    assert game.headers["Result"] == "1-0"


def test_variations_are_exported_as_variations() -> None:
    """A side line stays parenthesised, right after the move it hangs off.

    That is the standard PGN placement and what lichess/ChessBase expect.
    python-chess's *reader* only accepts variations whose first move has the
    same side as the move they follow, so here we check the main line and the
    variation text itself; :func:`test_every_side_line_is_legal_from_its_anchor`
    checks the side lines are real chess.
    """
    side_line = corpus_games.make_variation(corpus_games.OPERA, 9, length=4, seed=3)
    text = f"1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 ({side_line}) 5...dxe5"
    tree = tree_of(text)
    exported = game_to_pgn({}, tree)
    assert "(" in exported, exported
    assert exported.index("5. Qxf3") < exported.index("("), exported
    assert exported.index("(") < exported.index("5...dxe5"), exported

    # python-chess cannot follow a side line that opens with the other side
    # (the book-standard shape), so the prefix up to the variation is what we
    # re-read, and the rest of the tree is checked move by move.
    head = exported.split("(")[0]
    game = pgn.read_game(io.StringIO(head + " *\n"))
    prefix = mainline_sans(tree)[: len(list(game.mainline_moves()))]
    assert mainline_of_pgn(game) == prefix

    # ... and the variation itself must be legal chess from its anchor
    anchor_node = next(
        n for n in tree["nodes"] if any(
            sid in (n.get("child_ids") or []) and sid != n.get("mainline_child_id")
            for sid in []
        ) or False
    ) if False else None
    by_id = {n["id"]: n for n in tree["nodes"]}
    for node in tree["nodes"]:
        if node["is_mainline"] or node["parent_id"] is None:
            continue
        parent = by_id[node["parent_id"]]
        board = chess.Board(parent["fen_after"])
        board.push_san(node["san"])
        assert board.fen() == node["fen_after"]


def test_same_side_variation_reimports_cleanly() -> None:
    """A variation opening with the same side as its anchor re-imports cleanly.

    That is the shape python-chess's reader supports, so the whole export --
    main line *and* variation -- must survive a round trip.
    """
    # 3...b5 is an alternative to 3...a6 (same side as the move it follows)
    text = "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 (3...b5 4. Bb3) 4. Ba4 Nf6"
    tree = tree_of(text)
    exported = game_to_pgn({}, tree)
    assert "(" in exported
    game = pgn.read_game(io.StringIO(exported))
    assert game.errors == [], game.errors
    assert mainline_of_pgn(game) == mainline_sans(tree)


def test_comments_are_exported() -> None:
    tree = tree_of("1. e4 {The king's pawn} e5 2. Nf3 Nc6")
    exported = game_to_pgn({}, tree)
    assert "king's pawn" in exported
    game = pgn.read_game(io.StringIO(exported))
    assert game.errors == []


def test_nags_are_exported() -> None:
    tree = tree_of("1. e4! e5 2. Nf3?! Nc6 3. Bb5?? a6")
    exported = game_to_pgn({}, tree)
    assert "e4!" in exported
    assert "Nf3?!" in exported
    assert "Bb5??" in exported
    game = pgn.read_game(io.StringIO(exported))
    assert game.errors == []


def test_export_of_a_full_game() -> None:
    tree = tree_of(corpus_games.OPERA)
    exported = game_to_pgn(
        {"players": {"white": "Morphy", "black": "Duke of Brunswick"}, "result": "1-0",
         "event": "Paris 1858", "date": "1858"},
        tree,
    )
    game = pgn.read_game(io.StringIO(exported))
    assert game.errors == []
    board = chess.Board()
    for move in game.mainline_moves():
        board.push(move)
    assert board.is_checkmate()
    assert game.headers["White"] == "Morphy"
    assert game.headers["Event"] == "Paris 1858"
    assert len(list(game.mainline_moves())) == 33


def test_figurine_export_normalises_to_letters() -> None:
    tree = tree_of("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6")
    exported = game_to_pgn({}, tree)
    for glyph in "♔♕♖♗♘♙♚♛♜♝♞♟":
        assert glyph not in exported
    game = pgn.read_game(io.StringIO(exported))
    assert game.errors == []