"""Persist a BuildOutput into SQLite + the filesystem.

Layout on disk (charter #35)::

    data/books/<book_id>/
        original.pdf
        metadata.json
        extracted/{pages.json,layout.json,games.json,tree.json}
        diagrams/<page>-<n>.png
        debug/{paragraphs.json,issues.json}
        corrections.json
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .db import connect, jdump, jload, now
from .models import (
    Book,
    Chapter,
    Comment,
    Diagram,
    Game,
    Node,
    Section,
    ValidationIssue,
)
from .pipeline.build import BuildOutput
from .storage import ensure_book_dirs, read_json, write_json


def save_book(output: BuildOutput, source_pdf: Path) -> None:
    conn = connect()
    book = output.book
    paths = ensure_book_dirs(book.id)

    _write_extracted(output, paths)

    with conn:
        conn.execute("DELETE FROM books WHERE id = ?", (book.id,))
        conn.execute(
            """INSERT INTO books(id, title, authors, source_filename, content_hash, page_count,
                                status, has_text_layer, metrics, toc, extraction_report, error,
                                processor_version, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                book.id,
                book.title,
                jdump(book.authors),
                book.source_filename,
                book.content_hash,
                book.page_count,
                book.status,
                int(book.has_text_layer),
                jdump(book.metrics.model_dump()),
                jdump([t.model_dump() for t in book.toc]),
                jdump(book.extraction_report),
                book.error,
                book.processor_version,
                now(),
                now(),
            ),
        )

        conn.executemany(
            """INSERT OR REPLACE INTO chapters(id, book_id, parent_id, idx, level, kind, title,
                                              start_page, end_page, game_ids, source, confidence)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    c.id,
                    book.id,
                    c.parent_id,
                    c.index,
                    c.level,
                    c.kind,
                    c.title,
                    c.start_page,
                    c.end_page,
                    jdump(c.game_ids),
                    jdump(c.source.model_dump(exclude_none=True)) if c.source else None,
                    c.confidence,
                )
                for c in output.chapters
            ],
        )

        conn.executemany(
            """INSERT OR REPLACE INTO games(id, book_id, chapter_id, section_id, idx, title,
                                           players, event, site, date, round, result, eco,
                                           opening, variation_name, ply_count, node_count,
                                           variation_count, comment_count, start_page, end_page,
                                           root_fen, final_fen, confidence, parse_status,
                                           detection_heuristic, stats, headers, source, data_path)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    g.id,
                    book.id,
                    g.chapter_id,
                    g.section_id,
                    g.index,
                    g.title,
                    jdump(g.players),
                    g.event,
                    g.site,
                    g.date,
                    g.round,
                    g.result,
                    g.eco,
                    g.opening,
                    g.variation_name,
                    g.ply_count,
                    g.node_count,
                    g.variation_count,
                    g.comment_count,
                    g.start_page,
                    g.end_page,
                    g.root_fen,
                    g.final_fen,
                    g.confidence,
                    g.parse_status,
                    g.detection_heuristic,
                    jdump(g.stats),
                    jdump(g.headers),
                    jdump(g.source.model_dump(exclude_none=True)) if g.source else None,
                    f"extracted/games/{g.id}.json",
                )
                for g in output.games
            ],
        )

        nodes: list[tuple] = []
        depths: dict[str, int] = {}
        for game in output.games:
            for node in game.tree:
                parent = node.parent_id
                depths[node.id] = 0 if parent is None else depths.get(parent, 0) + 1
                nodes.append(
                    (
                        node.id,
                        game.id,
                        node.parent_id,
                        node.ply,
                        node.move_number,
                        node.side,
                        node.san,
                        node.move_uci,
                        node.fen_before,
                        node.fen_after,
                        jdump(node.annotations),
                        jdump(node.nags),
                        jdump(node.comment_ids),
                        jdump(node.child_ids),
                        node.mainline_child_id,
                        int(node.is_mainline),
                        node.variation_index,
                        node.variation_depth,
                        node.started_by,
                        node.move_origin,
                        node.source_text,
                        node.source.page if node.source else None,
                        jdump(node.source.bbox) if node.source and node.source.bbox else None,
                        node.confidence,
                        jdump(node.issues),
                        depths[node.id],
                    )
                )
        if nodes:
            conn.executemany(
                """INSERT OR REPLACE INTO game_nodes(id, game_id, parent_id, ply, move_number, side,
                        san, move_uci, fen_before, fen_after, annotations, nags, comment_ids,
                        child_ids, mainline_child_id, is_mainline, variation_index,
                        variation_depth, started_by, move_origin, source_text, page, bbox,
                        confidence, issues, depth)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                nodes,
            )

        conn.executemany(
            """INSERT OR REPLACE INTO comments(id, book_id, game_id, node_id, page, bbox, text,
                                              confidence, anchor_kind, signals)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    c.id,
                    book.id,
                    g.id,
                    c.attached_to,
                    c.source.page if c.source else None,
                    jdump(c.source.bbox) if c.source and c.source.bbox else None,
                    c.text,
                    c.confidence,
                    c.anchor_kind,
                    jdump(c.signals),
                )
                for g in output.games
                for c in g.comments
            ],
        )

        conn.executemany(
            """INSERT OR REPLACE INTO diagrams(id, book_id, game_id, node_id, page, bbox, width,
                    height, kind, is_board_candidate, board_score, caption, fen,
                    position_confidence, position_method, grid, image_path, confidence, notes)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    d.id,
                    book.id,
                    d.game_id,
                    d.node_id,
                    d.page,
                    jdump(d.bbox),
                    d.width,
                    d.height,
                    d.kind,
                    int(d.is_board_candidate),
                    d.board_score,
                    d.caption,
                    d.fen,
                    d.position_confidence.value,
                    d.position_method,
                    jdump(d.grid_detected),
                    d.image_path,
                    d.confidence,
                    jdump(d.notes),
                )
                for d in output.diagrams
            ],
        )

        if output.extraction is not None:
            conn.executemany(
                """INSERT OR REPLACE INTO source_pages(book_id, page, width, height, text,
                        char_count, has_text, image_count)
                   VALUES(?,?,?,?,?,?,?,?)""",
                [
                    (
                        book.id,
                        p.number,
                        p.width,
                        p.height,
                        (output.extraction.per_page_text.get(p.number) or "")[:20000],
                        p.text_char_count,
                        int(p.text_char_count > 0),
                        len(p.images),
                    )
                    for p in output.extraction.pages
                ],
            )

        conn.executemany(
            """INSERT OR REPLACE INTO validation_issues(id, book_id, game_id, severity, code,
                    message, page, node_id, source_text, source, context)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    i.id,
                    book.id,
                    i.game_id,
                    i.severity.value,
                    i.code,
                    i.message,
                    i.page,
                    i.node_id,
                    i.source_text,
                    jdump(i.source.model_dump(exclude_none=True)) if i.source else None,
                    jdump(i.context),
                )
                for i in output.issues
            ],
        )

        _reindex_search(conn, book, output)

    write_json(paths["metadata"], book.model_dump(mode="json"))


