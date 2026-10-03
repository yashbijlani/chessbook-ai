"""Unit tests for the chess notation layer (charter #7)."""

from __future__ import annotations

import chess
import pytest

from chessbook.pipeline import notation as nz


def moves_of(text: str) -> list[nz.Piece]:
    return [p for p in nz.tokenize(text) if p.kind == nz.PieceKind.MOVE]


def bodies(text: str) -> list[str]:
    return [p.move.body for p in moves_of(text)]


# --------------------------------------------------------------------------
# #7 token shapes
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.e4", ["e4"]),
        ("1. e4", ["e4"]),
        ("1...c5", ["c5"]),
        ("1... c5", ["c5"]),
        ("23.Nf3", ["Nf3"]),
        ("23...Nf6", ["Nf6"]),
        ("23. Nf3", ["Nf3"]),
        ("23... Nf6", ["Nf6"]),
        ("23...Nf6! 24.Qh4??", ["Nf6", "Qh4"]),
        ("1. e4+ e5#", ["e4+", "e5#"]),
        ("1. O-O", ["O-O"]),
        ("1. O-O-O", ["O-O-O"]),
        ("1. 0-0", ["0-0"]),
        ("1. 0-0-0", ["0-0-0"]),
        ("1. e8=Q+", ["e8=Q+"]),
        ("1. e8Q", ["e8Q"]),
        ("1. exd8=N+", ["exd8=N+"]),
        ("1. Qxd5", ["Qxd5"]),
        ("1. Nxe4", ["Nxe4"]),
        ("1. exd5", ["exd5"]),
        ("12. exd5 e.p.", ["exd5"]),
        ("1. Qh5+", ["Qh5+"]),
        ("1. e4 e5 2. Nf3", ["e4", "e5", "Nf3"]),
        ("1. bxc3", ["bxc3"]),
        ("1. Bxc3", ["Bxc3"]),
    ],
)
def test_token_shapes(text: str, expected: list[str]) -> None:
    assert bodies(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("♘f3 ♗b5 ♕h5", ["Nf3", "Bb5", "Qh5"]),
        ("1. ♞c6", ["Nc6"]),
        ("1. ♚e8", ["Ke8"]),
        ("♙e4 ♟e5", ["Pe4", "Pe5"]),
    ],
)
def test_figurine_notation(text: str, expected: list[str]) -> None:
    assert bodies(text) == expected


def test_figurine_is_reported() -> None:
    assert moves_of("1. ♘f3")[0].move.is_figurine is True
    assert moves_of("1. ♞f3")[0].move.is_figurine is True
    assert moves_of("1. Nf3")[0].move.is_figurine is False


@pytest.mark.parametrize(
    ("text", "result"),
    [("1-0", "result"), ("0-1", "result"), ("1/2-1/2", "result"), ("*", "result")],
)
def test_result_tokens(text: str, result: str) -> None:
    kinds = {p.kind.value for p in nz.tokenize(text)}
    assert kinds == {result}


def test_castling_beats_result_shape() -> None:
    """'0-0' is castling, not a '0-0' result."""
    assert bodies("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. 0-0")[-1] == "0-0"


def test_braces_become_structure() -> None:
    kinds = [p.kind.value for p in nz.tokenize("1. e4 {A standard position} e5")]
    assert "open" in kinds and "close" in kinds


def test_nag_is_captured() -> None:
    pieces = moves_of("23... Nf6! 24. Qh4?? 25. Rxh6?!")
    assert [p.move.annotation for p in pieces] == ["!", "??", "?!"]
    pieces = moves_of("17. Rd8#")
    assert pieces[0].move.san is None  # resolved later


def test_prose_is_not_a_move() -> None:
    assert bodies("The point of the game is obvious") == []
    assert bodies("After 2...Nc6 White can play 3.d4") == ["Nc6", "d4"]


def test_move_numbers_are_not_tokens() -> None:
    pieces = nz.tokenize("1. e4 e5 2. Nf3")
    texts = [p.text for p in pieces if p.kind == nz.PieceKind.TEXT]
    assert all(t.strip() in {"", "."} or "e4" not in t for t in texts)


# --------------------------------------------------------------------------
# #6 legality
# --------------------------------------------------------------------------

START = chess.Board()


def san_tokens(pgn: str) -> list[str]:
    """'1. e4 e5' -> ['e4', 'e5']"""
    import re

    out = []
    for t in pgn.split():
        t2 = re.sub(r"^\d{1,3}\.{1,3}", "", t)
        if t2:
            out.append(t2)
    return out


@pytest.mark.parametrize(
    ("prelude", "san", "uci"),
    [
        ("", "e4", "e2e4"),
        ("", "Nf3", "g1f3"),
        ("", "d4", "d2d4"),
        ("", "c4", "c2c4"),
        ("1. e4", "e5", "e7e5"),
        ("1. e4 d5", "exd5", "e4d5"),
        ("1. e4 e5 2. Nf3", "Nc6", "b8c6"),
        ("1. e4 e5 2. Nf3 Nc6 3. Bc4", "Bc5", "f8c5"),
    ],
)
def test_legal_moves_resolve(prelude: str, san: str, uci: str) -> None:
    board = START.copy()
    for t in san_tokens(prelude):
        board.push_san(t)
    res = nz.san_candidates(board, san)
    assert res.status == "ok", res.detail
    assert res.move.uci() == uci


