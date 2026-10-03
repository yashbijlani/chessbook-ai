"""Verified chess content for the corpus.

Every game and every variation line in here is validated with ``python-chess``
when the corpus is built (``corpus/generate.py`` refuses to emit a PDF whose
content is not chess-legal).  Variations are *derived* from the mainlines with a
seeded chooser rather than typed by hand, because hand-written variations are
exactly where test data rots.
"""

from __future__ import annotations

import random
import re

import chess

# --------------------------------------------------------------------------
# Mainlines (all verified)
# --------------------------------------------------------------------------

OPERA = (
    "1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 "
    "7. Qb3 Qe7 8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 "
    "12. O-O-O Rd8 13. Rxd7 Rxd7 14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 "
    "17. Rd8# 1-0"
)

IMMORTAL = (
    "1. e4 e5 2. f4 exf4 3. Bc4 Qh4+ 4. Kf1 b5 5. Bxb5 Nf6 6. Nf3 Qh6 7. d3 Nh5 "
    "8. Nh4 Qg5 9. Nf5 c6 10. g4 Nf6 11. Rg1 cxb5 12. h4 Qg6 13. h5 Qg5 "
    "14. Qf3 Ng8 15. Bxf4 Qf6 16. Nc3 Bc5 17. Nd5 Qxb2 18. Bd6 Bxg1 19. e5 Qxa1+ "
    "20. Ke2 Na6 21. Nxg7+ Kd8 22. Qf6+ Nxf6 23. Be7# 1-0"
)

EVERGREEN = (
    "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. b4 Bxb4 5. c3 Ba5 6. d4 exd4 7. O-O d3 "
    "8. Qb3 Qf6 9. e5 Qg6 10. Re1 Nge7 11. Ba3 b5 12. Qxb5 Rb8 13. Qa4 Bb6 "
    "14. Nbd2 Bb7 15. Ne4 Qf5 16. Bxd3 Qh5 17. Nf6+ gxf6 18. exf6 Rg8 "
    "19. Rad1 Qxf3 20. Rxe7+ Nxe7 21. Qxd7+ Kxd7 22. Bf5+ Ke8 23. Bd7+ Kf8 "
    "24. Bxe7# 1-0"
)

FOOLS_MATE = "1. f3 e5 2. g4 Qh4# 0-1"

SIMPLE_QGD = (
    "1. d4 d5 2. c4 e6 3. Nc3 Nf6 4. Bg5 Be7 5. e3 O-O 6. Nf3 h6 7. Bh4 b6 "
    "8. cxd5 Nxd5 9. Bxe7 Qxe7 10. Nxd5 exd5 11. Rc1 Be6 1-0"
)

#: (name, pgn) pairs used across the corpus.
ALL_GAMES = [
    ("opera", OPERA),
    ("immortal", IMMORTAL),
    ("evergreen", EVERGREEN),
    ("simple_qgd", SIMPLE_QGD),
    ("fools_mate", FOOLS_MATE),
]


# --------------------------------------------------------------------------
# Verification helpers
# --------------------------------------------------------------------------


def san_tokens(pgn: str) -> list[str]:
    p = re.sub(r"\{[^}]*\}", "", pgn)
    p = re.sub(r"[!?]{1,2}", "", p)
    out: list[str] = []
    for t in p.split():
        if t in ("1-0", "0-1", "1/2-1/2", "½-½", "*"):
            break
        t2 = re.sub(r"^\d{1,3}\.{1,3}", "", t)
        if t2:
            out.append(t2)
    return out


def board_after(pgn: str, ply: int) -> chess.Board:
    board = chess.Board()
    for t in san_tokens(pgn)[:ply]:
        board.push_san(t)
    return board


def assert_legal(pgn: str, label: str = "line", base: chess.Board | None = None) -> int:
    board = (base or chess.Board()).copy()
    n = 0
    for t in san_tokens(pgn):
        board.push_san(t)
        n += 1
    return n


