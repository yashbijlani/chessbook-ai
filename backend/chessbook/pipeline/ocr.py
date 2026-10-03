"""Optional local OCR layer (charter #5 Type D / #40 level 4).

Uses Tesseract *locally* through ``pytesseract`` when it is installed.  No
remote OCR service is ever contacted.  If Tesseract is missing the stage is
reported as ``skipped`` and the import continues at a lower recovery level.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable

from ..config import Settings
from ..models import Span, TextLine
from .extract import RawPage, normalize_text

log = logging.getLogger(__name__)


def tesseract_available() -> bool:
    if shutil.which("tesseract") is None:
        try:
            import pytesseract  # noqa: F401
        except Exception:
            return False
    return True


def ocr_document(
    pdf_path,
    pages: list[RawPage],
    settings: Settings,
    progress: Callable[[str, float, str], None] | None = None,
    max_pages: int = 600,
) -> bool:
    """OCR pages that have no usable text layer, in place.

    Returns True when at least one page gained text.
    """
    progress = progress or (lambda *_: None)
    if not settings.ocr_enabled:
        progress("ocr", 1.0, "OCR disabled by settings")
        return False
    try:
        import pytesseract
        from PIL import Image
    except Exception as exc:
        progress("ocr", 1.0, f"OCR unavailable ({exc.__class__.__name__}); continuing without it")
        log.info("pytesseract/Pillow unavailable: %s", exc)
        return False

    import pymupdf

    todo = [p for p in pages if p.text_char_count < 24][:max_pages]
    if not todo:
        return False

    done = 0
    with pymupdf.open(pdf_path) as doc:
        for raw in todo:
            try:
                page = doc.load_page(raw.number - 1)
                zoom = settings.pdf_dpi / 72.0
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                # --psm 6 ("one uniform block of text") is dramatically better
                # than the default page-segmentation mode on book pages, which
                # are exactly that.
                data = pytesseract.image_to_data(
                    img,
                    lang=settings.ocr_language,
                    config="--psm 6 -c preserve_interword_spaces=1",
                    output_type=pytesseract.Output.DICT,
                )
                lines = _words_to_lines(data, raw, 1.0 / zoom)
                if lines:
                    raw.lines = lines
                    raw.text_char_count = sum(len(ln.text) for ln in lines)
            except Exception as exc:  # pragma: no cover - OCR is best effort
                log.warning("OCR failed on page %s: %s", raw.number, exc)
            done += 1
            progress("ocr", done / max(1, len(todo)), f"OCR page {raw.number}")
    return True


def _words_to_lines(data: dict, raw: RawPage, scale: float) -> list[TextLine]:
    buckets: dict[tuple[int, int, int], list[dict]] = {}
    n = len(data.get("text", []))
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 30:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        buckets.setdefault(key, []).append(
            {
                "text": text,
                "x": data["left"][i] * scale,
                "y": data["top"][i] * scale,
                "w": data["width"][i] * scale,
                "h": data["height"][i] * scale,
            }
        )

    out: list[TextLine] = []
    for words in buckets.values():
        words.sort(key=lambda w: w["x"])
        text = normalize_text(" ".join(w["text"] for w in words))
        if not text:
            continue
        x0 = min(w["x"] for w in words)
        y0 = min(w["y"] for w in words)
        x1 = max(w["x"] + w["w"] for w in words)
        y1 = max(w["y"] + w["h"] for w in words)
        size = max((w["h"] for w in words), default=10.0)
        out.append(
            TextLine(
                page=raw.number,
                text=text,
                bbox=[round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
                font_size=round(size, 2),
                spans=[
                    Span(
                        text=w["text"],
                        bbox=[round(w["x"], 2), round(w["y"], 2),
                              round(w["x"] + w["w"], 2), round(w["y"] + w["h"], 2)],
                        font="ocr",
                        size=round(w["h"], 2),
                    )
                    for w in words
                ],
                source_text=text,
            )
        )
    out.sort(key=lambda l: (round(l.bbox[1], 1), l.bbox[0]))
    return out