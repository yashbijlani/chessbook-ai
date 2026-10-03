"""Chess notation: tokenizing + legality-aware SAN resolution.

Charter #6/#7: regex *proposes*, the board *disposes*.  A token only becomes a
real move when ``python-chess`` agrees it is legal in the current position.

Charter #17: we never silently "fix" chess.  A candidate that only parses after
we ignore the piece letter (e.g. ``Nxf7`` where only a bishop can reach f7) is
**rejected** and reported as ``piece_mismatch`` -- not silently accepted.
"""

from __future__ import annotations

import enum
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

import chess

from ..models import ParsedMove, SourceRef

# ---------------------------------------------------------------------------
# Figurine normalisation (#7)
# ---------------------------------------------------------------------------

FIGURINES = {
    "♔": "K", "♕": "Q", "♖": "R", "♗": "B", "♘": "N", "♙": "P",
    "♚": "K", "♛": "Q", "♜": "R", "♝": "B", "♞": "N", "♟": "P",
    "Ⓚ": "K", "Ⓠ": "Q", "Ⓡ": "R", "ⓑ": "B", "ⓝ": "N", "ⓟ": "P",
    "ⓚ": "K", "ⓠ": "Q", "ⓡ": "R", "ⓑ": "B", "ⓝ": "N", "ⓟ": "P",
}
FIGURE_RE = re.compile("[" + "".join(sorted(FIGURINES, key=len, reverse=True)) + "]")

PIECES = "KQRBNP"
FILE_CHARS = "abcdefgh"

#: Canonical starting position.  ``python-chess`` owns this constant; we re-export
#: it so callers do not need to import ``chess`` just to name the start.
START_FEN_FALLBACK = chess.STARTING_FEN

# ---------------------------------------------------------------------------
# Token grammar
# ---------------------------------------------------------------------------

RESULT_BODY_SRC = (
    r"(?:[0-9]{1,2}[ ]?[-–—][ ]?[0-9]{1,2}"
    r"|1/2[ ]?[-–—][ ]?1/2"
    r"|½[ ]?[-–—][ ]?½"
    r"|\*)"
)
CASTLE_BODY_SRC = (
    r"[0OoQq]{1,2}[ ]?[-–—][ ]?(?:[0OoQq]{1,2}[ ]?[-–—][ ]?)?[0OoQq]{1,2}"
)

# Case sensitive on purpose: `bxc3` (pawn from the b-file) must never be
# confused with `Bxc3` (bishop takes c3), and a promotion letter must never
# swallow the following word.
SAN_BODY_SRC = (
    # the disambiguation letter may be a file *or* a rank ("Nb1" / "N7c6")
    r"(?:[KQRBNP][a-h1-8]?[x×:]?[a-h][1-8](?:[ ]?=[ ]?[QRBNP]|[QRBNP])?[+#]?"
    r"|[a-h][x×:][a-h][1-8](?:[ ]?=[ ]?[QRBNP]|[QRBNP])?[+#]?"
    r"|[x×:][a-h][1-8](?:[ ]?=[ ]?[QRBNP]|[QRBNP])?[+#]?"
    r"|[a-h][1-8](?:[ ]?=[ ]?[QRBNP]|[QRBNP])?[+#]?)"
)
SAN_BODY_FULL = f"(?:{SAN_BODY_SRC}|{CASTLE_BODY_SRC})"

NAG_SRC = r"(?:!!|\?\?|!\?|\?!|!|\?)"
NAG_RE = re.compile(NAG_SRC)

SAN_BODY_RE = re.compile(rf"^{SAN_BODY_FULL}$")
MOVE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:(?P<num>\d{1,3})[ ]*(?P<dots>\.{1,3})[ ]*)?"
    r"(?P<body>"
    + SAN_BODY_SRC
    + "|"
    + CASTLE_BODY_SRC
    + "|"
    + RESULT_BODY_SRC
    + ")"
    r"[ ]*(?P<nag>" + NAG_SRC + r"+)?"
)
RESULT_RE = re.compile(
    r"^(?:\d{1,2}[ ]?[-–—][ ]?\d{1,2}|1/2[ ]?[-–—][ ]?1/2"
    r"|½[ ]?[-–—][ ]?½|\*)$"
)
CASTLE_RE = re.compile(rf"^{CASTLE_BODY_SRC}$")

