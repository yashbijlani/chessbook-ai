"""Move-tree reconstruction tests (charter #6, #9, #17)."""

from __future__ import annotations

import sys
from pathlib import Path

import chess
import pytest

from chessbook.pipeline import notation as nz
from chessbook.pipeline.tree import Segment, build_tree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "corpus"))
import games as corpus_games  # noqa: E402  (verified legal chess content)

OPERA = (
    "1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 "
    "7. Qb3 Qe7 8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 "
    "12. O-O-O Rd8 13. Rxd7 Rxd7 14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0"
)

EVERGREEN = (
    "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. b4 Bxb4 5. c3 Ba5 6. d4 exd4 7. O-O d3 "
    "8. Qb3 Qf6 9. e5 Qg6 10. Re1 Nge7 11. Ba3 b5 12. Qxb5 Rb8 13. Qa4 Bb6 "
    "14. Nbd2 Bb7 15. Ne4 Qf5 16. Bxd3 Qh5 17. Nf6+ gxf6 18. exf6 Rg8 "
    "19. Rad1 Qxf3 20. Rxe7+ Nxe7 21. Qxd7+ Kxd7 22. Bf5+ Ke8 23. Bd7+ Kf8 24. Bxe7# 1-0"
)


def segs(*lines: str, kind: str = "move_run", indent: int = 0) -> list[Segment]:
    return [
        Segment(i + 1, 1, text, [54, 50 + i * 14, 400, 60 + i * 14], kind=kind, indent=indent)
        for i, text in enumerate(lines)
    ]


def san_of(nodes: list) -> str:
    return " ".join(
        (f"{n.move_number}." if n.side == "w" else f"{n.move_number}...") + (n.san or "?")
        for n in nodes[1:]
    )


def assert_mainline_legal(result, root_fen: str = chess.STARTING_FEN) -> chess.Board:
    """Charter #31: replay the main line and prove it is real chess."""
    board = chess.Board(root_fen)
    for node in result.mainline()[1:]:
        assert node.fen_before == board.fen(), (
            f"FEN before {node.san} disagrees with the replay\n stored: {node.fen_before}"
            f"\n replay: {board.fen()}"
        )
        move = board.parse_san(node.san)
        assert move.uci() == node.move_uci, f"{node.san}: uci mismatch"
        board.push(move)
        assert board.fen() == node.fen_after, f"FEN after {node.san} disagrees"
    return board


# --------------------------------------------------------------------------


def test_clean_game_is_exact() -> None:
    result = build_tree(segs(OPERA), game_id="g")
    board = assert_mainline_legal(result)
    assert len(result.mainline()) - 1 == 33
    assert board.is_checkmate()
    assert result.result == "1-0"
    assert result.stats["invalid"] == 0
    assert result.stats["ambiguous"] == 0
    assert not [i for i in result.issues if i.severity.value == "error"]


def test_second_clean_game() -> None:
    result = build_tree(segs(EVERGREEN), game_id="g")
    assert_mainline_legal(result)
    assert len(result.mainline()) - 1 == 47
    assert result.stats["invalid"] == 0


def test_number_spacing_variants() -> None:
    for text, plies in (
        ("1.e4 e5 2.Nf3 Nc6 3.Bb5 a6", 6),
        ("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6", 6),
        ("1.  e4   e5   2.  Nf3  Nc6   3.  Bb5  a6", 6),
        ("1.e4 e5 2.Nf3 Nc6 3.Bb5 a6 4.Ba4 Nf6 5.O-O Be7", 10),
    ):
        result = build_tree(segs(text), game_id="g")
        assert_mainline_legal(result)
        assert len(result.mainline()) - 1 == plies, text


