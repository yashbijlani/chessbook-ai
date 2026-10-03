"""Stage: PDF -> pages, text lines, images.

Everything here is deterministic and inspectable.  We never trust PyMuPDF's
block ordering (charter #5 Type B) -- we extract geometry and let
:mod:`chessbook.pipeline.layout` rebuild reading order.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

from ..config import Settings
from ..models import PageLayout, SourceRef, Span, TextLine

log = logging.getLogger(__name__)

ProgressCb = Callable[[str, float, str], None]


@dataclass
class ImageInfo:
    page: int
    xref: int
    bbox: list[float]
    width: int
    height: int
    colorspace: str = ""
    bpc: int = 8
    is_mask: bool = False
    smask: int = 0

    @property
    def aspect(self) -> float:
        w = max(1.0, self.bbox[2] - self.bbox[0])
        h = max(1.0, self.bbox[3] - self.bbox[1])
        return w / h


@dataclass
class RawPage:
    number: int
    width: float
    height: float
    rotation: int
    lines: list[TextLine] = field(default_factory=list)
    images: list[ImageInfo] = field(default_factory=list)
    drawings: list[dict[str, Any]] = field(default_factory=list)
    text_char_count: int = 0


@dataclass
class ExtractionResult:
    path: Path
    page_count: int
    pages: list[RawPage]
    has_text_layer: bool
    ocr_pages: list[int]
    metadata: dict[str, Any]
    per_page_text: dict[int, str]

    def layouts(self) -> list[PageLayout]:
        return [
            PageLayout(
                number=p.number,
                width=p.width,
                height=p.height,
                columns=1,
                rotation=p.rotation,
                has_text_layer=p.text_char_count > 0,
                text_char_count=p.text_char_count,
                image_count=len(p.images),
                line_count=len(p.lines),
            )
            for p in self.pages
        ]


_EXTRACT_FLAGS = (
    pymupdf.TEXT_PRESERVE_WHITESPACE
    | pymupdf.TEXT_MEDIABOX_CLIP
    | pymupdf.TEXT_PRESERVE_IMAGES
)

# A page with fewer than this many extractable characters is a strong signal
# that the PDF is a scan (charter #5 Type D).
TEXT_LAYER_CHAR_FLOOR = 24


def extract_document(
    pdf_path: Path,
    settings: Settings,
    progress: ProgressCb | None = None,
) -> ExtractionResult:
    progress = progress or (lambda *_: None)
    doc = pymupdf.open(pdf_path)
    try:
        if doc.needs_pass:
            raise ValueError("PDF is password protected; decryption is not supported")

        pages: list[RawPage] = []
        ocr_pages: list[int] = []
        total = max(1, doc.page_count)
        per_page_text: dict[int, str] = {}
        any_text = False

        for idx in range(doc.page_count):
            page = doc.load_page(idx)
            rect = page.rect
            raw = RawPage(
                number=idx + 1,
                width=float(rect.width),
                height=float(rect.height),
                rotation=int(page.rotation),
            )
            _extract_page(page, raw)

            if raw.text_char_count < TEXT_LAYER_CHAR_FLOOR:
                ocr_pages.append(raw.number)

            progress("text", (idx + 1) / total * 0.9, f"page {idx + 1}/{doc.page_count}")
            pages.append(raw)

        any_text = any(p.text_char_count >= TEXT_LAYER_CHAR_FLOOR for p in pages)

        if not any_text:
            progress("ocr", 0.0, "no text layer detected -- attempting local OCR")
            from . import ocr as ocr_mod

            ok = ocr_mod.ocr_document(pdf_path, pages, settings, progress=progress)
            if ok:
                for p in pages:
                    p.text_char_count = sum(len(ln.text) for ln in p.lines)
                any_text = any(p.text_char_count >= TEXT_LAYER_CHAR_FLOOR for p in pages)

        for p in pages:
            per_page_text[p.number] = "\n".join(ln.text for ln in p.lines)

        meta = {k: v for k, v in (doc.metadata or {}).items() if v}
        return ExtractionResult(
            path=pdf_path,
            page_count=doc.page_count,
            pages=pages,
            has_text_layer=any_text,
            ocr_pages=ocr_pages,
            metadata=meta,
            per_page_text=per_page_text,
        )
    finally:
        doc.close()


def _extract_page(page: pymupdf.Page, raw: RawPage) -> None:
    try:
        data = page.get_text("dict", flags=_EXTRACT_FLAGS, sort=False)
    except Exception as exc:  # pragma: no cover - defensive, corrupt PDFs
        log.warning("text extraction failed on page %s: %s", raw.number, exc)
        return

    for block in data.get("blocks", []):
        btype = block.get("type")
        if btype == 1:  # image block
            raw.images.append(
                ImageInfo(
                    page=raw.number,
                    xref=int(block.get("number", 0)),
                    bbox=[float(v) for v in block.get("bbox", (0, 0, 0, 0))],
                    width=int(block.get("width", 0)),
                    height=int(block.get("height", 0)),
                    colorspace=str(block.get("colorspace", "")),
                    bpc=int(block.get("bpc", 8)),
                )
            )
            continue
        if btype != 0:
            continue

        for line in block.get("lines", []):
            spans_data = line.get("spans", [])
            spans: list[Span] = []
            pieces: list[str] = []
            max_size = 0.0
            bold = italic = False
            for sp in spans_data:
                text = sp.get("text", "")
                if not text:
                    continue
                flags = int(sp.get("flags", 0))
                is_bold = bool(flags & 2 ** 4) or _font_is_bold(sp.get("font", ""))
                is_italic = bool(flags & 2 ** 1) or _font_is_italic(sp.get("font", ""))
                size = float(sp.get("size", 10.0))
                max_size = max(max_size, size)
                bold = bold or is_bold
                italic = italic or is_italic
                pieces.append(text)
                spans.append(
                    Span(
                        text=text,
                        bbox=[float(v) for v in sp.get("bbox", (0, 0, 0, 0))],
                        font=sp.get("font"),
                        size=size,
                        bold=is_bold,
                        italic=is_italic,
                    )
                )
            text = "".join(pieces)
            text = normalize_text(text)
            if not text.strip():
                continue
            bbox = _union_bbox(spans) or [float(v) for v in line.get("bbox", (0, 0, 0, 0))]
            raw.lines.append(
                TextLine(
                    page=raw.number,
                    text=text,
                    bbox=[round(v, 2) for v in bbox],
                    font_size=round(max_size or 10.0, 2),
                    bold=bold,
                    italic=italic,
                    spans=spans,
                    source_text=text,
                )
            )
            raw.text_char_count += len(text.strip())

    _extract_embedded_images(page, raw)
    _extract_vector_drawings(page, raw)


def _extract_embedded_images(page: pymupdf.Page, raw: RawPage) -> None:
    """Raw XObject images (catches boards drawn as tiled bitmaps)."""
    try:
        infos = page.get_image_info(hashes=True)
    except Exception:  # pragma: no cover
        return
    for info in infos or []:
        bbox = [float(v) for v in info.get("bbox", (0, 0, 0, 0))]
        if bbox[2] - bbox[0] <= 1 or bbox[3] - bbox[1] <= 1:
            continue
        raw.images.append(
            ImageInfo(
                page=raw.number,
                xref=int(info.get("xref", 0)),
                bbox=[round(v, 2) for v in bbox],
                width=int(info.get("width", 0)),
                height=int(info.get("height", 0)),
                colorspace=str(info.get("cs-name", "")),
                bpc=int(info.get("bpc", 8)),
                is_mask=bool(info.get("has-mask", False)),
            )
        )


def _extract_vector_drawings(page: pymupdf.Page, raw: RawPage) -> None:
    """Collect filled rectangles: chess diagrams are usually 8x8 rect grids."""
    try:
        drawings = page.get_drawings()
    except Exception:  # pragma: no cover
        return
    for dr in drawings or []:
        rect = dr.get("rect")
        if rect is None:
            continue
        r = [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]
        if r[2] - r[0] < 2 or r[3] - r[1] < 2:
            continue
        raw.drawings.append(
            {
                "rect": [round(v, 2) for v in r],
                "fill": dr.get("fill"),
                "color": dr.get("color"),
                "type": str(dr.get("type", "")),
                "items": [it[0] for it in dr.get("items", [])][:64],
            }
        )


def _union_bbox(spans: list[Span]) -> list[float] | None:
    if not spans:
        return None
    x0 = min(s.bbox[0] for s in spans)
    y0 = min(s.bbox[1] for s in spans)
    x1 = max(s.bbox[2] for s in spans)
    y1 = max(s.bbox[3] for s in spans)
    return [x0, y0, x1, y1]


def _font_is_bold(font: str) -> bool:
    f = font.lower()
    return "bold" in f or "black" in f or "heavy" in f or "semibold" in f


def _font_is_italic(font: str) -> bool:
    f = font.lower()
    return "italic" in f or "oblique" in f


_ZERO_WIDTH = {
    "\u00ad",  # soft hyphen
    "\u200b",
    "\u200c",
    "\u200d",
    "\ufeff",
}


def normalize_text(text: str) -> str:
    """Glyph-level cleanup that must happen before *any* parsing."""
    if not text:
        return ""
    for ch in _ZERO_WIDTH:
        text = text.replace(ch, "")
    text = text.replace("\ufb01", "fi").replace("\ufb02", "fl")
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\u00a0", " ").replace("\u2007", " ").replace("\u202f", " ")
    text = text.replace("\u2212", "-").replace("\u25a0", " ")
    # ligature artefacts from broken encodings
    text = text.replace("\ufb00", "ff").replace("\ufb03", "ffi").replace("\ufb04", "ffl")
    # collapse runs of spaces but keep single separation
    text = re_sub_spaces(text)
    return text.strip()


def re_sub_spaces(text: str) -> str:
    out: list[str] = []
    prev_space = False
    for ch in text:
        if ch == " ":
            if not prev_space:
                out.append(" ")
            prev_space = True
        else:
            out.append(ch)
            prev_space = False
    return "".join(out)


def page_source(page: int, bbox: list[float] | None, page_obj: PageLayout | None = None) -> SourceRef:
    return SourceRef(
        page=page,
        bbox=[round(v, 2) for v in bbox] if bbox else None,
        page_width=page_obj.width if page_obj else None,
        page_height=page_obj.height if page_obj else None,
    )