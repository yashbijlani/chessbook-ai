"""End-to-end pipeline tests over the generated corpus (charter #29, #47).

These are the tests that matter most: each one names the *class* of PDF problem
it exercises, and asserts the behaviour we promise for that class.
"""

from __future__ import annotations

import json
from pathlib import Path

import chess
import pytest

from chessbook.pipeline.build import process_pdf

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")

GAMES_PER_FILE = {
    "clean_single_column.pdf": 4,
    "two_column.pdf": 1,
    "parallel_columns.pdf": 1,
    "variations.pdf": 1,
    "figurine.pdf": 1,
    "weird_typography.pdf": 1,
    "diagram_heavy.pdf": 1,
    "with_toc_and_headers.pdf": 2,
    "ambiguous.pdf": 4,
}


def run(settings, corpus: Path, name: str):
    return process_pdf(corpus / name, "bk_test", name.replace(".pdf", ""), settings)


def assert_every_game_is_real_chess(book) -> None:
    """Charter #31, applied to whole books: replay every reconstructed game."""
    for game in book.games:
        by_id = {n.id: n for n in game.tree}
        root = next((n for n in game.tree if n.parent_id is None), None)
        assert root is not None, f"{game.title}: no root node"

        # (a) the main line must be a chain of real chess
        board = chess.Board(root.fen_before)
        cur = root
        seen = set()
        while cur.mainline_child_id:
            assert cur.mainline_child_id not in seen, "cycle in the main line"
            seen.add(cur.mainline_child_id)
            nxt = by_id.get(cur.mainline_child_id)
            assert nxt is not None, "main-line pointer points at a missing node"
            assert nxt.fen_before == board.fen(), (
                f"{book.book.title} / {game.title}: FEN mismatch at {nxt.san}"
            )
            board = chess.Board(nxt.fen_after)
            cur = nxt

        # (b) every side line must start from a position that admits its move
        for node in game.tree:
            if node.parent_id is None or node.move_uci is None:
                continue
            parent = by_id[node.parent_id]
            try:
                vb = chess.Board(parent.fen_after)
            except ValueError:  # pragma: no cover
                continue
            assert chess.Move.from_uci(node.move_uci) in vb.legal_moves, (
                f"{book.book.title} / {game.title}: {node.san} is illegal in its parent"
            )


@pytest.mark.parametrize("name", sorted(GAMES_PER_FILE))
def test_corpus_file(settings, ensure_corpus: Path, name: str) -> None:
    book = run(settings, ensure_corpus, name)
    metrics = book.book.metrics
    assert metrics.pages > 0
    assert metrics.games == GAMES_PER_FILE[name], (
        f"{name}: expected {GAMES_PER_FILE[name]} games, got {metrics.games}"
    )
    assert metrics.moves > 20
    assert book.book.has_text_layer
    assert_every_game_is_real_chess(book)


def test_clean_book_is_error_free(settings, ensure_corpus: Path) -> None:
    """Type A: a well-behaved digital book must import without complaints."""
    book = run(settings, ensure_corpus, "clean_single_column.pdf")
    assert book.book.metrics.errors == 0
    assert book.book.metrics.warnings == 0
    assert book.book.metrics.moves == 129
    # not even an info-level note: a clean book is read without any complaint
    assert book.book.metrics.issues == 0, book.book.metrics
    titles = [g.title for g in book.games]
    assert titles[0].startswith("Game 1: Morphy")
    assert book.games[0].white == "Morphy"
    assert book.games[0].black == "Duke of Brunswick"
    assert book.games[0].event == "Paris 1858"
    assert book.games[0].result == "1-0"
    assert book.games[0].mainline_san[-1] == "Rd8#"
    # running heads, folios and the chapter heading must not leak into the text
    raw = "\n".join(g.raw_text for g in book.games)
    assert "The Classical Chess Openings" not in raw
    assert book.book.metrics.chapters >= 1


def test_two_column_layout_reads_in_order(settings, ensure_corpus: Path) -> None:
    """Type C: multi-column prose must not be concatenated blindly."""
    book = run(settings, ensure_corpus, "two_column.pdf")
    modes = book.book.extraction_report["layout_modes"]
    assert modes["columns"] >= 1
    game = book.games[0]
    assert game.ply_count == 22
    assert game.mainline_san[0] == "d4"


def test_parallel_columns_interleaves_commentary(settings, ensure_corpus: Path) -> None:
    """Type B: moves left, commentary right, same row bands."""
    book = run(settings, ensure_corpus, "parallel_columns.pdf")
    assert book.book.extraction_report["layout_modes"]["parallel"] == 1
    game = book.games[0]
    assert game.ply_count == 33
    assert game.comment_count >= 5
    # every comment must be attached to a real node
    ids = {n.id for n in game.tree}
    assert all(c.attached_to in ids for c in game.comments)


