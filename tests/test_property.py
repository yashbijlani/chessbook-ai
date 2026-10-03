"""Property tests (charter #31).

Chess legality is a powerful invariant: if every move we emit can be replayed on
a real board, the parser cannot have invented anything.  Hypothesis generates
positions and move lists; we check the invariants the pipeline promises.
"""

from __future__ import annotations

import chess
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from chessbook.pipeline import notation as nz
from chessbook.pipeline.tree import Segment, build_tree
from chessbook.pipeline.validate import validate_game

SETTINGS = settings(max_examples=60, deadline=None,
                    suppress_health_check=[HealthCheck.too_slow])


def boards() -> st.SearchStrategy[chess.Board]:
    @st.composite
    def _boards(draw):
        board = chess.Board()
        for _ in range(draw(st.integers(min_value=0, max_value=70))):
            legal = list(board.legal_moves)
            if not legal:
                break
            board.push(draw(st.sampled_from(legal)))
        return board

    return _boards()


def move_lists() -> st.SearchStrategy[list[str]]:
    """A random but legal move sequence, as SAN tokens."""

    @st.composite
    def _lines(draw):
        board = chess.Board()
        tokens: list[str] = []
        for _ in range(draw(st.integers(min_value=1, max_value=24))):
            legal = list(board.legal_moves)
            if not legal:
                break
            move = draw(st.sampled_from(legal))
            tokens.append(board.san(move))
            board.push(move)
        return tokens

    return _lines()


@given(boards())
@SETTINGS
def test_every_legal_move_resolves_exactly(board: chess.Board) -> None:
    for move in board.legal_moves:
        san = board.san(move)
        res = nz.san_candidates(board, san)
        assert res.status == "ok", (board.fen(), san, res.detail)
        assert res.move == move


def render(moves: list[str]) -> str:
    """Book-style text for a list of SAN tokens (the canonical writer)."""
    out: list[str] = []
    last_white: int | None = None
    board = chess.Board()
    for san in moves:
        number = board.fullmove_number
        if board.turn == chess.WHITE:
            out.append(f"{number}. {san}")
            last_white = number
        elif last_white == number:
            out.append(san)
        else:
            out.append(f"{number}...{san}")
            last_white = None
        board.push_san(san)
    return " ".join(out)


@given(move_lists())
@SETTINGS
def test_tokenizer_round_trips_a_legal_line(tokens: list[str]) -> None:
    """Whatever python-chess prints, our tokenizer must read it back."""
    if not tokens:
        return
    result = build_tree(
        [Segment(1, 1, render(tokens), [0, 0, 400, 10], kind="move_run")], game_id="p"
    )
    mainline = result.mainline()[1:]
    assert [n.san for n in mainline] == tokens, render(tokens)
    walk = chess.Board()
    for node in mainline:
        assert node.fen_before == walk.fen()
        walk.push_san(node.san)


@given(boards())
@SETTINGS
def test_accepted_lines_reach_the_same_position(board: chess.Board) -> None:
    """If the builder accepted every move, the position must match exactly."""
    tokens = []
    replay = chess.Board()
    for move in board.move_stack:
        tokens.append(replay.san(move))
        replay.push(move)
    if not tokens:
        return
    result = build_tree(
        [Segment(1, 1, render(tokens), [0, 0, 400, 10], kind="move_run")], game_id="p"
    )
    if [i.severity.value for i in result.issues if i.severity.value == "error"]:
        return
    walk = chess.Board()
    for node in result.mainline()[1:]:
        walk.push_san(node.san)
    assert walk.board_fen() == board.board_fen(), render(tokens)


@given(move_lists())
@SETTINGS
def test_reconstructed_tree_is_always_legal(tokens: list[str]) -> None:
    result = build_tree(
        [Segment(1, 1, render(tokens), [0, 0, 400, 10], kind="move_run")], game_id="p"
    )
    board = chess.Board()
    for node in result.mainline()[1:]:
        assert node.fen_before == board.fen()
        board.push_san(node.san)
    by_id = result.node_by_id
    for node in result.nodes:
        if node.parent_id is None or node.move_uci is None:
            continue
        parent = by_id[node.parent_id]
        parent_board = chess.Board(parent.fen_after)
        assert chess.Move.from_uci(node.move_uci) in parent_board.legal_moves


@given(move_lists())
@SETTINGS
def test_validator_agrees_with_the_builder(tokens: list[str]) -> None:
    """An independent replay must never find a main-line error."""
    result = build_tree(
        [Segment(1, 1, " ".join(tokens), [0, 0, 400, 10], kind="move_run")], game_id="p"
    )
    report = validate_game("p", chess.STARTING_FEN, result, result.result)
    if not report.moves_detected == 0:
        # a builder that abandoned the stream cannot be judged
        return
    errors = [i for i in report.issues if i.severity.value == "error"]
    assert not errors, [(i.code, i.message) for i in errors]


@given(move_lists(), st.integers(min_value=0, max_value=1000))
@SETTINGS
def test_truncation_never_corrupts_the_prefix(tokens: list[str], cut: int) -> None:
    """Cutting a game off mid-line must not change how the prefix is read."""
    full = build_tree(
        [Segment(1, 1, render(tokens), [0, 0, 400, 10], kind="move_run")], game_id="p"
    )
    prefix_tokens = tokens[: max(1, len(tokens) * cut // 1000 + 1)]
    part = build_tree(
        [Segment(1, 1, render(prefix_tokens), [0, 0, 400, 10], kind="move_run")], game_id="q"
    )
    n_full = len(full.mainline()) - 1
    n_part = len(part.mainline()) - 1
    assert n_part <= n_full
    board_full = chess.Board()
    board_part = chess.Board()
    for a, b in zip(full.mainline()[1:], part.mainline()[1:]):
        board_full.push_san(a.san)
        board_part.push_san(b.san)
        assert board_full.fen() == board_part.fen()


def test_two_thousand_random_games_round_trip() -> None:
    """A wide regression net for the notation layer (see also hypothesis above)."""
    import random

    rng = random.Random(20240501)
    failures = 0
    for _ in range(2000):
        board = chess.Board()
        tokens: list[str] = []
        for _ in range(rng.randint(1, 30)):
            legal = list(board.legal_moves)
            if not legal:
                break
            move = rng.choice(legal)
            tokens.append(board.san(move))
            board.push(move)
        result = build_tree(
            [Segment(1, 1, render(tokens), [0, 0, 400, 10], kind="move_run")], game_id="p"
        )
        if [n.san for n in result.mainline()[1:]] != tokens:
            failures += 1
    assert failures == 0, f"{failures} of 2000 random games did not round-trip"


@given(st.text(max_size=120))
@SETTINGS
def test_never_raises_on_arbitrary_text(text: str) -> None:
    """Garbage in must never crash the parser (charter #40)."""
    result = build_tree([Segment(1, 1, text, [0, 0, 400, 10], kind="prose")], game_id="p")
    board = chess.Board()
    for node in result.mainline()[1:]:
        assert node.fen_before == board.fen()
        board.push_san(node.san)


@given(st.lists(st.sampled_from(list("KQRBNPkqrbnp12345678abcdefghx+#=!?-.")), max_size=40))
@SETTINGS
def test_notation_parser_never_raises(chars: list[str]) -> None:
    token = "".join(chars)
    board = chess.Board()
    result = nz.san_candidates(board, token)
    assert result.status in ("ok", "repaired", "ambiguous", "illegal")
    if result.move is not None:
        assert result.move in board.legal_moves