"""Pipeline orchestrator: PDF in, Chessbook out.

Stage order is fixed and every stage is inspectable:

    pages -> layout -> paragraphs -> chapters -> games -> trees
          -> commentary -> diagrams -> validation -> metrics
"""

from __future__ import annotations

import logging
import re
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..config import Settings
from ..models import (
    Book,
    BookMetrics,
    Chapter,
    Comment,
    Diagram,
    Game,
    GameReport,
    Node,
    ParsedMove,
    Section,
    Severity,
    TocEntry,
    ValidationIssue,
)
from ..models import PositionConfidence
from . import chapters as chapters_mod
from . import commentary as commentary_mod
from . import diagrams as diagrams_mod
from . import games as games_mod
from . import validate as validate_mod
from .extract import ExtractionResult, extract_document
from .layout import LaidOutPage, Paragraph, analyze_pages, merge_paragraphs
from .notation import START_FEN_FALLBACK
from .tree import Segment, TreeResult, build_tree

log = logging.getLogger(__name__)

ProgressCb = Callable[[str, float, str], None]


@dataclass
class BuildOutput:
    book: Book
    chapters: list[Chapter] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    games: list[Game] = field(default_factory=list)
    diagrams: list[Diagram] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    reports: list[GameReport] = field(default_factory=list)
    extraction: ExtractionResult | None = None
    layout: list[LaidOutPage] = field(default_factory=list)
    paragraphs: list[Paragraph] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)