def make_variation(
    pgn: str,
    anchor_ply: int,
    length: int = 4,
    seed: int = 0,
) -> str:
    """A legal *alternative* continuation of ``length`` plies from ``anchor_ply``.

    Deterministic (seeded) so regenerating the corpus produces byte-identical
    PDFs, chosen from the real legal-move list so it is always legal, and it
    always avoids replaying the mainline's move -- a "variation" that repeats
    the main line is not a variation.
    """
    board = board_after(pgn, anchor_ply)
    mainline = san_tokens(pgn)
    avoid = mainline[anchor_ply].rstrip("+#") if anchor_ply < len(mainline) else None
    rng = random.Random(f"{pgn[:24]}:{anchor_ply}:{seed}")
    line: list[str] = []
    last_white_number: int | None = None
    for _ in range(length):
        legal = [m for m in board.legal_moves if board.san(m).rstrip("+#") != avoid]
        if not legal:
            legal = list(board.legal_moves)
        if not legal:
            break
        # prefer captures/checks so the lines look like real book variations
        scored = []
        for m in legal:
            score = rng.random() * 1.5
            if board.is_capture(m):
                score += 3.0
            if board.gives_check(m):
                score += 2.0
            if board.is_castling(m):
                score += 1.0
            scored.append((score, board.san(m), m))
        scored.sort(key=lambda t: -t[0])
        san, mv = scored[0][1], scored[0][2]
        n = board.fullmove_number
        if board.turn == chess.WHITE:
            line.append(f"{n}. {san}")
            last_white_number = n
        else:
            if last_white_number == n:
                line.append(san)
            else:
                line.append(f"{n}...{san}")
                last_white_number = None
        board.push(mv)
    return " ".join(line)

def ambiguity_line(max_plies: int = 12) -> str | None:
    """Find a short legal line that ends on a genuinely ambiguous move.

    A breadth-first walk over the real legal-move tree (deduplicated by FEN)
    finds the first move that two identical pieces could equally have made.  The
    returned text spells that one move *without* its disambiguation letter --
    exactly what a sloppy typesetter produces, and exactly what charter #6 says
    we must refuse to guess at.
    """
    seen: set[str] = {chess.STARTING_FEN}
    frontier: list[tuple[chess.Board, list[chess.Move]]] = [(chess.Board(), [])]

    for _ in range(max_plies):
        nxt: list[tuple[chess.Board, list[chess.Move]]] = []
        for board, history in frontier:
            for move in board.legal_moves:
                child = board.copy(stack=False)
                child.push(move)
                line = history + [move]
                if _played_move_was_ambiguous(board, move):
                    return render_line(board, line, ambiguous=move)
                key = child.fen()
                if key not in seen:
                    seen.add(key)
                    nxt.append((child, line))
        frontier = nxt[:4000]
    return None


AMBIGUOUS_PIECES = (chess.KNIGHT, chess.QUEEN, chess.ROOK, chess.BISHOP)


def _played_move_was_ambiguous(board: chess.Board, move: chess.Move) -> bool:
    piece = board.piece_type_at(move.from_square)
    if piece not in AMBIGUOUS_PIECES:
        return False
    rivals = [
        m
        for m in board.legal_moves
        if m != move
        and m.to_square == move.to_square
        and board.piece_type_at(m.from_square) == piece
    ]
    return bool(rivals)


def render_line(
    board: chess.Board, moves: list[chess.Move], ambiguous: chess.Move | None = None
) -> str:
    """Render a move list as book-style text, optionally under-specifying one move."""
    replay = chess.Board()
    out: list[str] = []
    white_number: int | None = None
    for mv in moves:
        number = replay.fullmove_number
        san = replay.san(mv)
        if ambiguous is not None and mv == ambiguous:
            san = strip_disambiguation(san)
        if replay.turn == chess.WHITE:
            out.append(f"{number}. {san}")
            white_number = number
        else:
            if white_number == number:
                out.append(san)
            else:
                out.append(f"{number}...{san}")
            white_number = None
        replay.push(mv)
    return " ".join(out)


def strip_disambiguation(san: str) -> str:
    return re.sub(r"^([KQRBN])[a-h]", r"\1", san)