# Diagnostic parse used for loose comparison (case sensitive).
SAN_PARTS_RE = re.compile(
    r"^(?:"
    # a piece move may be disambiguated by file *or* rank ("Nb1", "N1b1", "N7c6")
    r"(?P<piece>[KQRBNP])(?P<dis>[a-h1-8])?(?P<cap>[x×:])?"
    r"(?P<target>[a-h][1-8])(?:[ ]?=[ ]?|[ ]?)(?P<promo>[QRBNP])?[+#]?"
    r"|(?P<dis2>[a-h])?(?P<cap2>[x×:])"
    r"(?P<target2>[a-h][1-8])(?:[ ]?=[ ]?|[ ]?)(?P<promo2>[QRBNP])?[+#]?"
    r"|(?P<target3>[a-h][1-8])(?:[ ]?=[ ]?|[ ]?)(?P<promo3>[QRBNP])?[+#]?"
    r")$"
)

OPEN_CHARS = "([{"
CLOSE_CHARS = ")]}"


class PieceKind(str, enum.Enum):
    MOVE = "move"
    TEXT = "text"
    RESULT = "result"
    OPEN = "open"
    CLOSE = "close"


@dataclass
class Piece:
    kind: PieceKind
    text: str
    start: int = 0
    end: int = 0
    move: ParsedMove | None = None
    number: int | None = None
    side: Literal["w", "b"] | None = None
    explicit_number: bool = False
    nag: str = ""
    page: int | None = None
    bbox: list[float] | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def normalize_figurines(text: str) -> str:
    if not text:
        return ""
    return "".join(FIGURINES.get(ch, ch) for ch in text)


def has_figurine(text: str) -> bool:
    return bool(FIGURE_RE.search(text or ""))


def looks_like_castling(token: str) -> bool:
    return bool(CASTLE_RE.match(normalize_figurines(token).strip()))


def canonical_castling(token: str) -> str | None:
    t = normalize_figurines(token).strip()
    if not looks_like_castling(t):
        return None
    dashes = t.count("-") + t.count("\u2013") + t.count("\u2014")
    return "O-O-O" if dashes >= 2 else "O-O"


def is_result_token(text: str) -> bool:
    return bool(RESULT_RE.match(text.strip()))


def looks_like_san(text: str) -> bool:
    """Cheap shape test used for layout profiling (no legality involved).

    Annotation glyphs are stripped first: "Nf3?!" is a move with a comment, not
    an unknown word.
    """
    t = NAG_RE.sub("", normalize_figurines(text).strip())
    return bool(SAN_BODY_RE.match(t))


def token_density(text: str) -> tuple[int, int]:
    toks = [t for t in re.split(r"\s+", text) if t]
    if not toks:
        return 0, 0
    moves = sum(1 for t in toks if looks_like_san(t) or CASTLE_RE.match(t))
    return moves, len(toks)


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