def test_every_legal_move_resolves_from_its_own_san() -> None:
    """Property: for any position and legal move, resolving its SAN is exact."""
    import random

    rng = random.Random(20240501)
    board = START.copy()
    for _ in range(120):
        legal = list(board.legal_moves)
        if not legal:
            break
        for m in legal:
            san = board.san(m)
            res = nz.san_candidates(board, san)
            assert res.status == "ok", (board.fen(), san, res.detail)
            assert res.move == m, (board.fen(), san)
        board.push(rng.choice(legal))


def test_illegal_is_reported_not_guessed() -> None:
    res = nz.san_candidates(START, "Nf3")
    assert res.status == "ok"
    res = nz.san_candidates(START, "Nc6")
    assert res.status == "illegal"
    assert res.move is None


def test_piece_mismatch_is_never_silently_accepted() -> None:
    """Charter #17: 'Nxf7' where only a bishop reaches f7 must be rejected."""
    board = chess.Board.empty()
    board.set_piece_at(chess.E8, chess.Piece(chess.KING, chess.BLACK))
    board.set_piece_at(chess.E1, chess.Piece(chess.KING, chess.WHITE))
    board.set_piece_at(chess.F7, chess.Piece(chess.PAWN, chess.BLACK))
    board.set_piece_at(chess.E6, chess.Piece(chess.BISHOP, chess.WHITE))
    board.set_piece_at(chess.E2, chess.Piece(chess.KNIGHT, chess.WHITE))
    assert nz.san_candidates(board, "Bxf7").status == "ok"
    res = nz.san_candidates(board, "Nxf7")
    assert res.status == "illegal"
    assert res.reason == "piece_mismatch"
    assert res.move is None


def test_wrong_origin_file_is_rejected() -> None:
    """'cxd5' must not be silently rewritten as 'd5'."""
    board = chess.Board("4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1")
    res = nz.san_candidates(board, "cxd5")
    assert res.move is None
    assert res.reason == "piece_mismatch"


def test_ambiguity_is_reported() -> None:
    """Two knights can both reach f3; bare 'Nf3' must be refused."""
    board = _two_knights_for_a_pawn()
    res = nz.san_candidates(board, "Nd4")
    assert res.status == "ambiguous"
    assert len(res.candidates) >= 2


def test_castling_normalisation_is_recorded() -> None:
    board = chess.Board("r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 4 4")
    res = nz.san_candidates(board, "0-0")
    assert res.status == "repaired"
    assert res.reason == "castling_normalised"
    assert res.move.uci() == "e1g1"


def _two_knights_for_a_pawn() -> chess.Board:
    """Both white knights attack the black pawn on d4."""
    board = chess.Board.empty()
    board.set_piece_at(chess.E8, chess.Piece(chess.KING, chess.BLACK))
    board.set_piece_at(chess.E1, chess.Piece(chess.KING, chess.WHITE))
    board.set_piece_at(chess.D4, chess.Piece(chess.PAWN, chess.BLACK))
    board.set_piece_at(chess.C2, chess.Piece(chess.KNIGHT, chess.WHITE))
    board.set_piece_at(chess.E2, chess.Piece(chess.KNIGHT, chess.WHITE))
    return board


def test_redundant_disambiguation_is_a_repair() -> None:
    """Both knights can take d4; the written letter makes the move unique."""
    board = _two_knights_for_a_pawn()
    assert len([m for m in board.legal_moves if m.to_square == chess.D4]) == 2
    assert nz.san_candidates(board, "Nxd4").status == "ambiguous"
    res = nz.san_candidates(board, "Ncxd4")
    assert res.status in ("ok", "repaired")
    assert res.move.uci() == "c2d4"
    res = nz.san_candidates(board, "Ned4")
    assert res.status in ("ok", "repaired")
    assert res.move.uci() == "e2d4"


def test_forbidden_ocr_substitutions() -> None:
    """We never swap a piece letter for another piece letter (#17)."""
    assert ("N", "B") in nz.FORBIDDEN_SUBSTITUTIONS
    assert ("Q", "R") in nz.FORBIDDEN_SUBSTITUTIONS
    assert ("B", "N") in nz.FORBIDDEN_SUBSTITUTIONS


def test_san_key_refuses_contradictions() -> None:
    written = nz.san_parts("cxd5")
    other = nz.san_parts("d5")
    assert written is not None and other is not None
    assert not written.compatible_with(other)
    written = nz.san_parts("d5")
    other = nz.san_parts("Nxd5")
    assert not written.compatible_with(other)
    # an omitted disambiguation letter is less specific, never contradictory
    assert nz.san_parts("Nxd4").compatible_with(nz.san_parts("Ncxd4"))
    # rank disambiguation is understood
    assert nz.san_parts("N7c6") is not None


def test_density_helper() -> None:
    moves, total = nz.token_density("1. e4 e5 2. Nf3 the rest is prose")
    assert moves == 3
    assert total > 3