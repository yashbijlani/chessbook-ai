"""FastAPI application (charter #36).

Local-only.  No outbound network calls anywhere in this package.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

from .. import interfaces, repository
from ..config import get_settings
from ..db import init_db
from ..export.pgn import book_to_pgn, game_to_pgn
from ..jobs import get_manager
from ..storage import book_paths, delete_book_dir, new_id

log = logging.getLogger(__name__)

app = FastAPI(
    title="Chessbook AI",
    version="0.1.0",
    description=(
        "Local-first PDF -> interactive chessbook converter. "
        "Runs entirely offline; no API keys, no cloud services, no telemetry."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    settings = get_settings()
    settings.ensure_dirs()
    init_db()
    get_manager()


@app.get("/api/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "data_dir": str(settings.data_dir),
        "providers": interfaces.PROVIDERS.describe(),
        # Names only, never values -- and functionality never reads them (#3).
        "cloud_credentials_present_but_unused": settings.present_cloud_credentials(),
        "offline": True,
    }


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------


@app.get("/api/books")
def get_books() -> dict[str, Any]:
    return {"books": repository.list_books()}


@app.get("/api/books/{book_id}")
def get_book(book_id: str) -> dict[str, Any]:
    book = repository.load_book(book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    book["chapters"] = repository.load_chapters(book_id)
    book["issues_summary"] = {
        "errors": sum(1 for i in repository.load_issues(book_id, "error")),
        "warnings": sum(1 for i in repository.load_issues(book_id, "warning")),
    }
    book["diagrams"] = repository.load_diagrams(book_id)
    book["progress"] = repository.load_progress(book_id)
    return book


@app.delete("/api/books/{book_id}")
def remove_book(book_id: str) -> dict[str, Any]:
    if repository.load_book(book_id) is None:
        raise HTTPException(404, "book not found")
    repository.delete_book(book_id)
    delete_book_dir(book_id)
    return {"deleted": book_id}


@app.get("/api/books/{book_id}/chapters")
def get_chapters(book_id: str) -> dict[str, Any]:
    if repository.load_book(book_id) is None:
        raise HTTPException(404, "book not found")
    return {"chapters": repository.load_chapters(book_id)}


@app.get("/api/books/{book_id}/games")
def get_games(book_id: str, chapter_id: str | None = None) -> dict[str, Any]:
    if repository.load_book(book_id) is None:
        raise HTTPException(404, "book not found")
    return {"games": repository.list_games(book_id, chapter_id)}


# ---------------------------------------------------------------------------
# Import (charter #32)
# ---------------------------------------------------------------------------


@app.post("/api/books/import")
async def import_book(file: UploadFile = File(...)) -> dict[str, Any]:
    settings = get_settings()
    tmp_dir = settings.tmp_dir / new_id("up")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    target = tmp_dir / "upload.pdf"
    written = 0
    try:
        with open(target, "wb") as fh:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    raise HTTPException(
                        413,
                        f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MiB limit",
                    )
                fh.write(chunk)
        if written == 0:
            raise HTTPException(400, "empty upload")
    except HTTPException:
        _cleanup(tmp_dir)
        raise

    job = get_manager().submit(target, file.filename or "book.pdf")
    return {"job_id": job.id, "status": job.status, "stages": [s.model_dump() for s in job.stages]}


@app.get("/api/import/{job_id}")
def import_status(job_id: str) -> dict[str, Any]:
    job = get_manager().get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job.model_dump()


@app.get("/api/import/{job_id}/events")
def import_events(job_id: str, request: Request) -> StreamingResponse:
    """Server-sent events so the UI can show a live pipeline."""

    def stream():
        last = -1.0
        while True:
            job = get_manager().get(job_id)
            if job is None:
                yield "event: error\ndata: {}\n\n"
                return
            if job.progress != last or job.status in ("succeeded", "failed"):
                last = job.progress
                yield f"data: {json.dumps(job.model_dump())}\n\n"
            if job.status in ("succeeded", "failed", "cancelled"):
                return

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/import/{job_id}/issues")
def import_issues(job_id: str) -> dict[str, Any]:
    return {"issues": get_manager().issues(job_id)}


# ---------------------------------------------------------------------------
# Games
# ---------------------------------------------------------------------------


@app.get("/api/games/{game_id}")
def get_game(game_id: str) -> dict[str, Any]:
    game = repository.load_game(game_id)
    if game is None:
        raise HTTPException(404, "game not found")
    return {k: v for k, v in game.items() if v is not None}


@app.get("/api/games/{game_id}/tree")
def get_tree(game_id: str) -> dict[str, Any]:
    if repository.load_game(game_id) is None:
        raise HTTPException(404, "game not found")
    return repository.load_tree(game_id)


@app.get("/api/games/{game_id}/diagrams")
def get_game_diagrams(game_id: str) -> dict[str, Any]:
    game = repository.load_game(game_id)
    if game is None:
        raise HTTPException(404, "game not found")
    book_id = game["book_id"]
    return {"diagrams": repository.load_diagrams(book_id, game_id)}


@app.get("/api/games/{game_id}/issues")
def get_game_issues(game_id: str) -> dict[str, Any]:
    game = repository.load_game(game_id)
    if game is None:
        raise HTTPException(404, "game not found")
    return {"issues": repository.load_issues(game["book_id"], limit=1000)}


# ---------------------------------------------------------------------------
# Diagrams, source pages, debug
# ---------------------------------------------------------------------------


@app.get("/api/books/{book_id}/diagrams")
def get_diagrams(book_id: str) -> dict[str, Any]:
    return {"diagrams": repository.load_diagrams(book_id)}


@app.get("/api/source/{book_id}/page/{page}")
def source_page(book_id: str, page: int) -> dict[str, Any]:
    book = repository.load_book(book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    from ..db import connect

    rec = connect().execute(
        "SELECT page, width, height, text, char_count, has_text FROM source_pages"
        " WHERE book_id = ? AND page = ?",
        (book_id, page),
    ).fetchone()
    if rec is None:
        raise HTTPException(404, "page not found")
    data = dict(rec)
    data["pdf_url"] = f"/api/source/{book_id}/pdf?page={page}"
    return data


@app.get("/api/source/{book_id}/pdf")
def source_pdf(book_id: str, page: int | None = None, download: bool = False):
    paths = book_paths(book_id)
    if not paths["original"].exists():
        raise HTTPException(404, "original PDF not found")
    headers = {}
    if page:
        headers["X-Chessbook-Page"] = str(page)
    return FileResponse(
        paths["original"],
        media_type="application/pdf",
        filename=None if not download else paths["original"].name,
        headers=headers,
    )


@app.get("/api/books/{book_id}/debug/paragraphs")
def debug_paragraphs(book_id: str) -> dict[str, Any]:
    from ..storage import read_json

    paths = book_paths(book_id)
    data = read_json(paths["debug"] / "paragraphs.json", default=[])
    return {"paragraphs": data}


@app.get("/api/books/{book_id}/debug/layout")
def debug_layout(book_id: str) -> dict[str, Any]:
    from ..storage import read_json

    paths = book_paths(book_id)
    return read_json(paths["layout"], default={})


@app.get("/api/books/{book_id}/issues")
def book_issues(book_id: str, severity: str | None = None) -> dict[str, Any]:
    return {"issues": repository.load_issues(book_id, severity)}


# ---------------------------------------------------------------------------
# Search, progress, corrections, export
# ---------------------------------------------------------------------------


@app.get("/api/search")
def search(
    q: str = Query(min_length=1, max_length=200),
    book_id: str | None = None,
    limit: int = Query(40, ge=1, le=200),
) -> dict[str, Any]:
    return {"query": q, "results": repository.search(book_id, q, limit)}


@app.post("/api/books/{book_id}/progress")
def save_progress(book_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    repository.save_progress(
        book_id,
        game_id=payload.get("game_id"),
        node_id=payload.get("node_id"),
        chapter_id=payload.get("chapter_id"),
        move_index=int(payload.get("move_index", 0)),
        orientation=payload.get("orientation", "white"),
        scope=payload.get("scope", "reader"),
    )
    return {"saved": True}


@app.get("/api/books/{book_id}/progress")
def get_progress(book_id: str, scope: str = "reader") -> dict[str, Any]:
    return {"progress": repository.load_progress(book_id, scope)}


@app.post("/api/books/{book_id}/corrections")
def add_correction(book_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    cid = repository.save_correction(
        book_id,
        payload.get("game_id"),
        payload.get("node_id"),
        payload.get("page"),
        payload.get("field", "move"),
        payload.get("original"),
        payload.get("replacement"),
    )
    return {"id": cid}


@app.get("/api/books/{book_id}/corrections")
def get_corrections(book_id: str) -> dict[str, Any]:
    return {"corrections": repository.list_corrections(book_id)}


@app.get("/api/books/{book_id}/export.pgn")
def export_pgn(book_id: str) -> PlainTextResponse:
    games = repository.list_games(book_id)
    if not games:
        raise HTTPException(404, "no games to export")
    pairs = [(g, repository.load_tree(g["id"])) for g in games]
    return PlainTextResponse(book_to_pgn(pairs), media_type="application/x-chess-pgn")


@app.get("/api/games/{game_id}/export.pgn")
def export_game_pgn(game_id: str) -> PlainTextResponse:
    game = repository.load_game(game_id)
    if game is None:
        raise HTTPException(404, "game not found")
    return PlainTextResponse(
        game_to_pgn(game, repository.load_tree(game_id)), media_type="application/x-chess-pgn"
    )


@app.get("/api/books/{book_id}/export.json")
def export_json(book_id: str) -> JSONResponse:
    book = repository.load_book(book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    payload = {
        "book": book,
        "chapters": repository.load_chapters(book_id),
        "games": [
            {
                **repository.load_game(g["id"]),
                "tree": repository.load_tree(g["id"]),
            }
            for g in repository.list_games(book_id)
        ],
        "diagrams": repository.load_diagrams(book_id),
        "issues": repository.load_issues(book_id, limit=5000),
        "format": "chessbook-json/1",
    }
    return JSONResponse(payload)


def _cleanup(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


@app.exception_handler(ValueError)
def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


def main() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "chessbook.api.app:app",
        host="127.0.0.1",
        port=8000,
        reload=bool(get_settings().data_dir),
    )


if __name__ == "__main__":  # pragma: no cover
    main()