def tokenize(text: str, *, page: int | None = None) -> list[Piece]:
    """Split a line/segment into move tokens, prose fragments and braces.

    Bare (unnumbered) moves are emitted too, but always carry lower
    confidence and still have to survive legality validation downstream.
    """
    pieces: list[Piece] = []
    original = text
    src = normalize_figurines(text)
    n = len(src)
    pos = 0
    pending = 0

    def flush(upto: int) -> None:
        nonlocal pending
        chunk = src[pending:upto]
        if chunk.strip():
            pieces.append(Piece(kind=PieceKind.TEXT, text=chunk, start=pending, end=upto))
        pending = upto

    while pos < n:
        ch = src[pos]

        if ch in OPEN_CHARS:
            flush(pos)
            pieces.append(Piece(kind=PieceKind.OPEN, text=ch, start=pos, end=pos + 1, page=page))
            pos += 1
            pending = pos
            continue
        if ch in CLOSE_CHARS:
            flush(pos)
            pieces.append(Piece(kind=PieceKind.CLOSE, text=ch, start=pos, end=pos + 1, page=page))
            pos += 1
            pending = pos
            continue

        m = _match_at(src, pos)
        if m is None:
            pos += 1
            continue

        body = m.group("body")
        if body in OPEN_CHARS or body in CLOSE_CHARS:
            pos = m.end("body")
            continue
        if not (looks_like_san(body) or is_result_token(body)):
            pos = m.end("body")
            continue

        num = m.group("num")
        dots = m.group("dots") or ""
        number = int(num) if num else None
        side: Literal["w", "b"] | None = None
        if num:
            ndots = dots.count(".")
            if ndots >= 3:
                side = "b"
            elif ndots >= 1:
                side = "w"
            # "12 Nf3" (author omitted the dot) -> side decided downstream

        prefix = src[m.start() : m.start("body")]
        if (
            pieces
            and pieces[-1].kind == PieceKind.TEXT
            and num
            and re.fullmatch(r"[ ]*\d{1,3}[ ]*\.{0,3}[ ]*", prefix)
            and pieces[-1].text.rstrip().endswith(prefix.strip())
        ):
            trimmed = pieces[-1].text.rstrip()[: -len(prefix.strip())]
            if not trimmed.strip():
                pieces.pop()
            else:
                pieces[-1].text = trimmed
                pieces[-1].end = m.start("body")
        flush(m.start("body"))
        if is_result_token(body) and not looks_like_san(body):
            pieces.append(
                Piece(
                    kind=PieceKind.RESULT,
                    text=body.strip(),
                    start=m.start(),
                    end=m.end(),
                    page=page,
                )
            )
        else:
            nag = (m.group("nag") or "").strip()
            parsed = ParsedMove(
                raw=src[m.start() : m.end("body")].strip(),
                body=body.strip(),
                san=None,
                move_number=number,
                side=side,
                # check the *original* text: figurines are already folded into
                # ``src`` by the time we get here
                is_figurine=has_figurine(original[m.start("body") : m.end("body")]),
                is_black_move=(side == "b"),
                annotation=nag,
                source=SourceRef(page=page) if page else None,
                confidence=0.9 if number else 0.45,
            )
            pieces.append(
                Piece(
                    kind=PieceKind.MOVE,
                    text=parsed.raw,
                    start=m.start(),
                    end=m.end(),
                    move=parsed,
                    number=number,
                    side=side,
                    explicit_number=number is not None,
                    nag=nag,
                    page=page,
                )
            )
        pos = m.end()
        pending = pos

    flush(n)
    return pieces


def _match_at(src: str, pos: int) -> re.Match[str] | None:
    i = pos
    n = len(src)
    while i < n and src[i] in " \t":
        i += 1
    if i >= n:
        return None
    if i > 0 and src[i - 1].isalnum():
        return None
    m = MOVE_TOKEN_RE.match(src, i)
    if m and m.end("body") > m.start("body"):
        return m
    return None


# ---------------------------------------------------------------------------
# Legality-aware resolution
# ---------------------------------------------------------------------------

#: Character confusions we are willing to *try*.  A substitution is only ever
#: accepted when it yields exactly one legal move, and it is always recorded.
OCR_CONFUSIONS: dict[str, tuple[str, ...]] = {
    "0": ("O", "D"),
    "O": ("0", "D"),
    "D": ("Q", "0"),
    "Q": ("O", "D"),
    "l": ("1",),
    "I": ("1",),
    "1": ("l",),
    "5": ("S",),
    "S": ("5",),
    "6": ("G",),
    "G": ("6",),
    "B": ("8",),
    "8": ("B",),
    "Z": ("2",),
    "2": ("Z",),
    "g": ("6",),
    "b": ("6",),
}

#: Substitutions we will *not* attempt because they would silently change the
#: meaning of the move (charter #17).
FORBIDDEN_SUBSTITUTIONS = {("N", "B"), ("B", "N"), ("Q", "R"), ("R", "Q"), ("K", "Q"), ("Q", "K")}


@dataclass
class Resolution:
    move: chess.Move | None
    status: Literal["ok", "repaired", "ambiguous", "illegal"]
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    candidates: list[chess.Move] = field(default_factory=list)
    confidence: float = 0.0


