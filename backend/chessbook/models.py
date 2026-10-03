"""Structured internal representation of a Chessbook.

Design rules from the charter:
  * #8  SAN is never the source of truth.  Every node carries FEN before/after
        plus a UCI move, which makes reconstruction deterministic.
  * #9  Games are trees, not move arrays.
  * #13 Every extracted object keeps a source reference (page + bbox).
  * #42 Every important object carries a confidence score.
"""

from __future__ import annotations

import enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


def chess_start_fen() -> str:
    return "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class PositionConfidence(str, enum.Enum):
    EXACT = "EXACT"
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
    PROBABLE = "PROBABLE"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_score(cls, score: float) -> PositionConfidence:
        if score >= 0.999:
            return cls.EXACT
        if score >= 0.9:
            return cls.HIGH_CONFIDENCE
        if score >= 0.65:
            return cls.PROBABLE
        return cls.UNKNOWN


class Severity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class SourceRef(BaseModel):
    """Where in the PDF this came from (#13)."""

    page: int = Field(ge=1, description="1-based PDF page number")
    bbox: list[float] | None = Field(
        default=None, description="[x0, y0, x1, y1] in PDF points"
    )
    page_width: float | None = None
    page_height: float | None = None

    def as_json(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


# --------------------------------------------------------------------------
# Layout layer (output of Phase 2: extraction / reading-order reconstruction)
# --------------------------------------------------------------------------


class Span(BaseModel):
    text: str
    bbox: list[float]
    font: str | None = None
    size: float | None = None
    bold: bool = False
    italic: bool = False


class TextLine(BaseModel):
    page: int
    text: str
    bbox: list[float]
    column: int = 0
    font_size: float = 10.0
    bold: bool = False
    italic: bool = False
    spans: list[Span] = Field(default_factory=list)
    # region classification produced by layout analysis
    role: Literal["body", "heading", "header", "footer", "page_number", "caption"] = "body"
    source_text: str = ""

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]


class Paragraph(BaseModel):
    """A block of reconstructed prose (may interleave with move runs)."""

    index: int
    page: int
    text: str
    bbox: list[float]
    line_indices: list[int] = Field(default_factory=list)
    column: int = 0
    kind: Literal["prose", "heading", "caption", "toc", "header_block", "move_run"] = "prose"
    level: int = 0
    source: SourceRef | None = None


class PageLayout(BaseModel):
    number: int
    width: float
    height: float
    columns: int = 1
    rotation: int = 0
    has_text_layer: bool = True
    text_char_count: int = 0
    image_count: int = 0
    line_count: int = 0


# --------------------------------------------------------------------------
# Chess notation layer
# --------------------------------------------------------------------------


class ParsedMove(BaseModel):
    """A single notation token as found in the text, before legality checks."""

    raw: str
    body: str = Field(default="", description="SAN only, without the move number")
    san: str | None = None
    uci: str | None = None
    move_number: int | None = None
    side: Literal["w", "b"] | None = None
    annotation: str = ""
    is_figurine: bool = False
    is_black_move: bool = False
    source: SourceRef | None = None
    # provenance of any repair attempt (#17 -- never silently "fix" chess)
    repaired: bool = False
    repair_reason: str | None = None
    repair_detail: dict[str, Any] | None = None
    confidence: Confidence = 0.5


class Comment(BaseModel):
    id: str
    text: str
    seg_index: int = -1
    source: SourceRef | None = None
    # #10 association
    attached_to: str | None = None
    confidence: Confidence = 0.0
    signals: dict[str, float] = Field(default_factory=dict)
    anchor_kind: Literal["preceding_move", "following_move", "inline_reference", "position"] = (
        "preceding_move"
    )


class Node(BaseModel):
    """A node in a game's move tree (#9)."""

    id: str
    parent_id: str | None = None
    ply: int = 0
    move_number: int = 1
    side: Literal["w", "b"] = "w"
    san: str | None = None
    lan: str | None = None
    move_uci: str | None = None
    fen_before: str
    fen_after: str
    annotations: list[str] = Field(default_factory=list)
    nags: list[str] = Field(default_factory=list)
    comment_ids: list[str] = Field(default_factory=list)
    source_text: str = ""
    source: SourceRef | None = None
    confidence: Confidence = 0.0
    child_ids: list[str] = Field(default_factory=list)
    mainline_child_id: str | None = None
    # variation bookkeeping
    is_mainline: bool = True
    variation_index: int = 0
    variation_depth: int = 0
    started_by: Literal["mainline", "explicit_number", "repeated_number", "parenthesis", "indent", "resync"] = (
        "mainline"
    )
    # provenance
    issues: list[str] = Field(default_factory=list)
    move_origin: Literal["parsed", "repaired", "inferred", "manual"] = "parsed"

    def add_child(self, child_id: str, mainline: bool = True) -> None:
        if child_id not in self.child_ids:
            self.child_ids.append(child_id)
        if mainline:
            self.mainline_child_id = child_id


