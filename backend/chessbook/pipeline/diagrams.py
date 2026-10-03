"""Stage: diagram detection (charter #14, #15).

V1 is deterministic.  We do **not** try to read the pieces out of the picture;
instead we answer the two questions we can answer reliably:

  1. *Is this rectangle probably a chessboard?*  (geometry + grid analysis)
  2. *Which position does it show?*  -- almost always inherited from the moves
     printed immediately before it, which is both cheaper and far more accurate
     than any vision heuristic.

The :class:`DiagramRecognizer` interface leaves room for a local vision model
later, but nothing in V1 requires one.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..models import Diagram, PositionConfidence, SourceRef
from .extract import ImageInfo, RawPage

CAPTION_RE = re.compile(
    r"(fig(?:ure)?\.?|diagram|position|chart|diagramme|plan\s|esquema|stellung)",
    re.I,
)
BOARD_WORDS = re.compile(r"\b(position|diagram|board|endgame|position\s+after)\b", re.I)

MIN_BOARD_PT = 42.0
MAX_BOARD_PT = 900.0
IDEAL_ASPECT = 1.0


@dataclass
class GridAnalysis:
    rows: int = 0
    cols: int = 0
    cell: float = 0.0
    coverage: float = 0.0
    checkerboard: float = 0.0

    @property
    def looks_like_board(self) -> bool:
        return self.rows in (8, 7, 6, 5) and self.cols in (8, 7, 6, 5) and self.coverage > 0.5

    def as_json(self) -> dict:
        return {
            "rows": self.rows,
            "cols": self.cols,
            "cell": round(self.cell, 2),
            "coverage": round(self.coverage, 3),
            "checkerboard": round(self.checkerboard, 3),
        }


@dataclass
class Candidate:
    page: int
    bbox: list[float]
    kind: str
    image: ImageInfo | None = None
    rects: list[list[float]] = field(default_factory=list)
    score: float = 0.0
    signals: dict[str, float] = field(default_factory=dict)


def collect_candidates(pages: list[RawPage]) -> list[Candidate]:
    out: list[Candidate] = []
    for page in pages:
        seen_boxes: list[tuple[int, int, int, int]] = []
        for img in page.images:
            key = (
                int(img.bbox[0]),
                int(img.bbox[1]),
                int(img.bbox[2]),
                int(img.bbox[3]),
            )
            if any(_iou(key, k) > 0.85 for k in seen_boxes):
                continue
            seen_boxes.append(key)
            w = img.bbox[2] - img.bbox[0]
            h = img.bbox[3] - img.bbox[1]
            out.append(
                Candidate(
                    page=page.number,
                    bbox=[round(v, 2) for v in img.bbox],
                    kind="embedded_image",
                    image=img,
                )
            )
        for cluster in _cluster_rects(page):
            xs = [r[0] for r in cluster]
            ys = [r[1] for r in cluster]
            xe = [r[2] for r in cluster]
            ye = [r[3] for r in cluster]
            out.append(
                Candidate(
                    page=page.number,
                    bbox=[min(xs), min(ys), max(xe), max(ye)],
                    kind="vector_drawing",
                    rects=cluster,
                )
            )
    return out


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(1, (a[2] - a[0])) * max(1, (a[3] - a[1]))
    area_b = max(1, (b[2] - b[0])) * max(1, (b[3] - b[1]))
    return inter / float(area_a + area_b - inter)


def _cluster_rects(page: RawPage, max_gap: float = 6.0) -> list[list[list[float]]]:
    """Group drawing rectangles that sit next to each other (a board is one)."""
    rects = [d["rect"] for d in page.drawings if _is_squareish(d["rect"])]
    if len(rects) < 12:
        return []
    used = [False] * len(rects)
    clusters: list[list[list[float]]] = []
    for i in range(len(rects)):
        if used[i]:
            continue
        stack = [i]
        used[i] = True
        group: list[list[float]] = []
        while stack:
            j = stack.pop()
            group.append(rects[j])
            for k in range(len(rects)):
                if used[k]:
                    continue
                if _near(rects[j], rects[k], max_gap):
                    used[k] = True
                    stack.append(k)
        if len(group) >= 12:
            clusters.append(group)
    return clusters


def _is_squareish(rect: list[float]) -> bool:
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]
    if w < 6 or h < 6:
        return False
    return abs(w - h) / max(w, h) < 0.25


def _near(a: list[float], b: list[float], gap: float) -> bool:
    return not (
        a[2] + gap < b[0] or b[2] + gap < a[0] or a[3] + gap < b[1] or b[3] + gap < a[1]
    )


def analyse_grid(cand: Candidate) -> GridAnalysis:
    if not cand.rects:
        return GridAnalysis()
    xs = sorted({round(r[0], 1) for r in cand.rects})
    ys = sorted({round(r[1], 1) for r in cand.rects})
    widths = [r[2] - r[0] for r in cand.rects]
    cell = sum(widths) / len(widths)
    tol = max(1.5, cell * 0.25)

    def cluster(values: list[float]) -> list[list[float]]:
        groups: list[list[float]] = [[values[0]]]
        for v in values[1:]:
            if v - groups[-1][-1] <= tol:
                groups[-1].append(v)
            else:
                groups.append([v])
        return groups

    cols = cluster(xs)
    rows = cluster(ys)
    expected = max(1, round((max(xs) + cell - min(xs)) / cell))
    expected_y = max(1, round((max(ys) + cell - min(ys)) / cell))
    cols_n = min(len(cols), expected)
    rows_n = min(len(rows), expected_y)
    coverage = len(cand.rects) / float(max(1, cols_n * rows_n))
    checker = 0.0
    if rows_n >= 4 and cols_n >= 4:
        hits = 0
        total = 0
        for r in cand.rects:
            ri = min(rows_n - 1, int((r[1] - min(ys)) / cell))
            ci = min(cols_n - 1, int((r[0] - min(xs)) / cell))
            total += 1
            if (ri + ci) % 2 == 0:
                hits += 1
        checker = hits / float(total or 1)
    return GridAnalysis(
        rows=rows_n,
        cols=cols_n,
        cell=cell,
        coverage=min(1.0, coverage),
        checkerboard=checker,
    )


def score_candidate(cand: Candidate, captions: dict[int, list[tuple[list[float], str]]]) -> None:
    w = cand.bbox[2] - cand.bbox[0]
    h = cand.bbox[3] - cand.bbox[1]
    signals: dict[str, float] = {}
    score = 0.0

    if MIN_BOARD_PT <= w <= MAX_BOARD_PT and MIN_BOARD_PT <= h <= MAX_BOARD_PT:
        score += 0.12
        signals["size_plausible"] = 1.0
    else:
        signals["size_plausible"] = 0.0

    aspect = w / h if h > 0 else 0.0
    closeness = math.exp(-((math.log(max(aspect, 1e-6)) - math.log(IDEAL_ASPECT)) ** 2) / 0.08)
    score += 0.2 * closeness
    signals["aspect"] = round(closeness, 3)

    grid = analyse_grid(cand)
    if grid.looks_like_board:
        bonus = 0.42 + 0.12 * grid.checkerboard
        score += bonus
        signals["grid"] = round(min(1.0, bonus / 0.54), 3)
    else:
        signals["grid"] = 0.0
        if grid.rows and grid.cols:
            signals["grid"] = 0.15

    cap = _nearest_caption(cand, captions.get(cand.page, []))
    if cap is not None:
        dist = min(abs(c[0][1] - cand.bbox[3]) for c in captions.get(cand.page, []))
        if dist < 40:
            score += 0.08
            signals["caption_nearby"] = 1.0
        elif CAPTION_RE.search(cap):
            score += 0.06
            signals["caption_wording"] = 1.0

    if cand.image is not None and cand.image.colorspace in {"DeviceRGB", "DeviceGray", "DeviceCMYK"}:
        score += 0.03
        signals["raster"] = 1.0

    cand.score = round(min(1.0, score), 3)
    cand.signals = signals
    cand.rects = cand.rects  # keep for grid export


def _nearest_caption(cand: Candidate, captions: list[tuple[list[float], str]]) -> str | None:
    best: str | None = None
    best_d = 1e9
    for bbox, text in captions:
        dy = abs(bbox[1] - cand.bbox[3])
        dy2 = abs(bbox[3] - cand.bbox[1])
        d = min(dy, dy2)
        if d < best_d:
            best_d, best = d, text
    return best


def detect_diagrams(
    pages: list[RawPage],
    captions: dict[int, list[tuple[list[float], str]]],
    progress: Callable[[str, float, str], None] | None = None,
) -> list[Diagram]:
    progress = progress or (lambda *_: None)
    candidates = collect_candidates(pages)
    total = max(1, len(candidates))
    diagrams: list[Diagram] = []
    for i, cand in enumerate(candidates):
        score_candidate(cand, captions)
        grid = analyse_grid(cand)
        w = cand.bbox[2] - cand.bbox[0]
        h = cand.bbox[3] - cand.bbox[1]
        diagrams.append(
            Diagram(
                id=f"dg_{i + 1}",
                page=cand.page,
                bbox=[round(v, 2) for v in cand.bbox],
                width=round(w, 2),
                height=round(h, 2),
                kind=cand.kind,  # type: ignore[arg-type]
                is_board_candidate=cand.score >= 0.45,
                board_score=cand.score,
                caption=_nearest_caption(cand, captions.get(cand.page, [])),
                grid_detected=grid.as_json(),
                confidence=cand.score,
                notes=[f"{k}={v}" for k, v in cand.signals.items() if v >= 0.5],
            )
        )
        progress("diagrams", (i + 1) / total, f"candidate {i + 1}/{total}")
    return diagrams


def source_ref(diagram: Diagram) -> SourceRef:
    return SourceRef(page=diagram.page, bbox=diagram.bbox)