def _write_extracted(output: BuildOutput, paths: dict[str, Path]) -> None:
    book = output.book
    if output.extraction is not None:
        write_json(
            paths["pages_text"],
            {
                "pages": [
                    {
                        "number": p.number,
                        "width": p.width,
                        "height": p.height,
                        "rotation": p.rotation,
                        "char_count": p.text_char_count,
                        "lines": [
                            {
                                "text": ln.text,
                                "bbox": ln.bbox,
                                "column": ln.column,
                                "font_size": ln.font_size,
                                "role": ln.role,
                            }
                            for ln in p.lines
                        ],
                        "text": (output.extraction.per_page_text.get(p.number) or "")[:20000],
                    }
                    for p in output.extraction.pages
                ]
            },
        )
        write_json(
            paths["layout"],
            {
                "modes": [
                    {"page": lp.page, "mode": lp.mode, "columns": len(lp.columns),
                     "width": lp.width, "height": lp.height}
                    for lp in output.layout
                ],
                "paragraphs": [p.model_dump(mode="json") for p in output.paragraphs],
            },
        )
    write_json(
        paths["games"],
        [
            {
                "id": g.id,
                "title": g.title,
                "players": g.players,
                "result": g.result,
                "start_page": g.start_page,
                "end_page": g.end_page,
                "stats": g.stats,
                "confidence": g.confidence,
                "parse_status": g.parse_status,
                "mainline_san": g.mainline_san,
            }
            for g in output.games
        ],
    )
    for game in output.games:
        write_json(
            paths["extracted"] / "games" / f"{game.id}.json",
            {
                "game": game.model_dump(mode="json", exclude={"tree", "comments"}),
                "nodes": [n.model_dump(mode="json") for n in game.tree],
                "comments": [c.model_dump(mode="json") for c in game.comments],
            },
            indent=None,
        )
    write_json(
        paths["debug"] / "issues.json",
        [i.model_dump(mode="json") for i in output.issues],
    )
    write_json(
        paths["debug"] / "paragraphs.json",
        [p.model_dump(mode="json") for p in output.paragraphs],
    )


