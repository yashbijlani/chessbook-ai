"""Stage: game detection (charter #11).

Many independent heuristics vote.  A region becomes a game when the votes are
strong enough *and* the region actually contains chess notation -- we never
promote a bare paragraph to a game, and we never invent metadata (missing
fields stay ``None``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import SourceRef
from . import notation as nz
from .layout import Paragraph

# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------

PGN_TAG_RE = re.compile(r'\[\s*(\w+)\s+"([^"]*)"\s*\]')
PGN_LINE_RE = re.compile(r'^\s*\[\s*(\w+)\s+"([^"]*)"\s*\]\s*$')
FIELD_RE = re.compile(
    r"^\s*(White|Black|Event|Site|Date|Round|Result|ECO|Opening|Variation|WhiteElo|BlackElo|FEN)\s*[:=]\s*(.+?)\s*$",
    re.I,
)
SEP_RE = re.compile(r"\s+(?:-|–|—|vs\.?|v\.|/)\s+")
YEARISH = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
NAME_OK = re.compile(r"[A-Za-z\u00C0-\u024F]")
NUMBERED_PLAYERS_VS_RE = re.compile(
    r"^\s*(?:Game\s+)?(?P<white>\d{1,2}\.\s*[\w.'\- ]{2,40}?)\s*(?:-|–|—|vs\.?|v\.)\s*"
    r"(?P<black>[\w.'\- ]{2,40}?)\s*$"
)
GAME_LABEL_RE = re.compile(r"^\s*(?:game|match|no\.?)\s*(\d{1,3})\s*[.:–-]?\s*(.*)$", re.I)
RESULT_RE = re.compile(r"(?<![0-9A-Za-z])(1\s*[–\-—]\s*0|0\s*[–\-—]\s*1|½\s*[–\-—]\s*½|1/2\s*[–\-—]\s*1/2|\*)(?![0-9A-Za-z])")
ROUND_RE = re.compile(r"\b(?:round|match)\s+(\d{1,3})\b", re.I)
ECO_RE = re.compile(r"\b([A-E]\d{2})\b")
YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
MOVENUM_RE = nz.MOVE_TOKEN_RE


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------


@dataclass
class GameRegion:
    index: int
    start: int
    end: int  # exclusive
    title: str = ""
    heuristic: str = ""
    confidence: float = 0.0
    headers: dict[str, str] = field(default_factory=dict)
    players: dict[str, str | None] = field(default_factory=lambda: {"white": None, "black": None})
    event: str | None = None
    date: str | None = None
    site: str | None = None
    round: str | None = None
    result: str | None = None
    eco: str | None = None
    opening: str | None = None
    start_page: int | None = None
    end_page: int | None = None
    source: SourceRef | None = None
    move_paragraphs: int = 0
    first_move_number: int | None = None


@dataclass
class DetectionReport:
    regions: list[GameRegion]
    scores: list[tuple[int, float, str]] = field(default_factory=list)


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


def detect_games(paragraphs: list[Paragraph]) -> DetectionReport:
    scores: list[tuple[int, float, str]] = []
    # Pass 0: move-number resets.  A move block that starts again at 1 (or 2)
    # after the document has already reached move 8+ is almost certainly the
    # beginning of a new game, whether or not it carries a heading.  This is
    # the single most reliable structural signal in chess books.
    reset_at = _move_number_resets(paragraphs)
    for i, para in enumerate(paragraphs):
        score, why = _start_score(paragraphs, i)
        if i in reset_at and (_move_density(para) >= 0.5 or para.kind == "move_run"):
            score += 0.55
            why = (why + "+" if why else "") + "move_number_reset"
        elif _fragment_start(paragraphs, i) and _starts_on_black_move(para):
            # A game never begins on Black's move.  Seeing one means we are
            # looking at an excerpt or the tail of the previous game -- but only
            # when there is a visible break; an indented variation line
            # directly under the main line is still part of the same game.
            score += 0.5
            why = (why + "+" if why else "") + "starts_on_black"
        # Below this a paragraph is not a game boundary, however much notation
        # happens to be nearby: prose that merely mentions moves is not a game.
        if score >= 0.5:
            scores.append((i, score, why))

    # A game starts where its *move list* starts.  When the paragraph before the
    # move list is prose that merely quotes a move (very common, and typical of
    # OCR output), the move list is the better anchor.
    for i, para in enumerate(paragraphs):
        if _starts_a_move_list(para) and not (i in reset_at):
            scores.append((i, 0.62, "move_list_start"))

    if not scores:
        regions = _implicit_regions(paragraphs)
        return DetectionReport(regions=regions, scores=[])

    scores.sort(key=lambda item: item[0])
    starts = _cluster_starts(scores, paragraphs)

    regions: list[GameRegion] = []
    for n, (idx, score, why) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(paragraphs)
        if why == "move_list_start":
            idx = _include_title_paragraphs(paragraphs, idx, n, starts)
        region = _build_region(paragraphs, idx, end, len(regions), score, why)
        regions.append(region)

    regions = _drop_move_less(regions, paragraphs)
    for n, region in enumerate(regions):
        region.index = n
    return DetectionReport(regions=regions, scores=scores)


def _start_score(paragraphs: list[Paragraph], i: int) -> tuple[float, str]:
    para = paragraphs[i]
    text = para.text.strip()
    if not text:
        return 0.0, ""
    score = 0.0
    if _looks_like_label(text):
        text = _strip_label_prefix(text)
    if para.kind == "heading":
        # a bold "Game 12: A - B" line is a perfectly good game title
        score -= 0.1
        game_word = bool(GAME_WORD_RE.search(text)) and len(text) <= 90
        if game_word:
            score += 0.4
        if not game_word and not (
            parse_players(text)[:2] != (None, None)
            or GAME_LABEL_RE.match(text) or FIELD_RE.match(text)
        ):
            score -= 0.45
    why: list[str] = []

    # (a) PGN tag block
    tags = _collect_pgn_tags(paragraphs, i)
    if tags:
        return 0.95, "pgn_headers:" + ",".join(sorted(tags)[:4])

    m = FIELD_RE.match(text)
    if m and m.group(1).lower() in ("white", "black", "event", "date", "round", "result"):
        score += 0.6
        why.append("label:" + m.group(1).lower())

    white_n, black_n, leftover = parse_players(text)
    if white_n and black_n:
        score += 0.7
        why.append("players_vs")

    m = GAME_LABEL_RE.match(text)
    if m:
        score += 0.42
        why.append("game_label")

    if ECO_RE.search(text):
        score += 0.1
        why.append("eco")

    # (b) a paragraph that *is* nothing but move notation is never a game start
    if re.match(r"^\s*\d{1,3}\s*\.{1,3}", text) and _move_density(para) > 0.55:
        return 0.0, ""

    # (c) is notation nearby?  a title only counts if moves follow.
    lookahead = paragraphs[i : i + 12]
    move_paras = [p for p in lookahead if _move_density(p) > 0.25 or p.kind == "move_run"]
    if move_paras:
        score += 0.3
        why.append("moves_follow")
    else:
        score -= 0.35

    if len(text) > 160:
        score -= 0.25

    return max(0.0, score), "+".join(why)


BLACK_FIRST_RE = re.compile(r"^\s*\d{1,3}\s*\.{3,}")
GAME_WORD_RE = re.compile(r"\b(game|match|partita|partie|party)\b", re.I)


def _starts_on_black_move(para: Paragraph) -> bool:
    return bool(BLACK_FIRST_RE.match(para.text.strip()))


def _fragment_start(paragraphs: list[Paragraph], i: int) -> bool:
    """Is there a visible break between the previous notation and ``i``?"""
    for k in range(i - 1, max(-1, i - 4), -1):
        para = paragraphs[k]
        if not para.text.strip():
            continue
        if RESULT_RE.search(para.text):
            return True
        is_notation = _move_density(para) > 0.2 or para.kind == "move_run"
        if is_notation:
            return False
        return True
    return False


def _move_number_resets(paragraphs: list[Paragraph]) -> dict[int, int]:
    """Map paragraph index -> highest move number seen before it."""
    out: dict[int, int] = {}
    running = 0
    for i, para in enumerate(paragraphs):
        if not (_move_density(para) > 0.2 or para.kind == "move_run"):
            continue
        first = _first_move_number(para)
        if first is not None and first <= 2 and running >= 8:
            out[i] = running
        if first is not None:
            running = max(running, _last_move_number(para) or first)
    return out


def _first_move_number(para: Paragraph) -> int | None:
    m = re.search(r"\b(\d{1,3})\s*\.{1,3}", para.text)
    return int(m.group(1)) if m else None


def _last_move_number(para: Paragraph) -> int | None:
    nums = [int(m) for m in re.findall(r"\b(\d{1,3})\s*\.{1,3}", para.text)]
    return max(nums) if nums else None


def _starts_a_move_list(para: Paragraph) -> bool:
    text = para.text.strip()
    if not re.match(r"^\s*\d{1,3}\s*\.{1,3}", text):
        return False
    if _move_density(para) < 0.55:
        return False
    first = _first_move_number(para)
    return first is not None and first <= 2


def _looks_like_label(text: str) -> bool:
    return bool(GAME_LABEL_RE.match(text) or re.match(r"^\s*(?:part|chapter|game)\b", text, re.I))


def _strip_label_prefix(text: str) -> str:
    return re.sub(r"^\s*(?:game|match|no\.?|part|chapter)\s*\d{0,3}\s*[.:\u2013\-]?\s*", "", text,
                  flags=re.I)


def _plausible_names(a: str, b: str) -> bool:
    for name in (a, b):
        n = name.strip()
        if not (2 <= len(n) <= 40):
            return False
        if any(ch.isdigit() for ch in n):
            return False
        if not NAME_OK.search(n):
            return False
    return True


def parse_players(text: str) -> tuple[str | None, str | None, str | None]:
    """Split "Morphy - Duke of Brunswick, Paris 1858" into its three parts.

    Returns ``(white, black, leftover)``.  Player names may legitimately contain
    a comma ("Kasparov, Garry"), so a comma only ends a name when the tail
    carries a year -- i.e. when it is really an event or venue.
    """
    t = _strip_label_prefix(text.strip())
    t = re.sub(r"\s*[([\d]\s*(?:1-0|0-1|1/2-1/2|½-½|\*)\)?\s*$", "", t).strip()
    parts = SEP_RE.split(t, maxsplit=1)
    if len(parts) != 2:
        return None, None, None
    left, right = parts[0], parts[1]

    def split_name(chunk: str) -> tuple[str, str]:
        chunk = re.sub(r"^\s*\d{1,2}\s*[.)]\s*", "", chunk).strip(" .,:;-")
        pieces = [p.strip() for p in chunk.split(",") if p.strip()]
        if len(pieces) > 1 and any(YEARISH.search(p) for p in pieces[1:]):
            name = pieces[0]
            rest = ", ".join(pieces[1:])
            if all(YEARISH.search(p) or len(p.split()) <= 3 for p in pieces[1:]):
                return name, rest
        return chunk, ""

    white, ev1 = split_name(left)
    black, ev2 = split_name(right)
    if not _plausible_names(white, black):
        return None, None, None
    leftover = ", ".join(x for x in (ev1, ev2) if x) or None
    return white, black, leftover


def _collect_pgn_tags(paragraphs: list[Paragraph], i: int) -> dict[str, str]:
    """Read a PGN tag block, whether the tags survived as separate paragraphs
    or were merged into one by paragraph assembly."""
    tags: dict[str, str] = {}
    for j in range(i, min(len(paragraphs), i + 6)):
        text = paragraphs[j].text
        found = list(PGN_TAG_RE.finditer(text))
        if not found:
            if tags:
                break
            continue
        if not PGN_LINE_RE.match(text.strip()) and not found:
            break
        for m in found:
            tags.setdefault(m.group(1), m.group(2))
        if len(tags) >= 2 and not PGN_LINE_RE.match(text.strip()):
            break
    return tags if len(tags) >= 2 else {}


def _move_density(para: Paragraph) -> float:
    text = nz.normalize_figurines(para.text)
    moves, total = nz.token_density(text)
    if not total:
        return 0.0
    numbered = len(MOVENUM_RE.findall(text))
    if numbered and moves / total >= 0.3:
        return moves / total
    return moves / total


def _implicit_regions(paragraphs: list[Paragraph]) -> list[GameRegion]:
    """No explicit game markers: one region per contiguous run of notation."""
    regions: list[GameRegion] = []
    start: int | None = None
    for i, para in enumerate(paragraphs):
        has_moves = _move_density(para) > 0.25 or para.kind == "move_run"
        if has_moves and start is None:
            start = i
        elif not has_moves and start is not None:
            if i - start >= 2:
                regions.append(
                    _build_region(paragraphs, start, i, len(regions), 0.5, "implicit_moves")
                )
            start = None
    if start is not None and len(paragraphs) - start >= 2:
        regions.append(
            _build_region(paragraphs, start, len(paragraphs), len(regions), 0.5, "implicit_moves")
        )
    return regions


#: Paragraphs within this distance are treated as one header cluster (e.g.
#: "Game 5" immediately followed by "White: ..." / "Black: ...").
HEADER_CLUSTER = 3


def _cluster_starts(
    scores: list[tuple[int, float, str]], paragraphs: list[Paragraph]
) -> list[tuple[int, float, str]]:
    """Collapse competing starts that belong to the same header block.

    A game header is routinely three lines ("Game 5", "White: A", "Black: B")
    and each line scores on its own.  Within one cluster we keep the single
    strongest signal, preferring a *heading* because it reads as the title.
    """
    if not scores:
        return []
    clusters: list[list[tuple[int, float, str]]] = [[scores[0]]]
    for item in scores[1:]:
        anchor = clusters[-1][0][0]
        if item[0] - anchor <= HEADER_CLUSTER and _is_header_block(paragraphs, anchor, item[0]):
            clusters[-1].append(item)
        else:
            clusters.append([item])

    out: list[tuple[int, float, str]] = []
    for cluster in clusters:
        def rank(item: tuple[int, float, str]) -> tuple[float, float, int]:
            idx, score, _ = item
            is_heading = 1.0 if paragraphs[idx].kind == "heading" else 0.0
            # a start that carries players/metadata beats a bare move block
            rich = 1.0 if ("players_vs" in item[2] or "pgn_headers" in item[2]
                           or "label:" in item[2]) else 0.0
            return (rich, is_heading, score)

        out.append(max(cluster, key=rank))
    return out


def _include_title_paragraphs(
    paragraphs: list[Paragraph], idx: int, ordinal: int, starts: list[tuple[int, float, str]]
) -> int:
    """Walk back over a heading / short intro so the game keeps its title."""
    floor = starts[ordinal - 1][0] + 1 if ordinal > 0 else 0
    j = idx - 1
    steps = 0
    while j >= floor and steps < 2:
        para = paragraphs[j]
        if para.kind == "move_run" or _move_density(para) > 0.5:
            break
        if para.kind == "heading":
            return j
        if len(para.text) > 200:
            break
        return j
    return idx


def _is_header_block(paragraphs: list[Paragraph], a: int, b: int) -> bool:
    """True when everything between ``a`` and ``b`` is short header-ish text.

    This is what stops two *consecutive* games from being merged into one
    header cluster: a move list or a paragraph of prose in between means the
    first game has already started.
    """
    if b - a > HEADER_CLUSTER:
        return False
    for k in range(a + 1, b):
        para = paragraphs[k]
        if para.kind == "heading":
            continue
        if len(para.text) > 100 or _move_density(para) > 0.2:
            return False
    return True


def _build_region(
    paragraphs: list[Paragraph],
    start: int,
    end: int,
    index: int,
    score: float,
    why: str,
) -> GameRegion:
    header_texts = [paragraphs[k].text for k in range(start, min(end, start + 6))]
    tags = _collect_pgn_tags(paragraphs, start)
    fields = dict(tags)
    for text in header_texts[:4]:
        m = FIELD_RE.match(text.strip())
        if m:
            fields.setdefault(m.group(1).lower(), m.group(2).strip())

    white = fields.get("white")
    black = fields.get("black")
    leftover: str | None = None
    title = _build_title(paragraphs, start, end, white, black)

    if not white or not black:
        for text in header_texts[:3]:
            w2, b2, extra = parse_players(text)
            if w2 and b2:
                white = white or _clean_player(w2)
                black = black or _clean_player(b2)
                if leftover is None and extra:
                    leftover = extra
                break

    head_blob = " ".join(header_texts)
    eco = ECO_RE.search(head_blob)
    opening = fields.get("opening") or fields.get("variation")
    year = YEAR_RE.search(head_blob)
    event = fields.get("event")
    if event is None:
        event = leftover or None
    if event is None and year:
        event = head_blob.strip() or None

    result = fields.get("result")
    if result is None:
        rm = None
        for k in range(end - 1, max(start, end - 14), -1):
            rm = RESULT_RE.search(paragraphs[k].text)
            if rm:
                break
        result = rm.group(1).replace(" ", "") if rm else None

    round_ = fields.get("round")
    if round_ is None:
        m = ROUND_RE.search(head_blob)
        round_ = m.group(1) if m else None

    move_paras = [
        k for k in range(start, end) if _move_density(paragraphs[k]) > 0.2
    ]
    first_num = None
    for k in move_paras[:3]:
        m = re.search(r"\b(\d{1,3})\s*\.{1,3}", paragraphs[k].text)
        if m:
            first_num = int(m.group(1))
            break

    return GameRegion(
        index=index,
        start=start,
        end=end,
        title=title,
        heuristic=why,
        confidence=round(min(1.0, score), 3),
        headers=fields,
        players={"white": white, "black": black},
        event=event,
        date=fields.get("date") or (year.group(1) if year else None),
        site=fields.get("site"),
        round=round_,
        result=_norm_result(result),
        eco=eco.group(1) if eco else None,
        opening=opening,
        start_page=paragraphs[start].page,
        end_page=paragraphs[min(end, len(paragraphs)) - 1].page if end > start else paragraphs[start].page,
        source=paragraphs[start].source,
        move_paragraphs=len(move_paras),
        first_move_number=first_num,
    )


def _build_title(
    paragraphs: list[Paragraph], start: int, end: int, white: str | None, black: str | None
) -> str:
    head = paragraphs[start].text.strip()
    if PGN_TAG_RE.match(head):
        tags = _collect_pgn_tags(paragraphs, start)
        parts = [tags.get("white", ""), tags.get("black", "")]
        joined = " - ".join(p for p in parts if p)
        ev = tags.get("event")
        if ev and ev.lower() not in ("?", ""):
            joined = f"{joined} ({ev})" if joined else ev
        return joined or head
    if white and black:
        m = GAME_LABEL_RE.match(head)
        label = f"Game {m.group(1)}: " if m else ""
        return f"{label}{white} - {black}"
    if len(head) > 4 and not head.endswith("."):
        return head
    if white:
        return white
    return head[:120]


def _clean_player(name: str) -> str:
    n = re.sub(r"^\s*\d{1,2}\s*[.)]\s*", "", name.strip())
    n = re.sub(r"\s+", " ", n)
    return n.title() if n.islower() else n


def _norm_result(result: str | None) -> str | None:
    if not result:
        return None
    r = re.sub(r"\s+", "", result).replace("–", "-").replace("—", "-")
    if r in ("1-0", "0-1", "½-½", "1/2-1/2", "*"):
        return "1/2-1/2" if r in ("½-½", "1/2-1/2") else r
    return None


def _drop_move_less(regions: list[GameRegion], paragraphs: list[Paragraph]) -> list[GameRegion]:
    """Chapters, prose and OCR noise produce false positives; drop them.

    A region only becomes a game if it contains at least one genuine *move run*
    (notation dominates the line).  Prose that merely quotes two moves -- which
    is extremely common, and is exactly what OCR turns a paragraph into -- is
    searchable text, not a game.
    """
    out: list[GameRegion] = []
    for region in regions:
        has_run = False
        for k in range(region.start, min(region.end, len(paragraphs))):
            para = paragraphs[k]
            if para.kind == "move_run" or _move_density(para) >= 0.55:
                has_run = True
                break
            moves, total = nz.token_density(para.text)
            if total >= 6 and moves / total >= 0.5:
                has_run = True
                break
        if not has_run:
            continue
        out.append(region)
    return out