def test_parenthesised_variation_does_not_flatten() -> None:
    # the side line is anchored after 5.Qxf3, where its own numbering starts
    side_line = corpus_games.make_variation(OPERA, 9, length=4, seed=7)
    text = f"1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 ({side_line}) 5...dxe5"
    result = build_tree(segs(text), game_id="g")
    assert_mainline_legal(result)
    mainline = result.mainline()
    assert [n.san for n in mainline[1:]] == ["e4", "e5", "Nf3", "d6", "d4", "Bg4", "dxe5",
                                            "Bxf3", "Qxf3", "dxe5"]
    variations = [n for n in result.nodes if not n.is_mainline and n.parent_id]
    expected = [
        nz.strip_noise(tok) for tok in side_line.split() if not tok.endswith((".", "..."))
    ]
    assert len(variations) == 4
    assert [n.san for n in variations] == expected
    # the variation hangs off the node *before* the move it replaces
    first = variations[0]
    assert result.node_by_id[first.parent_id].san == "Qxf3"
    assert first.started_by == "parenthesis"
    assert all(n.is_mainline for n in mainline[1:])


def test_indented_variation_line() -> None:
    side_line = corpus_games.make_variation(OPERA, 21, length=3, seed=13)
    lines = segs(OPERA.replace(" 1-0", ""), f"    {side_line}")
    result = build_tree(lines, game_id="g")
    assert_mainline_legal(result)
    assert [n.san for n in result.mainline()[1:]] == corpus_games.san_tokens(OPERA)
    var = [n for n in result.nodes if not n.is_mainline]
    assert len(var) == 3
    assert [n.ply for n in var] == [22, 23, 24]
    assert result.node_by_id[var[0].parent_id].ply == 21


def test_numbered_indented_variation() -> None:
    side_line = corpus_games.make_variation(OPERA, 27, length=3, seed=11)
    lines = segs(OPERA.replace(" 1-0", ""), f"    {side_line}")
    result = build_tree(lines, game_id="g")
    assert_mainline_legal(result)
    assert result.mainline()[-1].san == "Rd8#"
    var = [n for n in result.nodes if not n.is_mainline]
    assert len(var) == 3
    assert var[0].started_by in ("repeated_number", "parenthesis")
    # the variation hangs off the last main line node, replacing its final move
    anchor = result.node_by_id[var[0].parent_id]
    assert anchor.is_mainline and anchor.ply == 27
    assert [n.ply for n in var] == [28, 29, 30]


def test_brace_comment_attaches_to_preceding_move() -> None:
    result = build_tree(
        segs("1. e4 {The king's pawn opening} e5 2. Nf3 Nc6 3. Bb5 a6"), game_id="g"
    )
    assert_mainline_legal(result)
    assert len(result.comments) == 1
    comment = result.comments[0]
    node = result.node_by_id[comment.attached_to]
    assert node.san == "e4"
    assert "king's pawn" in comment.text


def test_prose_between_move_runs_is_commentary_not_moves() -> None:
    result = build_tree(
        segs(
            "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6",
            "The Ruy Lopez is the most examined opening in chess.",
            kind="prose",
        ),
        game_id="g",
    )
    assert_mainline_legal(result)
    assert [n.san for n in result.mainline()[1:]] == ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6",
                                                       "Ba4", "Nf6"]
    assert any("Ruy Lopez" in c.text for c in result.comments)


def test_inline_reference_is_linked() -> None:
    result = build_tree(
        segs(
            "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7",
            "After 4...Nf6 the king is safe.",
            kind="prose",
        ),
        game_id="g",
    )
    from chessbook.pipeline.commentary import link_inline_refs

    link_inline_refs(result, result.inline_refs)
    assert_mainline_legal(result)
    linked = [r for r in result.inline_refs if r.node_id]
    assert linked, "at least one prose reference should resolve to a node"
    node = result.node_by_id[linked[0].node_id]
    assert node.san == "Nf6"


def test_midgame_fragment_is_refused_not_guessed() -> None:
    result = build_tree(segs("12...Nxd4 13. Bxd4 Qxd4 14. Nc3"), game_id="g")
    codes = [i.code for i in result.issues]
    assert "game_starts_midgame" in codes
    assert len(result.mainline()) - 1 == 0
    assert any(i.severity.value == "error" for i in result.issues)


def test_fragment_with_supplied_fen_is_accepted() -> None:
    """With a caller-supplied starting position a mid-game fragment is fine."""
    toks = corpus_games.san_tokens(OPERA)
    cut = 24
    anchor = corpus_games.board_after(OPERA, cut)
    fragment = " ".join(toks[cut : cut + 3])
    number = anchor.fullmove_number
    side = "w" if anchor.turn == chess.WHITE else "b"
    assert len(toks[cut]) > 2 and fragment, fragment
    result = build_tree(segs(fragment), game_id="g", root_fen=anchor.fen())
    assert_mainline_legal(result, anchor.fen())
    assert len(result.mainline()) - 1 == 3, fragment