def _reindex_search(conn: sqlite3.Connection, book: Book, output: BuildOutput) -> None:
    """Rebuild the FTS index for this book (charter #25)."""
    conn.execute("DELETE FROM search_index WHERE book_id = ?", (book.id,))
    rows: list[tuple] = []
    for chapter in output.chapters:
        rows.append(
            (
                "chapter",
                chapter.id,
                book.id,
                chapter.title,
                f"{chapter.kind} on pages {chapter.start_page}-{chapter.end_page}",
                "",
                "",
                chapter.start_page,
            )
        )
    for game in output.games:
        body_parts = [c.text for c in game.comments]
        body_parts.append(game.raw_text)
        rows.append(
            (
                "game",
                game.id,
                book.id,
                game.title,
                "\n".join(body_parts)[:40000],
                " ".join(x for x in (game.white, game.black) if x),
                " ".join(x for x in (game.event, game.opening, game.eco, game.result) if x),
                game.start_page,
            )
        )
    # Everything else in the book stays searchable even when it produced no game.
    for i, para in enumerate(output.paragraphs):
        if len(para.text) < 40:
            continue
        rows.append(
            ("paragraph", f"p{i}", book.id, para.text[:120], para.text, "", para.kind, para.page)
        )
    conn.executemany(
        """INSERT INTO search_index(kind, ref_id, book_id, title, body, players, tokens, page)
           VALUES(?,?,?,?,?,?,?,?)""",
        rows,
    )


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def load_book(book_id: str) -> dict[str, Any] | None:
    conn = connect()
    row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["metrics"] = jload(data.get("metrics"), {})
    data["toc"] = jload(data.get("toc"), [])
    data["extraction_report"] = jload(data.get("extraction_report"), {})
    data["authors"] = jload(data.get("authors"), [])
    return data