def process_pdf(
    pdf_path: Path,
    book_id: str,
    title: str,
    settings: Settings,
    progress: ProgressCb | None = None,
) -> BuildOutput:
    import time

    progress = progress or (lambda *_: None)
    timings: dict[str, float] = {}

    def tick(name: str, value: float, detail: str = "") -> None:
        progress(name, value, detail)

    # --- 1. extraction ---------------------------------------------------
    t0 = time.time()
    tick("pages", 0.05, "opening document")
    extraction = extract_document(pdf_path, settings, progress=lambda k, v, d: tick("text", v, d))
    timings["extract"] = time.time() - t0
    tick("pages", 1.0, f"{len(extraction.pages)} pages")
    tick("chess", 1.0, f"{sum(len(p.lines) for p in extraction.pages)} text lines scanned")

    # --- 2. layout -------------------------------------------------------
    t0 = time.time()
    laid_out = analyze_pages(extraction.pages)
    timings["layout"] = time.time() - t0
    tick("layout", 0.9, f"{len(laid_out)} pages analysed")

    paragraphs: list[Paragraph] = []
    page_paragraphs: dict[int, list[Paragraph]] = {}
    para_offset: dict[int, int] = {}
    for lp in laid_out:
        paras = merge_paragraphs(lp)
        para_offset[lp.page] = len(paragraphs)
        page_paragraphs[lp.page] = paras
        paragraphs.extend(paras)
    timings["paragraphs"] = time.time() - t0

    body_sizes = [ln.font_size for lp in laid_out for ln in lp.ordered if ln.role == "body"]
    body_size = statistics.median(body_sizes) if body_sizes else 10.0

    # --- 3. chapters -----------------------------------------------------
    t0 = time.time()
    offset = chapters_mod.estimate_page_offset(laid_out)
    toc = chapters_mod.detect_toc(page_paragraphs, max_page=min(30, len(laid_out)))
    chapters_mod.reconcile_toc_with_pages(toc, offset)
    struct = chapters_mod.detect_structure(laid_out, paragraphs, body_size)
    timings["chapters"] = time.time() - t0
    tick("chapters", 0.95, f"{len(struct)} headings, {len(toc)} TOC entries")

    # --- 4. games --------------------------------------------------------
    t0 = time.time()
    detection = games_mod.detect_games(paragraphs)
    timings["detect_games"] = time.time() - t0
    tick("games", 0.5, f"{len(detection.regions)} candidate games")

    # --- 5. trees + commentary ------------------------------------------
    t0 = time.time()
    games: list[Game] = []
    reports: list[GameReport] = []
    issues: list[ValidationIssue] = []
    indent_base = _indent_bases(paragraphs)

    for n, region in enumerate(detection.regions):
        segs = _segments_for(paragraphs, region, indent_base)
        game_id = f"{book_id}_g{n + 1}"
        result = build_tree(segs, game_id=game_id, root_fen=START_FEN_FALLBACK)
        _post_process_commentary(result, paragraphs)
        game = _assemble_game(
            game_id=game_id,
            index=n,
            region=region,
            result=result,
            chapter_id=_chapter_for(struct, region.start),
            paragraphs=paragraphs,
        )
        report = validate_mod.validate_game(game_id, START_FEN_FALLBACK, result, game.result)
        games.append(game)
        reports.append(report)
        issues.extend(report.issues)
        tick("commentary", (n + 1) / max(1, len(detection.regions)), f"game {n + 1}/{len(detection.regions)}")
        tick("validate", (n + 1) / max(1, len(detection.regions)), f"{len(report.issues)} issues")
        progress(
            "tree",
            (n + 1) / max(1, len(detection.regions)),
            f"game {n + 1}/{len(detection.regions)}: {game.stats.get('valid', 0)} moves",
        )
    timings["trees"] = time.time() - t0
    tick("tree", 0.98, "move trees reconstructed")

    # --- 6. diagrams -----------------------------------------------------
    t0 = time.time()
    captions = _caption_index(paragraphs)
    diagram_list = diagrams_mod.detect_diagrams(
        extraction.pages, captions, progress=lambda k, v, d: tick("diagrams", v * 0.9, d)
    )
    _attach_diagram_positions(diagram_list, games)
    issues.extend(validate_mod.validate_diagrams(diagram_list, book_id))
    timings["diagrams"] = time.time() - t0

    # --- 7. assemble -----------------------------------------------------
    chapter_models = _chapter_models(book_id, struct, games)
    metrics = _metrics(extraction, games, chapter_models, diagram_list, issues, len(paragraphs))
    book = Book(
        id=book_id,
        title=title,
        authors=[],
        source_filename=pdf_path.name,
        content_hash="",
        page_count=extraction.page_count,
        created_at=datetime.now(UTC).isoformat(),
        updated_at=datetime.now(UTC).isoformat(),
        status="ready",
        has_text_layer=extraction.has_text_layer,
        metrics=metrics,
        toc=toc,
        extraction_report={
            "has_text_layer": extraction.has_text_layer,
            "ocr_pages": extraction.ocr_pages,
            "printed_page_offset": offset,
            "pdf_metadata": extraction.metadata,
            "body_font_size": body_size,
            "pages_analyzed": len(laid_out),
            "paragraphs": len(paragraphs),
            "recovery_level": metrics.recovery_level,
            "timings": timings,
            "layout_modes": {
                "single": sum(1 for p in laid_out if p.mode == "single"),
                "columns": sum(1 for p in laid_out if p.mode == "columns"),
                "parallel": sum(1 for p in laid_out if p.mode == "parallel"),
            },
        },
    )

    return BuildOutput(
        book=book,
        chapters=chapter_models,
        games=games,
        diagrams=diagram_list,
        issues=issues,
        reports=reports,
        extraction=extraction,
        layout=laid_out,
        paragraphs=paragraphs,
        timings=timings,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _indent_bases(paragraphs: list[Paragraph]) -> dict[int, float]:
    """Modal left edge of move runs, per page (indentation baseline)."""
    per_page: dict[int, list[float]] = {}
    for p in paragraphs:
        if p.kind == "move_run":
            per_page.setdefault(p.page, []).append(p.bbox[0])
    return {page: statistics.median(vals) for page, vals in per_page.items() if vals}


def _segments_for(
    paragraphs: list[Paragraph], region: games_mod.GameRegion, indent_base: dict[int, float]
) -> list[Segment]:
    segs: list[Segment] = []
    for i in range(region.start, min(region.end, len(paragraphs))):
        para = paragraphs[i]
        base = indent_base.get(para.page, para.bbox[0])
        segs.append(
            Segment(
                index=i,
                page=para.page,
                text=para.text,
                bbox=para.bbox,
                kind=para.kind,
                indent=max(0.0, para.bbox[0] - base),
                column=para.column,
                font_size=0.0,
                is_heading=(para.kind == "heading"),
            )
        )
    return segs


def _chapter_for(struct: list[chapters_mod.StructureNode], seg_index: int) -> str | None:
    best: chapters_mod.StructureNode | None = None
    for node in struct:
        if node.start_seg <= seg_index:
            best = node
        else:
            break
    return best.id if best else None


def _post_process_commentary(result: TreeResult, paragraphs: list[Paragraph]) -> None:
    commentary_mod.link_inline_refs(result, result.inline_refs)
    commentary_mod.attach_sentences_to_nodes(result)
    commentary_mod.apply_parallel_geometry(result, paragraphs)
    commentary_mod.finalise_confidence(result)
    commentary_mod.node_comment_index(result)


def _assemble_game(
    game_id: str,
    index: int,
    region: games_mod.GameRegion,
    result: TreeResult,
    chapter_id: str | None,
    paragraphs: list[Paragraph],
) -> Game:
    mainline = result.mainline()
    nodes = result.nodes
    stats = dict(result.stats)
    stats["nodes"] = len(nodes)
    variation_count = sum(1 for n in nodes if not n.is_mainline and n.parent_id)
    confidence = min(
        region.confidence,
        _tree_confidence(result),
    )
    parse_status = "ok"
    if stats.get("invalid", 0) or stats.get("midgame_start"):
        parse_status = "failed" if not len(mainline) - 1 else "partial"
    elif stats.get("ambiguous", 0) or stats.get("repaired", 0):
        parse_status = "partial" if stats.get("ambiguous") else "ok"

    return Game(
        id=game_id,
        index=index,
        title=region.title or f"Game {index + 1}",
        chapter_id=chapter_id,
        players=dict(region.players),
        event=region.event,
        site=region.site,
        date=region.date,
        round=region.round,
        result=region.result or result.result,
        eco=region.eco,
        opening=region.opening,
        variation_name=region.headers.get("variation"),
        ply_count=max(0, len(mainline) - 1),
        node_count=len(nodes),
        variation_count=variation_count,
        comment_count=len(result.comments),
        start_page=region.start_page,
        end_page=region.end_page,
        source=region.source,
        raw_text="\n".join(paragraphs[i].text for i in range(region.start, min(region.end, len(paragraphs)))),
        headers=region.headers,
        confidence=round(confidence, 3),
        confidence_parts={
            "detection": region.confidence,
            "tree": _tree_confidence(result),
            "heuristic": 1.0 if region.heuristic else 0.0,
        },
        detection_heuristic=region.heuristic,
        parse_status=parse_status,  # type: ignore[arg-type]
        stats=stats,
        tree=nodes,
        comments=result.comments,
        root_fen=START_FEN_FALLBACK,
        final_fen=result.final_fen,
        mainline_san=[n.san or "" for n in mainline[1:]],
    )


def _tree_confidence(result: TreeResult) -> float:
    moves = [n for n in result.nodes if n.parent_id]
    if not moves:
        return 0.0
    return round(statistics.fmean(n.confidence for n in moves), 3)


def _caption_index(paragraphs: list[Paragraph]) -> dict[int, list[tuple[list[float], str]]]:
    out: dict[int, list[tuple[list[float], str]]] = {}
    for p in paragraphs:
        if p.kind == "caption" or re.match(
            r"^(fig(?:ure)?\.?|diagram|position)", p.text.strip(), re.I
        ):
            out.setdefault(p.page, []).append((p.bbox, p.text.strip()))
    return out


def _attach_diagram_positions(diagrams: list[Diagram], games: list[Game]) -> None:
    """Prefer the position implied by the moves above the diagram (#15)."""
    node_points: list[tuple[int, float, Game, Node]] = []
    for game in games:
        for node in game.tree:
            if node.parent_id is None or node.source is None or not node.source.bbox:
                continue
            node_points.append((node.source.page, float(node.source.bbox[3]), game, node))
    node_points.sort(key=lambda t: (t[0], t[1]))

    for diagram in diagrams:
        if not diagram.is_board_candidate:
            continue
        best: tuple[int, float, Game, Node] | None = None
        for point in node_points:
            if point[0] < diagram.page or (
                point[0] == diagram.page and point[1] <= diagram.bbox[1] + 2
            ):
                best = point
            else:
                break
        if best is None:
            continue
        page, _y, game, node = best
        diagram.game_id = game.id
        diagram.node_id = node.id
        diagram.fen = node.fen_after
        same_page = page == diagram.page
        diagram.position_method = (
            "inherited_from_preceding_moves" if same_page else "nearest_preceding_move_run"
        )
        diagram.position_confidence = validate_mod.confidence_for_diagram(
            diagram.fen, diagram.position_method
        )
        if diagram.caption is None and game.title:
            diagram.notes.append("no caption found near the diagram")
        if diagram.id not in game.diagram_ids:
            game.diagram_ids.append(diagram.id)


def _chapter_models(
    book_id: str, struct: list[chapters_mod.StructureNode], games: list[Game]
) -> list[Chapter]:
    out: list[Chapter] = []
    by_para_game = {g.chapter_id: g for g in games}
    for node in struct:
        chapter = Chapter(
            id=f"{book_id}_{node.id}",
            index=len(out),
            parent_id=(f"{book_id}_{node.parent_id}" if node.parent_id else None),
            title=node.title,
            level=node.level,
            kind=node.kind,  # type: ignore[arg-type]
            start_page=node.start_page,
            end_page=node.end_page,
            source=node.source,
            confidence=node.confidence,
        )
        out.append(chapter)
    for chapter in out:
        short = chapter.id.split("_", 1)[1]
        chapter.game_ids = [g.id for g in games if g.chapter_id == short]
    return out


def _metrics(
    extraction: ExtractionResult,
    games: list[Game],
    chapters: list[Chapter],
    diagrams: list[Diagram],
    issues: list[ValidationIssue],
    paragraph_count: int,
) -> BookMetrics:
    moves = sum(g.stats.get("valid", 0) for g in games)
    nodes = sum(g.node_count for g in games)
    variations = sum(g.variation_count for g in games)
    comments = sum(g.comment_count for g in games)
    summary = validate_mod.summarise(issues)
    recovery = 1
    if not extraction.has_text_layer:
        recovery = 4
    elif any(i.code in ("resync", "illegal_in_mainline") for i in issues):
        recovery = 3
    elif any(i.severity == Severity.WARNING for i in issues):
        recovery = 2
    return BookMetrics(
        pages=extraction.page_count,
        chapters=len([c for c in chapters if c.level == 0]),
        sections=len(chapters),
        games=len(games),
        moves=moves,
        variations=variations,
        comments=comments,
        diagrams=len(diagrams),
        board_diagrams=len([d for d in diagrams if d.is_board_candidate]),
        nodes=nodes,
        words=sum(len(g.raw_text.split()) for g in games),
        issues=summary["total"],
        errors=summary["errors"],
        warnings=summary["warnings"],
        ocr_used=bool(extraction.ocr_pages),
        ocr_pages=len(extraction.ocr_pages),
        recovery_level=recovery,
    )