def turn_of(side: str) -> bool:
    """'w'/'b' -> chess.WHITE/chess.BLACK (python-chess uses bitboard flags)."""
    return chess.WHITE if side == "w" else chess.BLACK


def side_of(turn: bool) -> str:
    return "w" if turn == chess.WHITE else "b"


def set_side(board: chess.Board, side: str) -> chess.Board:
    board.turn = turn_of(side)
    return board


_LEADING_NUMBER = re.compile(r"^\s*\d{1,3}\s*\.{1,3}\s*")


def strip_noise(san: str) -> str:
    t = normalize_figurines(san)
    t = _LEADING_NUMBER.sub("", t, count=1)
    t = NAG_RE.sub("", t)
    t = re.sub(r"(?i)\s*e\.?\s*p\.?\s*$", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t.strip(".,;:")


@dataclass(frozen=True)
class SanKey:
    """A faithful, comparable description of one SAN move.

    ``origin`` is the disambiguation letter the author wrote -- a file (``b``)
    or a rank (``7``) -- and is checked against the real from-square rather than
    against python-chess's own spelling, because python-chess omits it whenever
    the destination already disambiguates.
    """

    piece: str
    origin: str | None
    capture: bool | None
    target: str
    promo: str

    def compatible_with(self, other: SanKey) -> bool:
        """May a legal move described by ``other`` be printed as ``self``?

        We allow the author to *omit* an optional part (disambiguation letter,
        capture glyph).  We never allow a *contradiction*: a written ``cxd5``
        can never be a move from the d-file, and a written ``xd5`` can never be
        a quiet move.  This is charter #17 in code form.
        """
        if self.piece != other.piece or self.target != other.target or self.promo != other.promo:
            return False
        if self.capture and not other.capture:
            return False
        return True

    def origin_matches(self, move: chess.Move) -> bool:
        if self.origin is None:
            return True
        if self.origin.isalpha():
            return chess.square_name(move.from_square)[0] == self.origin
        return chess.square_name(move.from_square)[1] == self.origin


def san_parts(token: str) -> SanKey | None:
    """Decompose a SAN-ish token; case sensitive, figurines already mapped."""
    t = strip_noise(token).replace("\u00d7", "x").replace(":", "x")
    m = SAN_PARTS_RE.match(t)
    if not m:
        return None
    g = m.groupdict()
    cap = g["cap"] or g["cap2"]
    return SanKey(
        piece=(g["piece"] or "P").upper(),
        origin=(g["dis"] or g["dis2"]),
        capture=bool(cap) if cap else None,
        target=(g["target"] or g["target2"] or g["target3"] or "").lower(),
        promo=(g["promo"] or g["promo2"] or g["promo3"] or "").upper(),
    )


def san_key_of(board: chess.Board, move: chess.Move) -> SanKey | None:
    """Ground truth for a *legal* move (castling has no SAN-parts decomposition).

    The origin file always comes from the from-square, never from the printed
    SAN: that is what lets us tell "Ncxd4" (the c-file knight) apart from the
    same letters used to mean something else.
    """
    k = san_parts(board.san(move))
    if k is None:
        return None
    # origin is checked against the from-square, never taken from the spelling
    return SanKey(k.piece, None, board.is_capture(move), k.target, k.promo)


def san_candidates(board: chess.Board, san: str) -> Resolution:
    """Resolve ``san`` against ``board`` using real chess rules."""
    token = strip_noise(san)
    if not token:
        return Resolution(None, "illegal", reason="empty_token")

    castle = canonical_castling(token)
    parse_target = castle or token

    try:
        mv = board.parse_san(parse_target)
        if mv == chess.Move.null():
            # "--", "-", "..." etc. parse as the null move but are not moves.
            return Resolution(None, "illegal", reason="empty_token")
        status: Literal["ok", "repaired"] = "ok"
        reason: str | None = None
        if castle and castle != token:
            status, reason = "repaired", "castling_normalised"
        return Resolution(mv, status, reason=reason, candidates=[mv], confidence=1.0,
                          detail={"as_written": token, "canonical": board.san(mv)} if reason else {})
    except ValueError:
        pass

    legal = list(board.legal_moves)
    key = san_parts(token)

    if key is not None:
        # Everything the written spelling does not *contradict* is a candidate.
        cands = [
            m
            for m in legal
            if (k := _key_of(board, m)) is not None and key.compatible_with(k)
        ]

        # A disambiguation letter is decisive even when python-chess would not
        # have printed one ("Ncxd4" where both knights can also take d4).
        if key.origin is not None:
            narrowed = [m for m in cands if key.origin_matches(m)]
            if len(narrowed) == 1:
                mv = narrowed[0]
                return Resolution(
                    mv,
                    "repaired",
                    reason="san_relaxed_spelling",
                    detail={"as_written": token, "canonical": board.san(mv)},
                    candidates=narrowed,
                    confidence=0.92,
                )
            if len(narrowed) > 1:
                return Resolution(
                    None,
                    "ambiguous",
                    reason="disambiguation_file_insufficient",
                    detail={
                        "as_written": token,
                        "disambiguation": key.origin,
                        "options": sorted(
                            f"{chess.square_name(m.from_square)}->{board.san(m)}" for m in narrowed
                        )[:8],
                    },
                    candidates=narrowed,
                    confidence=0.45,
                )
            cands = []

        if len(cands) == 1:
            mv = cands[0]
            return Resolution(
                mv,
                "repaired",
                reason="san_normalised",
                detail={"as_written": token, "canonical": board.san(mv)},
                candidates=cands,
                confidence=0.9,
            )
        if len(cands) > 1:
            return Resolution(
                None,
                "ambiguous",
                reason="under_specified_move",
                detail={"as_written": token, "options": sorted(board.san(m) for m in cands)},
                candidates=cands,
                confidence=0.45,
            )

        # Nothing the written spelling allows is legal: report what *is*
        # available on that square rather than inventing a move.
        reach = sorted(
            san
            for san in (board.san(m) for m in legal)
            if (parts := san_parts(san)) is not None and parts.target == key.target
        )
        if reach:
            return Resolution(
                None,
                "illegal",
                reason="piece_mismatch",
                detail={
                    "as_written": token,
                    "stated_piece": key.piece,
                    "legal_moves_to_square": reach[:12],
                },
                candidates=[],
                confidence=0.0,
            )

    alt = _ocr_repair(board, token)
    if alt is not None:
        mv, applied = alt
        return Resolution(
            mv,
            "repaired",
            reason="ocr_confusion",
            detail={
                "as_written": token,
                "applied_substitutions": applied,
                "canonical": board.san(mv),
            },
            candidates=[mv],
            confidence=0.7,
        )

    return Resolution(
        None,
        "illegal",
        reason="no_legal_move_matches",
        detail={
            "as_written": token,
            "legal_san_sample": sorted(board.san(m) for m in legal)[:24],
        },
    )


def _ocr_repair(board: chess.Board, token: str) -> tuple[chess.Move, list[str]] | None:
    """Try single-character OCR confusions; accept only when unambiguous."""
    if len(token) < 2 or len(token) > 8:
        return None
    key = san_parts(token)
    for i, ch in enumerate(token):
        if not ch.isalnum():
            continue
        for dst in OCR_CONFUSIONS.get(ch, ()):  # type: ignore[arg-type]
            if (ch.upper(), dst.upper()) in FORBIDDEN_SUBSTITUTIONS:
                continue
            candidate_text = token[:i] + dst + token[i + 1 :]
            cands = _exact_matches(board, candidate_text)
            if len(cands) == 1:
                return cands[0], [f"{i}:{ch}->{dst}"]
    return None


def _key_of(board: chess.Board, move: chess.Move) -> SanKey | None:
    return san_key_of(board, move)


def _exact_matches(board: chess.Board, token: str) -> list[chess.Move]:
    castle = canonical_castling(token)
    if castle:
        try:
            return [board.parse_san(castle)]
        except ValueError:
            return []
    key = san_parts(token)
    if key is None:
        return []
    return [m for m in board.legal_moves if _key_of(board, m) == key]


def uci_of(mv: chess.Move) -> str:
    return mv.uci()


def normalize_name(name: str) -> str:
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()