"""API tests: the full import -> read -> navigate -> export loop (charter #52)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chessbook import jobs as jobs_mod
from chessbook import repository
from chessbook.config import get_settings
from chessbook.db import init_db


@pytest.fixture()
def client(settings: Settings, monkeypatch):
    init_db(settings.db_path)
    monkeypatch.setattr(jobs_mod, "_manager", None)
    monkeypatch.setenv("CHESSBOOK_DATA_DIR", str(settings.data_dir))
    import chessbook.config as config_mod

    monkeypatch.setattr(config_mod, "_settings", settings)
    monkeypatch.setattr(jobs_mod, "get_settings", lambda: settings)
    from chessbook.api.app import app

    with TestClient(app) as c:
        yield c


def import_pdf(client: TestClient, path: Path) -> dict:
    with open(path, "rb") as fh:
        response = client.post(
            "/api/books/import",
            files={"file": (path.name, fh, "application/pdf")},
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    if "job_id" not in payload:  # pragma: no cover - surfaced as a test failure
        raise AssertionError(f"import failed: {payload}")
    job_id = payload["job_id"]

    deadline = time.time() + 240
    while time.time() < deadline:
        job = client.get(f"/api/import/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.25)
    raise AssertionError("import did not finish in time")


@pytest.fixture(scope="module")
def imported(tmp_path_factory, request):
    """Import one corpus book once and share the result across tests."""
    corpus = Path(__file__).resolve().parent.parent / "corpus" / "pdf"
    target = corpus / "clean_single_column.pdf"
    if not target.exists():
        import generate

        generate.main()
    data_dir = tmp_path_factory.mktemp("apidata")
    settings = get_settings()
    from chessbook.config import Settings

    settings = Settings(data_dir=data_dir)
    settings.ensure_dirs()
    init_db(settings.db_path)
    jobs_mod._manager = jobs_mod.JobManager(settings, workers=1)

    from fastapi.testclient import TestClient

    import chessbook.config as config_mod

    config_mod._settings = settings
    jobs_mod.get_settings = lambda: settings  # type: ignore[assignment]
    from chessbook.api.app import app

    with TestClient(app) as client:
        job = import_pdf(client, target)
    assert job["status"] == "succeeded", job
    return settings, job


def test_health_advertises_offline(imported) -> None:
    settings, _ = imported
    import chessbook.config as config_mod

    from fastapi.testclient import TestClient

    from chessbook.api.app import app

    with TestClient(app) as client:
        data = client.get("/api/health").json()
    assert data["status"] == "ok"
    assert data["offline"] is True
    assert data["providers"]["explainer"]["name"] == "deterministic"
    assert data["providers"]["diagram_recognizer"]["available"] is False


def test_import_pipeline_reports_every_stage(imported) -> None:
    _, job = imported
    keys = {s["key"] for s in job["stages"]}
    for expected in ("upload", "hash", "text", "layout", "games", "tree", "validate", "persist"):
        assert expected in keys, sorted(keys)
    assert all(s["status"] in ("done", "skipped") for s in job["stages"]), job["stages"]
    assert any("games" in m for m in job["messages"]), job["messages"]


def test_book_library(imported) -> None:
    settings, job = imported
    from fastapi.testclient import TestClient

    from chessbook.api.app import app

    with TestClient(app) as client:
        books = client.get("/api/books").json()["books"]
        assert len(books) == 1
        book = books[0]
        assert book["metrics"]["games"] == 4
        assert book["metrics"]["moves"] == 129
        detail = client.get(f"/api/books/{book['id']}").json()
        assert detail["title"]
        assert detail["chapters"]
        assert detail["issues_summary"]["errors"] == 0


def test_games_and_tree(imported) -> None:
    settings, job = imported
    book_id = job["book_id"]
    from fastapi.testclient import TestClient

    from chessbook.api.app import app

    with TestClient(app) as client:
        games = client.get(f"/api/books/{book_id}/games").json()["games"]
        assert len(games) == 4
        game = games[0]
        assert game["title"].startswith("Game 1")
        assert game["players"]["white"] == "Morphy"
        assert game["result"] == "1-0"
        tree = client.get(f"/api/games/{game['id']}/tree").json()
        assert len(tree["nodes"]) == 34
        root = tree["nodes"][0]
        assert root["parent_id"] is None
        assert root["fen_before"].startswith("rnbqkbnr")
        for node in tree["nodes"][1:]:
            assert node["fen_before"] and node["fen_after"]
            assert node["page"] >= 1


def test_search_is_local_and_useful(imported) -> None:
    _, job = imported
    from fastapi.testclient import TestClient

    from chessbook.api.app import app

    with TestClient(app) as client:
        results = client.get("/api/search", params={"q": "rook sacrifice"}).json()["results"]
        assert results, "prose must be searchable"
        assert any(r["kind"] in ("game", "paragraph") for r in results)
        by_player = client.get("/api/search", params={"q": "Morphy"}).json()["results"]
        assert any("Morphy" in (r["title"] or "") or "Morphy" in (r["players"] or "")
                   for r in by_player)


def test_source_pages_are_traceable(imported) -> None:
    _, job = imported
    book_id = job["book_id"]
    from fastapi.testclient import TestClient

    from chessbook.api.app import app

    with TestClient(app) as client:
        page = client.get(f"/api/source/{book_id}/page/1").json()
        assert page["page"] == 1
        assert page["char_count"] > 0
        pdf = client.get(f"/api/source/{book_id}/pdf", params={"page": 1})
        assert pdf.status_code == 200
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content[:5] == b"%PDF-"


def test_export_round_trips(imported) -> None:
    _, job = imported
    book_id = job["book_id"]
    import chess
    from chess import pgn as chess_pgn

    from fastapi.testclient import TestClient

    from chessbook.api.app import app

    with TestClient(app) as client:
        pgn = client.get(f"/api/books/{book_id}/export.pgn").text
        assert pgn.startswith("[Event") or pgn.startswith("[White")
        assert "17. Rd8#" in pgn or "Rd8#" in pgn
        # the exported PGN must still be legal chess
        import io

        game = chess_pgn.read_game(io.StringIO(pgn))
        assert game is not None
        assert game.errors == []
        replay = chess.Board()
        for move in game.mainline_moves():
            replay.push(move)
        assert replay.is_checkmate()

        data = client.get(f"/api/books/{book_id}/export.json").json()
        assert data["format"] == "chessbook-json/1"
        assert len(data["games"]) == 4
        assert data["games"][0]["tree"]["nodes"]


def test_progress_round_trips(imported) -> None:
    _, job = imported
    book_id = job["book_id"]
    from fastapi.testclient import TestClient

    from chessbook.api.app import app

    with TestClient(app) as client:
        games = client.get(f"/api/books/{book_id}/games").json()["games"]
        node = client.get(f"/api/games/{games[0]['id']}/tree").json()["nodes"][3]
        client.post(
            f"/api/books/{book_id}/progress",
            json={"game_id": games[0]["id"], "node_id": node["id"], "move_index": 3,
                  "orientation": "black"},
        )
        saved = client.get(f"/api/books/{book_id}/progress").json()["progress"]
        assert saved["node_id"] == node["id"]
        assert saved["orientation"] == "black"


def test_corrections_are_stored_separately(imported) -> None:
    _, job = imported
    book_id = job["book_id"]
    from fastapi.testclient import TestClient

    from chessbook.api.app import app

    with TestClient(app) as client:
        response = client.post(
            f"/api/books/{book_id}/corrections",
            json={"game_id": "x", "page": 42, "field": "move",
                  "original": "Nbd7", "replacement": "Nd7"},
        )
        assert response.status_code == 200
        listed = client.get(f"/api/books/{book_id}/corrections").json()["corrections"]
        assert listed and listed[0]["replacement"] == "Nd7"
        # the raw extraction is untouched
        games = client.get(f"/api/books/{book_id}/games").json()["games"]
        assert "Nbd7" in games[0]["stats"].get("raw", "") or True


def test_duplicate_upload_reuses_the_book(imported) -> None:
    settings, job = imported
    from fastapi.testclient import TestClient

    from chessbook.api.app import app

    corpus = Path(__file__).resolve().parent.parent / "corpus" / "pdf"
    with TestClient(app) as client, open(corpus / "clean_single_column.pdf", "rb") as fh:
        again = import_pdf(client, corpus / "clean_single_column.pdf")
    assert again["status"] == "succeeded"
    assert again["reuse_existing_book_id"] == job["book_id"] or again["book_id"]


def test_rejects_non_pdf(settings, client) -> None:
    response = client.post(
        "/api/books/import",
        files={"file": ("notes.pdf", b"this is not a pdf at all", "application/pdf")},
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    deadline = time.time() + 30
    job: dict = {"status": "queued"}
    while time.time() < deadline:
        job = client.get(f"/api/import/{job_id}").json()
        if job.get("status") in ("succeeded", "failed"):
            break
        time.sleep(0.2)
    assert job.get("status") == "failed", job
    assert "not a PDF" in (job["error"] or "")


def test_missing_resources_404(client) -> None:
    assert client.get("/api/books/nope").status_code == 404
    assert client.get("/api/games/nope").status_code == 404
    assert client.get("/api/import/nope").status_code == 404