def test_illegal_move_is_reported_and_stream_survives() -> None:
    # after 4...Nf6 White's only knight is on f3, so "5. Nf3" cannot be played
    result = build_tree(
        segs("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. Nf3 Nxe4 6. d3"), game_id="g"
    )
    assert_mainline_legal(result)
    codes = {i.code for i in result.issues}
    assert "move_rejected_as_prose" in codes or "illegal_move" in codes, codes
    # The main line stops at the last position we can actually prove; nothing is
    # invented to bridge the gap (charter #17).
    assert [n.san for n in result.mainline()[1:]] == [
        "e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6"
    ]
    assert not [i for i in result.issues if i.code == "stream_abandoned"]


def test_ambiguous_move_is_surfaced() -> None:
    """A move two identical pieces could make must be reported, not guessed."""
    line = corpus_games.ambiguity_line()
    assert line, "corpus helper must find an ambiguous move"
    result = build_tree(segs(line), game_id="g")
    assert_mainline_legal(result)
    assert any(i.code == "ambiguous_move" for i in result.issues), [i.code for i in result.issues]
    assert result.stats["ambiguous"] + result.stats["valid"] > 0


def test_stream_is_abandoned_rather_than_guessed() -> None:
    junk = "12...Nxd4 13. Bxd4 Qxd4 14. Nc3 Qc5 15. Nb5 a6 16. Ka3 b6 17. Qb2 c6"
    result = build_tree(segs(junk), game_id="g")
    assert any(i.code == "game_starts_midgame" for i in result.issues)
    assert result.stats.get("nodes") == 1


def test_duplicate_branch_merges_instead_of_forking() -> None:
    """The same move twice must not create two identical children."""
    side_line = corpus_games.make_variation(EVERGREEN, 13, length=4, seed=17)
    result = build_tree(segs(EVERGREEN.replace(" 1-0", ""), f"    {side_line}"), game_id="g")
    assert_mainline_legal(result)
    for node in result.nodes:
        seen: dict[str, int] = {}
        for cid in node.child_ids:
            uci = result.node_by_id[cid].move_uci
            seen[uci] = seen.get(uci, 0) + 1
        assert all(v == 1 for v in seen.values()), "duplicate sibling moves"


def test_variation_parents_are_legal_positions() -> None:
    side_line = corpus_games.make_variation(EVERGREEN, 25, length=5, seed=19)
    result = build_tree(segs(EVERGREEN.replace(" 1-0", ""), f"    {side_line}"), game_id="g")
    for node in result.nodes:
        if node.parent_id is None or node.move_uci is None:
            continue
        board = chess.Board(result.node_by_id[node.parent_id].fen_after)
        assert chess.Move.from_uci(node.move_uci) in board.legal_moves


def test_every_node_has_fens_and_confidence() -> None:
    result = build_tree(segs(OPERA), game_id="g")
    for node in result.nodes:
        assert node.fen_before and node.fen_after
        chess.Board(node.fen_before)
        chess.Board(node.fen_after)
        assert 0.0 <= node.confidence <= 1.0
        if node.parent_id is None:
            continue
        assert node.source is not None and node.source.page == 1


def test_repaired_move_is_recorded() -> None:
    """0-0 for O-O is a repair and must be visible in the issue list."""
    result = build_tree(
        segs("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. 0-0 Be7"), game_id="g"
    )
    assert_mainline_legal(result)
    assert any(i.code == "move_repaired" for i in result.issues)
    assert result.mainline()[-1].san == "Be7"


def test_result_token_is_captured() -> None:
    for text, expected in [
        ("1. f3 e5 2. g4 Qh4# 0-1", "0-1"),
        ("1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0", "1-0"),
        ("1. e4 e5 2. Nf3 Nc6 1/2-1/2", "1/2-1/2"),
    ]:
        result = build_tree(segs(text), game_id="g")
        assert result.result == expected, text