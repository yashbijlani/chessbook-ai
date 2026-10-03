# Chessbook AI

Turn a printed chess book (PDF) into an interactive chessbook you can read, search and
navigate move by move — entirely on your own machine.

**No cloud, no API keys, no account.** Every stage (extraction, chess validation, search,
diagrams, commentary) is local. If you delete the internet connection nothing breaks.

---

## What it does

1. **Reads a PDF** — text layer first, local Tesseract OCR for scans.
2. **Understands the page** — columns, running heads, page numbers, paragraph reflow,
   indented side lines, figures.
3. **Finds the games** — clusters move runs into games, splits a page into several games
   when the author interleaves them.
4. **Resolves the chess** — SAN is never trusted. Every token is resolved against a real
   board: legality, disambiguation, castling spelling, promotion, figurines (`Nc3`,
   `♘c3`), and repairs are recorded rather than silently applied.
5. **Builds a move tree** — main line plus side lines, each node carrying UCI,
   `fen_before`/`fen_after`, source page and bounding box.
6. **Attaches the commentary** — prose is bound to the move it explains.
7. **Validates** — every game is replayed move by move; anything that does not add up is
   reported as an issue with its source location, never quietly "fixed".
8. **Serves a reader** — an interactive board, keyboard navigation, variations, commentary,
   source PDF overlay, full-text search, and PGN/JSON export.

## Quick start

```bash
# one-time setup
python3 -m venv backend/.venv
backend/.venv/bin/pip install -e backend
backend/.venv/bin/pip install pytesseract          # optional: OCR for scanned pages
npm install

# run it (API on :8000, UI on :5173 with /api proxied)
./scripts/dev.sh
```

Open <http://127.0.0.1:5173> and drop in a PDF.

Already have a book? `corpus/pdf/` holds 11 generated test books:

```bash
backend/.venv/bin/python corpus/generate.py          # rebuild the PDFs
backend/.venv/bin/python corpus/run_pipeline.py       # parse them and print a report
```

## Using it without the UI

```bash
export PYTHONPATH=$PWD/backend
backend/.venv/bin/python -m uvicorn chessbook.api.app:app --port 8000

curl -F "file=@corpus/pdf/variations.pdf" http://127.0.0.1:8000/api/books/import
curl http://127.0.0.1:8000/api/import/<job_id>          # live stage progress
curl http://127.0.0.1:8000/api/books
curl http://127.0.0.1:8000/api/games/<game_id>/tree
curl http://127.0.0.1:8000/api/games/<game_id>/export.pgn
curl "http://127.0.0.1:8000/api/search?q=queen"
```

## Tests

```bash
cd backend && PYTHONPATH=$PWD .venv/bin/python -m pytest ../tests/ -q   # 127 tests
npm run build                                                             # frontend
```

The suite includes property tests that feed random legal games and random junk tokens
through the notation layer: the parser must never raise, must never invent an illegal
move, and must never claim a null move is a move.

## Layout

```
backend/chessbook/
  pipeline/
    extract.py     PDF text + images (PyMuPDF), OCR fallback
    layout.py      columns, reading order, running heads, paragraph reflow
    notation.py    SAN resolution, figurines, disambiguation, repairs
    tree.py        move-tree construction, variations, game recovery
    games.py       game region detection and clustering
    chapters.py    TOC + heading reconciliation, page offsets
    commentary.py  prose <-> move association
    diagrams.py    board detection
    validate.py    legality replay + issue reporting
    build.py       orchestration
  export/pgn.py     PGN writer (main line, side lines, comments)
  repository.py     SQLite (FTS5) + on-disk layout
  jobs.py           background imports with live stage progress
  api/app.py       FastAPI routes (all local)
  interfaces.py    provider seams: explainer, diagram recogniser, engine
frontend/src/
  pages/            LibraryPage, BookPage, ReaderPage, SearchPage
  components/Board  custom SVG board, no chess lib
  lib/              api client, tree navigation, chess helpers
corpus/             synthetic book generator + fixtures
tests/              unit, corpus, API and property tests
```

## Design rules

- **The board decides, the page proposes.** Text is evidence, never truth. A move is only
  accepted if it is legal in the position the book has actually reached.
- **Nothing is repaired in silence.** Fixes are typed (`san_relaxed_spelling`,
  `castling_normalised`, …), carry the original spelling, and surface in the issues report.
- **Every node is traceable.** UCI, FEN before and after, source page, bounding box,
  confidence.
- **Providers are seams, not dependencies.** The explainer, diagram recogniser and engine
  are interfaces with a deterministic/no-op default. Wire up a local model later without
  touching the pipeline.
- **Offline by construction.** `GET /api/health` reports `offline: true` and lists any
  cloud credentials it found but did not use.

## Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `CHESSBOOK_DATA_DIR` | `./data` | Library root (SQLite + original PDFs) |
| `pdf_dpi` | 200 | Rasterisation DPI for OCR and diagrams |
| `ocr_enabled` | on | Use Tesseract when a page has no text layer |
| `stockfish_path` | unset | Optional local engine; never required |

## Known limits

- python-chess's PGN *reader* cannot follow a side line that opens with the other side
  (the shape printed books use). Our exporter writes the standard placement — variation
  right after the move it branches from — which lichess and ChessBase read correctly.
- OCR on scanned pages is slow (tens of seconds per page) and noisy; issues are reported
  rather than papered over.