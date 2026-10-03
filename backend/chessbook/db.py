"""SQLite persistence (#34).

Deliberately hybrid: relational columns for the things we query/sort on
(books, chapters, games, nodes, issues, progress) plus JSON columns for the
rich internal objects (nodes carry their own source refs / annotations).  That
avoids over-normalising a tree structure while keeping FTS5 fast.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .config import get_settings

SCHEMA_VERSION = 1

_LOCAL = threading.local()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS books (
    id                TEXT PRIMARY KEY,
    title             TEXT NOT NULL DEFAULT '',
    authors           TEXT NOT NULL DEFAULT '[]',
    source_filename   TEXT NOT NULL DEFAULT '',
    content_hash      TEXT NOT NULL DEFAULT '',
    page_count        INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'pending',
    has_text_layer    INTEGER NOT NULL DEFAULT 1,
    metrics           TEXT NOT NULL DEFAULT '{}',
    toc               TEXT NOT NULL DEFAULT '[]',
    extraction_report TEXT NOT NULL DEFAULT '{}',
    error             TEXT,
    processor_version TEXT NOT NULL DEFAULT '0.1.0',
    created_at        REAL NOT NULL DEFAULT 0,
    updated_at        REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_books_hash ON books(content_hash);
CREATE INDEX IF NOT EXISTS idx_books_created ON books(created_at DESC);

CREATE TABLE IF NOT EXISTS chapters (
    id          TEXT PRIMARY KEY,
    book_id     TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    parent_id   TEXT,
    idx         INTEGER NOT NULL DEFAULT 0,
    level       INTEGER NOT NULL DEFAULT 0,
    kind        TEXT NOT NULL DEFAULT 'chapter',
    title       TEXT NOT NULL DEFAULT '',
    start_page  INTEGER,
    end_page    INTEGER,
    game_ids    TEXT NOT NULL DEFAULT '[]',
    source      TEXT,
    confidence  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chapters_book ON chapters(book_id, idx);

CREATE TABLE IF NOT EXISTS sections (
    id          TEXT PRIMARY KEY,
    book_id     TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    parent_id   TEXT,
    idx         INTEGER NOT NULL DEFAULT 0,
    level       INTEGER NOT NULL DEFAULT 0,
    title       TEXT NOT NULL DEFAULT '',
    start_page  INTEGER,
    end_page    INTEGER,
    game_ids    TEXT NOT NULL DEFAULT '[]',
    source      TEXT,
    confidence  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sections_book ON sections(book_id, idx);

CREATE TABLE IF NOT EXISTS games (
    id            TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_id    TEXT,
    section_id    TEXT,
    idx           INTEGER NOT NULL DEFAULT 0,
    title         TEXT NOT NULL DEFAULT '',
    players       TEXT NOT NULL DEFAULT '{}',
    event         TEXT,
    site          TEXT,
    date          TEXT,
    round         TEXT,
    result        TEXT,
    eco           TEXT,
    opening       TEXT,
    variation_name TEXT,
    ply_count     INTEGER NOT NULL DEFAULT 0,
    node_count    INTEGER NOT NULL DEFAULT 0,
    variation_count INTEGER NOT NULL DEFAULT 0,
    comment_count INTEGER NOT NULL DEFAULT 0,
    start_page    INTEGER,
    end_page      INTEGER,
    root_fen      TEXT NOT NULL,
    final_fen     TEXT NOT NULL,
    confidence    REAL NOT NULL DEFAULT 0,
    parse_status  TEXT NOT NULL DEFAULT 'ok',
    detection_heuristic TEXT NOT NULL DEFAULT '',
    stats         TEXT NOT NULL DEFAULT '{}',
    headers       TEXT NOT NULL DEFAULT '{}',
    source        TEXT,
    data_path     TEXT
);
CREATE INDEX IF NOT EXISTS idx_games_book ON games(book_id, idx);
CREATE INDEX IF NOT EXISTS idx_games_page ON games(book_id, start_page);

CREATE TABLE IF NOT EXISTS game_nodes (
    id            TEXT PRIMARY KEY,
    game_id       TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    parent_id     TEXT,
    ply           INTEGER NOT NULL DEFAULT 0,
    move_number   INTEGER NOT NULL DEFAULT 1,
    side          TEXT NOT NULL DEFAULT 'w',
    san           TEXT,
    move_uci      TEXT,
    fen_before    TEXT NOT NULL,
    fen_after     TEXT NOT NULL,
    annotations   TEXT NOT NULL DEFAULT '[]',
    nags          TEXT NOT NULL DEFAULT '[]',
    comment_ids   TEXT NOT NULL DEFAULT '[]',
    child_ids     TEXT NOT NULL DEFAULT '[]',
    mainline_child_id TEXT,
    is_mainline   INTEGER NOT NULL DEFAULT 1,
    variation_index INTEGER NOT NULL DEFAULT 0,
    variation_depth INTEGER NOT NULL DEFAULT 0,
    started_by    TEXT NOT NULL DEFAULT 'mainline',
    move_origin   TEXT NOT NULL DEFAULT 'parsed',
    source_text   TEXT NOT NULL DEFAULT '',
    page          INTEGER,
    bbox          TEXT,
    confidence    REAL NOT NULL DEFAULT 0,
    issues        TEXT NOT NULL DEFAULT '[]',
    depth         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_nodes_game ON game_nodes(game_id, ply);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON game_nodes(parent_id);

CREATE TABLE IF NOT EXISTS comments (
    id          TEXT PRIMARY KEY,
    book_id     TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    game_id     TEXT,
    node_id     TEXT,
    page        INTEGER,
    bbox        TEXT,
    text        TEXT NOT NULL DEFAULT '',
    confidence  REAL NOT NULL DEFAULT 0,
    anchor_kind TEXT NOT NULL DEFAULT 'preceding_move',
    signals     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_comments_game ON comments(game_id);
CREATE INDEX IF NOT EXISTS idx_comments_node ON comments(node_id);

CREATE TABLE IF NOT EXISTS diagrams (
    id           TEXT PRIMARY KEY,
    book_id      TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    game_id      TEXT,
    node_id      TEXT,
    page         INTEGER NOT NULL,
    bbox         TEXT NOT NULL DEFAULT '[]',
    width        REAL NOT NULL DEFAULT 0,
    height       REAL NOT NULL DEFAULT 0,
    kind         TEXT NOT NULL DEFAULT 'embedded_image',
    is_board_candidate INTEGER NOT NULL DEFAULT 0,
    board_score  REAL NOT NULL DEFAULT 0,
    caption      TEXT,
    fen          TEXT,
    position_confidence TEXT NOT NULL DEFAULT 'UNKNOWN',
    position_method TEXT,
    grid         TEXT NOT NULL DEFAULT '{}',
    image_path   TEXT,
    confidence   REAL NOT NULL DEFAULT 0,
    notes        TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_diagrams_book ON diagrams(book_id, page);

CREATE TABLE IF NOT EXISTS source_pages (
    book_id     TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    page        INTEGER NOT NULL,
    width       REAL NOT NULL DEFAULT 0,
    height      REAL NOT NULL DEFAULT 0,
    text        TEXT NOT NULL DEFAULT '',
    char_count  INTEGER NOT NULL DEFAULT 0,
    has_text    INTEGER NOT NULL DEFAULT 1,
    image_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, page)
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id          TEXT PRIMARY KEY,
    book_id     TEXT,
    filename    TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'queued',
    progress    REAL NOT NULL DEFAULT 0,
    stages      TEXT NOT NULL DEFAULT '[]',
    messages    TEXT NOT NULL DEFAULT '[]',
    logs        TEXT NOT NULL DEFAULT '[]',
    error       TEXT,
    reuse_book_id TEXT,
    created_at  REAL NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON import_jobs(created_at DESC);

CREATE TABLE IF NOT EXISTS validation_issues (
    id         TEXT PRIMARY KEY,
    book_id    TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    game_id    TEXT,
    severity   TEXT NOT NULL DEFAULT 'warning',
    code       TEXT NOT NULL DEFAULT '',
    message    TEXT NOT NULL DEFAULT '',
    page       INTEGER,
    node_id    TEXT,
    source_text TEXT,
    source     TEXT,
    context    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_issues_book ON validation_issues(book_id, severity);
CREATE INDEX IF NOT EXISTS idx_issues_game ON validation_issues(game_id);

CREATE TABLE IF NOT EXISTS corrections (
    id         TEXT PRIMARY KEY,
    book_id    TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    game_id    TEXT,
    node_id    TEXT,
    page       INTEGER,
    field      TEXT NOT NULL,
    original   TEXT,
    replacement TEXT,
    created_at REAL NOT NULL DEFAULT 0,
    author     TEXT NOT NULL DEFAULT 'user'
);
CREATE INDEX IF NOT EXISTS idx_corrections_book ON corrections(book_id);

CREATE TABLE IF NOT EXISTS reading_progress (
    book_id   TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    scope     TEXT NOT NULL DEFAULT 'reader',
    game_id   TEXT,
    node_id   TEXT,
    chapter_id TEXT,
    move_index INTEGER NOT NULL DEFAULT 0,
    orientation TEXT NOT NULL DEFAULT 'white',
    updated_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, scope)
);

CREATE TABLE IF NOT EXISTS completed_chapters (
    book_id    TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL,
    completed_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, chapter_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    kind UNINDEXED,
    ref_id UNINDEXED,
    book_id UNINDEXED,
    title,
    body,
    players,
    tokens,
    page UNINDEXED
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Thread-local connection (uvicorn workers use threads)."""
    db_path = Path(path) if path else get_settings().db_path
    key = f"conn::{db_path}"
    conn: sqlite3.Connection | None = getattr(_LOCAL, key, None)
    if conn is None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        setattr(_LOCAL, key, conn)
    return conn


def init_db(path: Path | None = None) -> None:
    conn = connect(path)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def jdump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def jload(value: Any, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def rows_to_dicts(rows: Iterator[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def now() -> float:
    return time.time()