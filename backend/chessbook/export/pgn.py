"""PGN export that preserves variations and comments (charter #27)."""

from __future__ import annotations

from typing import Any

from ..models import Game


def game_to_pgn(game: dict[str, Any], tree: dict[str, Any], *, include_headers: bool = True) -> str:
    nodes: dict[str, dict] = {n["id"]: n for n in tree["nodes"]}
    comments: dict[str, list[dict]] = tree.get("comments", {})
    root = next((n for n in tree["nodes"] if n["parent_id"] is None), None)
    if root is None:
        return ""
    lines: list[str] = []
    if include_headers:
        for tag, value in _headers(game).items():
            lines.append(f'[{tag} "{_escape(value)}"]')
        if lines:
            lines.append("")

    body = _render(root, nodes, comments, depth=0)
    lines.extend(body)
    return "\n".join(lines).rstrip() + "\n"


def _headers(game: dict[str, Any]) -> dict[str, str]:
    players = game.get("players") or {}
    out: dict[str, str] = {}
    if game.get("event"):
        out["Event"] = game["event"]
    if game.get("site"):
        out["Site"] = game["site"]
    if game.get("date"):
        out["Date"] = game["date"]
    out["Round"] = game.get("round") or "?"
    if players.get("white"):
        out["White"] = players["white"]
    if players.get("black"):
        out["Black"] = players["black"]
    out["Result"] = game.get("result") or "*"
    if game.get("eco"):
        out["ECO"] = game["eco"]
    if game.get("opening"):
        out["Opening"] = game["opening"]
    return out


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _render(
    node: dict,
    nodes: dict[str, dict],
    comments: dict[str, list[dict]],
    depth: int,
) -> list[str]:
    """Render the main line that follows ``node`` (which is the parent position).

    Side lines are emitted after the move they replace, wrapped in parentheses,
    so a reader can round-trip our own export without flattening anything.
    """
    lines: list[str] = []
    tokens: list[str] = []
    width = 0
    indent = "  " * depth

    def flush() -> None:
        nonlocal width, tokens
        if tokens:
            lines.append(indent + " ".join(tokens))
            tokens = []
            width = 0

    def emit(piece: str, hard: bool = False) -> None:
        nonlocal width
        if hard and tokens:
            flush()
        if not hard and width + len(piece) + 1 > 74 and tokens:
            flush()
        tokens.append(piece)
        width += len(piece) + 1

    cur = node
    seen = {cur.get("id")}
    while True:
        next_id = cur.get("mainline_child_id")
        if not next_id or next_id in seen:
            break
        child = nodes.get(next_id)
        if child is None:
            break
        seen.add(next_id)

        emit(_move_token(child, not tokens))
        for comment in comments.get(child["id"], []):
            emit("{" + _escape(comment["text"]) + "}")

        # Side lines hang off this move, so they are written straight after it.
        for sid in (child.get("child_ids") or []):
            if sid == child.get("mainline_child_id"):
                continue
            text = _side_line(sid, nodes, comments, depth + 1)
            if text:
                emit(text, hard=True)
                flush()
        cur = child
    flush()
    return lines


def _side_line(
    first_id: str,
    nodes: dict[str, dict],
    comments: dict[str, list[dict]],
    depth: int,
    max_nodes: int = 64,
) -> str:
    """One parenthesised side line, following its own main line."""
    body: list[str] = []
    cur: str | undefined = first_id
    seen: set[str] = set()
    nested: list[str] = []
    while cur and cur not in seen and len(body) < max_nodes:
        seen.add(cur)
        node = nodes.get(cur)
        if node is None:
            break
        body.append(_move_token(node, not body))
        for comment in comments.get(cur, []):
            body.append("{" + _escape(comment["text"]) + "}")
        for sid in node.get("child_ids") or []:
            if sid != node.get("mainline_child_id"):
                sub = _side_line(sid, nodes, comments, depth + 1)
                if sub:
                    nested.append(sub)
        cur = node.get("mainline_child_id") or None
    if not body:
        return ""
    return "(" + " ".join(body) + " " + " ".join(nested) + ")"


def _move_token(node: dict, starts_line: bool) -> str:
    """Format one move.  Black needs its number only when it opens a line."""
    san = node.get("san") or ""
    side = node.get("side")
    number = node.get("move_number")
    nag_text = "".join(_nag_to_symbol(n) for n in (node.get("nags") or []))
    if side == "w" or number is None:
        return f"{number}. {san}{nag_text}"
    if starts_line:
        return f"{number}...{san}{nag_text}"
    return f"{san}{nag_text}"


def _nag_to_symbol(nag: str) -> str:
    return {"$1": "!", "$2": "!!", "$3": "??", "$4": "?!", "$5": "!?", "$6": "?!"}.get(nag, "")


def book_to_pgn(games: list[tuple[dict, dict]]) -> str:
    chunks = [game_to_pgn(game, tree) for game, tree in games]
    return "\n".join(c for c in chunks if c.strip())