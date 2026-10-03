#!/usr/bin/env python3
"""Run the whole pipeline over the generated corpus and print a report.

    python corpus/inspect.py [name ...]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from chessbook.config import Settings  # noqa: E402
from chessbook.pipeline.build import process_pdf  # noqa: E402

ROOT = Path(__file__).resolve().parent
PDF_DIR = ROOT / "pdf"


def dump_paragraphs(out) -> None:
    print("  --- paragraphs ---")
    for i, p in enumerate(out.paragraphs):
        print(
            f"   {i:3} p{p.page} col{p.column} {p.kind:9} lvl{p.level} "
            f"x={p.bbox[0]:6.1f} y={p.bbox[1]:6.1f} :: {p.text[:96]}"
        )


def report(path: Path, verbose: bool = True, dump: bool = False) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(data_dir=Path(tmp), ocr_enabled=True)
        out = process_pdf(path, "bk1", path.stem.replace("_", " ").title(), settings)

    m = out.book.metrics
    print(f"\n{'=' * 78}\n{path.name}  ->  {out.book.title}")
    print(
        f"  pages={m.pages} games={m.games} moves={m.moves} variations={m.variations} "
        f"nodes={m.nodes} comments={m.comments} diagrams={m.board_diagrams}/{m.diagrams} "
        f"chapters={m.chapters} issues={m.issues}(E{m.errors}/W{m.warnings}) "
        f"text_layer={out.book.has_text_layer} ocr_pages={m.ocr_pages}"
    )
    if dump:
        dump_paragraphs(out)
    modes = out.book.extraction_report.get("layout_modes", {})
    print(f"  layout: {modes}  offset={out.book.extraction_report.get('printed_page_offset')}")

    for game in out.games:
        status = game.parse_status
        line = (
            f"  [{status:7}] {game.title[:44]:46} ply={game.ply_count:3} "
            f"var={game.variation_count:2} cmt={game.comment_count:2} "
            f"conf={game.confidence:.2f} p{game.start_page}-{game.end_page}"
        )
        print(line)
        print(
            f"            W={game.players['white']} B={game.players['black']} "
            f"res={game.result} eco={game.eco} via {game.detection_heuristic[:40]}"
        )
        if verbose:
            ml = [n for n in game.tree if n.is_mainline and n.parent_id]
            print("            " + " ".join(
                (f"{n.move_number}{'.' if n.side == 'w' else '...'}{n.san}") for n in ml[:18]
            ) + (" ..." if len(ml) > 18 else ""))
            for n in game.tree:
                if not n.is_mainline and n.parent_id:
                    print(f"            VAR {n.move_number}{'.' if n.side=='w' else '...'}"
                          f"{n.san} [{n.started_by}]")

    if verbose:
        errs = [i for i in out.issues if i.severity.value in ("error", "warning")]
        for i in errs[:14]:
            page = f"p{i.page}" if i.page else "  "
            game = i.game_id or "-"
            print(f"    {i.severity.value:7} {page:6} {i.code:24} {i.message[:96]}")
        if len(errs) > 14:
            print(f"    ... and {len(errs) - 14} more")

    boards = [d for d in out.diagrams if d.is_board_candidate]
    if boards:
        print("  diagrams:")
        for d in boards:
            print(
                f"    p{d.page} score={d.board_score:.2f} conf={d.position_confidence.value:15} "
                f"method={d.position_method} fen={(d.fen or '-')[:34]} grid={d.grid_detected}"
            )
    return {
        "name": path.name,
        "games": m.games,
        "moves": m.moves,
        "errors": m.errors,
        "warnings": m.warnings,
    }


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--dump"]
    dump = "--dump" in sys.argv[1:]
    names = args
    paths = [PDF_DIR / n for n in names] if names else sorted(PDF_DIR.glob("*.pdf"))
    rows = []
    for p in paths:
        if not p.exists():
            print(f"missing {p}")
            continue
        rows.append(report(p, dump=dump))
    if len(rows) > 1:
        print(f"\n{'=' * 78}\nSUMMARY")
        print(f"{'file':34} {'games':>6} {'moves':>7} {'errors':>7} {'warn':>6}")
        for r in rows:
            print(f"{r['name']:34} {r['games']:6} {r['moves']:7} {r['errors']:7} {r['warnings']:6}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())