class Game(BaseModel):
    id: str
    index: int = 0
    title: str = ""
    chapter_id: str | None = None
    section_id: str | None = None
    players: dict[str, str | None] = Field(default_factory=dict)  # white/black
    event: str | None = None
    site: str | None = None
    date: str | None = None
    round: str | None = None
    result: str | None = None
    eco: str | None = None
    opening: str | None = None
    variation_name: str | None = None
    ply_count: int = 0
    root_fen: str = chess_start_fen()
    final_fen: str = chess_start_fen()
    mainline_san: list[str] = Field(default_factory=list)
    node_count: int = 0
    variation_count: int = 0
    comment_count: int = 0
    diagram_ids: list[str] = Field(default_factory=list)
    start_page: int | None = None
    end_page: int | None = None
    source: SourceRef | None = None
    raw_text: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    confidence: Confidence = 0.0
    confidence_parts: dict[str, float] = Field(default_factory=dict)
    detection_heuristic: str = ""
    parse_status: Literal["ok", "partial", "failed"] = "ok"
    stats: dict[str, int] = Field(default_factory=dict)
    tree: list[Node] = Field(default_factory=list)
    comments: list[Comment] = Field(default_factory=list)

    @property
    def white(self) -> str | None:
        return self.players.get("white")

    @property
    def black(self) -> str | None:
        return self.players.get("black")


class Section(BaseModel):
    id: str
    index: int
    title: str
    parent_id: str | None = None
    level: int = 0
    start_page: int | None = None
    end_page: int | None = None
    source: SourceRef | None = None
    game_ids: list[str] = Field(default_factory=list)
    confidence: Confidence = 0.0
    kind: Literal["chapter", "section", "subsection", "game_group"] = "chapter"


class Chapter(Section):
    kind: Literal["chapter", "section", "subsection", "game_group"] = "chapter"


class Diagram(BaseModel):
    """A detected diagram candidate (#14)."""

    id: str
    page: int
    bbox: list[float]
    width: float
    height: float
    kind: Literal["embedded_image", "vector_drawing", "raster_region", "unknown"] = (
        "embedded_image"
    )
    is_board_candidate: bool = False
    board_score: float = 0.0
    caption: str | None = None
    caption_source: SourceRef | None = None
    game_id: str | None = None
    node_id: str | None = None
    fen: str | None = None
    position_confidence: PositionConfidence = PositionConfidence.UNKNOWN
    position_method: str | None = None
    grid_detected: dict[str, Any] = Field(default_factory=dict)
    image_path: str | None = None
    confidence: Confidence = 0.0
    notes: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    id: str
    severity: Severity = Severity.WARNING
    code: str
    message: str
    page: int | None = None
    game_id: str | None = None
    node_id: str | None = None
    source_text: str | None = None
    source: SourceRef | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    resolved_by: str | None = None


class GameReport(BaseModel):
    game_id: str
    title: str = ""
    moves_detected: int = 0
    valid: int = 0
    ambiguous: int = 0
    invalid: int = 0
    variations: int = 0
    issues: list[ValidationIssue] = Field(default_factory=list)
    mainline_legal: bool = True
    fen_consistent: bool = True


class TocEntry(BaseModel):
    level: int
    title: str
    page: int | None = None
    target_id: str | None = None


class BookMetrics(BaseModel):
    pages: int = 0
    chapters: int = 0
    sections: int = 0
    games: int = 0
    moves: int = 0
    variations: int = 0
    comments: int = 0
    diagrams: int = 0
    board_diagrams: int = 0
    nodes: int = 0
    words: int = 0
    issues: int = 0
    errors: int = 0
    warnings: int = 0
    ocr_used: bool = False
    ocr_pages: int = 0
    recovery_level: int = 1


class Book(BaseModel):
    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    source_filename: str = ""
    content_hash: str = ""
    page_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    status: Literal["pending", "processing", "ready", "failed"] = "pending"
    metrics: BookMetrics = Field(default_factory=BookMetrics)
    has_text_layer: bool = True
    processor_version: str = "0.1.0"
    extraction_report: dict[str, Any] = Field(default_factory=dict)
    toc: list[TocEntry] = Field(default_factory=list)
    error: str | None = None


class JobStage(BaseModel):
    key: str
    label: str
    status: Literal["pending", "running", "done", "skipped", "failed"] = "pending"
    detail: str = ""
    progress: float = 0.0
    started_at: float | None = None
    ended_at: float | None = None


class ImportJob(BaseModel):
    id: str
    book_id: str | None = None
    filename: str = ""
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"] = "queued"
    progress: float = 0.0
    stages: list[JobStage] = Field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    error: str | None = None
    reuse_existing_book_id: str | None = None
    messages: list[str] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)


STAGE_SEQUENCE: list[tuple[str, str]] = [
    ("upload", "Reading PDF"),
    ("hash", "Hashing content"),
    ("pages", "Detecting pages"),
    ("text", "Extracting text"),
    ("layout", "Detecting document layout"),
    ("ocr", "Local OCR"),
    ("chess", "Detecting chess notation"),
    ("games", "Detecting games"),
    ("chapters", "Detecting chapters"),
    ("tree", "Reconstructing move trees"),
    ("commentary", "Associating commentary"),
    ("diagrams", "Detecting diagrams"),
    ("validate", "Validating games"),
    ("persist", "Saving to library"),
]