def list_books() -> list[dict[str, Any]]:
    conn = connect()
    rows = conn.execute(
        "SELECT id, title, source_filename, page_count, status, metrics, created_at, updated_at,"
        " has_text_layer FROM books ORDER BY updated_at DESC"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["metrics"] = jload(d.get("metrics"), {})
        out.append(d)
    return out


def find_book_by_hash(content_hash: str) -> str | None:
    conn = connect()
    row = conn.execute(
        "SELECT id FROM books WHERE content_hash = ? ORDER BY updated_at DESC LIMIT 1",
        (content_hash,),
    ).fetchone()
    return row["id"] if row else None


def load_chapters(book_id: str) -> list[dict[str, Any]]:
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM chapters WHERE book_id = ? ORDER BY idx", (book_id,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["game_ids"] = jload(d.get("game_ids"), [])
        out.append(d)
    return out


def list_games(book_id: str, chapter_id: str | None = None) -> list[dict[str, Any]]:
    conn = connect()
    sql = "SELECT * FROM games WHERE book_id = ?"
    args: list[Any] = [book_id]
    if chapter_id:
        sql += " AND chapter_id = ?"
        args.append(chapter_id)
    sql += " ORDER BY idx"
    out = []
    for r in conn.execute(sql, args).fetchall():
        d = dict(r)
        d["players"] = jload(d.get("players"), {})
        d["stats"] = jload(d.get("stats"), {})
        d["headers"] = jload(d.get("headers"), {})
        out.append(d)
    return out


def load_game(game_id: str) -> dict[str, Any] | None:
    conn = connect()
    row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    for key in ("players", "stats", "headers"):
        d[key] = jload(d.get(key), {})
    return d


def load_tree(game_id: str) -> dict[str, Any]:
    conn = connect()
    nodes = []
    comments_by_node: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT * FROM game_nodes WHERE game_id = ?", (game_id,)).fetchall():
        d = dict(r)
        for key in ("annotations", "nags", "comment_ids", "child_ids", "issues"):
            d[key] = jload(d.get(key), [])
        d["bbox"] = jload(d.get("bbox"), None)
        d["is_mainline"] = bool(d.get("is_mainline"))
        nodes.append(d)
    for r in conn.execute(
        "SELECT * FROM comments WHERE game_id = ?", (game_id,)
    ).fetchall():
        d = dict(r)
        d["signals"] = jload(d.get("signals"), {})
        comments_by_node.setdefault(d.get("node_id") or "", []).append(d)
    return {"nodes": nodes, "comments": comments_by_node}


def load_issues(book_id: str, severity: str | None = None, limit: int = 500) -> list[dict]:
    conn = connect()
    sql = "SELECT * FROM validation_issues WHERE book_id = ?"
    args: list[Any] = [book_id]
    if severity:
        sql += " AND severity = ?"
        args.append(severity)
    sql += " ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, page LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def load_diagrams(book_id: str, game_id: str | None = None) -> list[dict]:
    conn = connect()
    sql = "SELECT * FROM diagrams WHERE book_id = ?"
    args: list[Any] = [book_id]
    if game_id:
        sql += " AND game_id = ?"
        args.append(game_id)
    out = []
    for r in conn.execute(sql, args).fetchall():
        d = dict(r)
        d["bbox"] = jload(d.get("bbox"), [])
        d["grid"] = jload(d.get("grid"), {})
        d["notes"] = jload(d.get("notes"), [])
        d["is_board_candidate"] = bool(d.get("is_board_candidate"))
        out.append(d)
    return out


def search(book_id: str | None, query: str, limit: int = 50) -> list[dict]:
    conn = connect()
    sql = """
        SELECT s.kind, s.ref_id, s.book_id, s.title, s.page, s.players, s.tokens,
               snippet(search_index, 4, '', '', ' ... ', 24) AS snip,
               bm25(search_index) AS score
        FROM search_index s
        WHERE search_index MATCH ? AND (? IS NULL OR s.book_id = ?)
        ORDER BY score LIMIT ?
    """
    try:
        rows = conn.execute(sql, (query, book_id, book_id, limit)).fetchall()
    except sqlite3.OperationalError:
        # a user query that FTS5 cannot parse: fall back to a LIKE scan
        like = f"%{query}%"
        rows = conn.execute(
            """SELECT kind, ref_id, book_id, title, page, players, tokens,
                      substr(body, 1, 180) AS snip, 0 AS score
               FROM search_index WHERE (title LIKE ? OR body LIKE ?)
                 AND (? IS NULL OR book_id = ?) LIMIT ?""",
            (like, like, book_id, book_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def save_progress(
    book_id: str,
    *,
    game_id: str | None,
    node_id: str | None,
    chapter_id: str | None = None,
    move_index: int = 0,
    orientation: str = "white",
    scope: str = "reader",
) -> None:
    conn = connect()
    with conn:
        conn.execute(
            """INSERT INTO reading_progress(book_id, scope, game_id, node_id, chapter_id,
                    move_index, orientation, updated_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(book_id, scope) DO UPDATE SET
                 game_id=excluded.game_id, node_id=excluded.node_id,
                 chapter_id=excluded.chapter_id, move_index=excluded.move_index,
                 orientation=excluded.orientation, updated_at=excluded.updated_at""",
            (book_id, scope, game_id, node_id, chapter_id, move_index, orientation, now()),
        )


def load_progress(book_id: str, scope: str = "reader") -> dict | None:
    conn = connect()
    row = conn.execute(
        "SELECT * FROM reading_progress WHERE book_id = ? AND scope = ?", (book_id, scope)
    ).fetchone()
    return dict(row) if row else None


def delete_book(book_id: str) -> None:
    conn = connect()
    with conn:
        conn.execute("DELETE FROM search_index WHERE book_id = ?", (book_id,))
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))


def save_correction(
    book_id: str, game_id: str | None, node_id: str | None, page: int | None,
    field: str, original: str | None, replacement: str | None,
) -> str:
    """Corrections are stored *beside* the extraction, never over it (#41)."""
    from .storage import new_id

    conn = connect()
    cid = new_id("fix")
    with conn:
        conn.execute(
            """INSERT INTO corrections(id, book_id, game_id, node_id, page, field, original,
                    replacement, created_at, author)
               VALUES(?,?,?,?,?,?,?,?,?,'user')""",
            (cid, book_id, game_id, node_id, page, field, original, replacement, now()),
        )
    return cid


def list_corrections(book_id: str) -> list[dict]:
    conn = connect()
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM corrections WHERE book_id = ? ORDER BY created_at DESC", (book_id,)
        ).fetchall()
    ]