def test_variations_are_not_flattened(settings, ensure_corpus: Path) -> None:
    """Type H: side lines survive as a tree."""
    book = run(settings, ensure_corpus, "variations.pdf")
    game = book.games[0]
    assert game.ply_count == 33
    assert game.variation_count >= 3
    assert_every_game_is_real_chess(book)
    assert book.book.metrics.errors == 0
    # each variation branch must start from a legal parent position
    by_id = {n.id: n for n in game.tree}
    for node in game.tree:
        if node.is_mainline or node.parent_id is None or node.move_uci is None:
            continue
        parent = by_id[node.parent_id]
        board = chess.Board(parent.fen_after)
        assert chess.Move.from_uci(node.move_uci) in board.legal_moves


def test_figurine_notation(settings, ensure_corpus: Path) -> None:
    """Charter #7: solid and outline figurines."""
    book = run(settings, ensure_corpus, "figurine.pdf")
    game = book.games[0]
    assert game.ply_count == 33
    assert game.mainline_san[:4] == ["e4", "e5", "Nf3", "d6"]
    assert game.stats["invalid"] == 0


def test_unusual_typography(settings, ensure_corpus: Path) -> None:
    """Move numbers in a hanging margin, and hyphenated prose."""
    book = run(settings, ensure_corpus, "weird_typography.pdf")
    game = book.games[0]
    assert game.ply_count == 33
    assert game.stats["invalid"] == 0


def test_prose_with_inline_moves_yields_no_game(settings, ensure_corpus: Path) -> None:
    """Type F: quoted moves are commentary, not a game."""
    book = run(settings, ensure_corpus, "prose_heavy.pdf")
    assert book.book.metrics.games == 0
    assert book.paragraphs, "the prose itself must still be captured for search"


def test_diagrams_detected_and_positions_inherited(settings, ensure_corpus: Path) -> None:
    """Type E: boards are found and their position comes from the moves."""
    book = run(settings, ensure_corpus, "diagram_heavy.pdf")
    assert book.book.metrics.board_diagrams >= 3
    resolved = [d for d in book.diagrams if d.fen]
    assert resolved, "at least one diagram should inherit a position"
    for d in resolved:
        chess.Board(d.fen)
        assert d.position_method in (
            "inherited_from_preceding_moves",
            "mainline_node_on_page",
            "nearest_preceding_move_run",
        )
        assert d.position_confidence.value in ("EXACT", "HIGH_CONFIDENCE", "PROBABLE")


def test_running_heads_and_toc(settings, ensure_corpus: Path) -> None:
    """Headers, folios, front matter and a table of contents."""
    book = run(settings, ensure_corpus, "with_toc_and_headers.pdf")
    assert book.book.metrics.games == 2
    assert len(book.book.toc) >= 4
    assert book.book.extraction_report["printed_page_offset"] is not None
    for game in book.games:
        assert "MODERN CHESS THEORY" not in game.raw_text


def test_illegal_move_is_surfaced_not_hidden(settings, ensure_corpus: Path) -> None:
    """The honesty tests: an impossible move must be reported."""
    book = run(settings, ensure_corpus, "ambiguous.pdf")
    codes = {i.code for i in book.issues}
    assert "game_starts_midgame" in codes, codes
    assert book.book.metrics.errors >= 1
    unparseable = [g for g in book.games if g.parse_status == "failed"]
    assert unparseable, "the mid-game fragment must be marked failed, not faked"
    for game in unparseable:
        assert game.ply_count == 0


def test_ambiguity_is_reported(settings, ensure_corpus: Path) -> None:
    book = run(settings, ensure_corpus, "ambiguous.pdf")
    ambiguous = [g for g in book.games if any(i.code == "ambiguous_move" for i in book.issues
                                              if i.game_id == g.id)]
    assert ambiguous, "a genuinely ambiguous move must be reported"
    assert_every_game_is_real_chess(book)


def test_confidence_is_populated(settings, ensure_corpus: Path) -> None:
    book = run(settings, ensure_corpus, "clean_single_column.pdf")
    for game in book.games:
        assert 0.0 < game.confidence <= 1.0
        assert game.detection_heuristic
        for node in game.tree:
            assert 0.0 <= node.confidence <= 1.0


def test_pages_carry_source_references(settings, ensure_corpus: Path) -> None:
    book = run(settings, ensure_corpus, "clean_single_column.pdf")
    seen_pages = set()
    for game in book.games:
        for node in game.tree:
            if node.source is None:
                continue
            assert node.source.page >= 1
            assert len(node.source.bbox) == 4
            seen_pages.add(node.source.page)
    assert len(seen_pages) >= 2, "sources must point at the real pages"


def test_recovery_level_is_reported(settings, ensure_corpus: Path) -> None:
    clean = run(settings, ensure_corpus, "clean_single_column.pdf")
    assert clean.book.metrics.recovery_level == 1
    messy = run(settings, ensure_corpus, "ambiguous.pdf")
    assert messy.book.metrics.recovery_level >= 2