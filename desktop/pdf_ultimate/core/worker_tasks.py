from __future__ import annotations

"""
Worker tasks that are safe to run in a separate *process*.

Important:
- This module MUST NOT import PySide6 / Qt (keeps worker process lightweight and avoids GUI init).
- Functions must be top-level so they are picklable for ProcessPoolExecutor on Windows (spawn).
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: Dict[int, List[Tuple[float, float, float, float]]]
    # sequence items are (display_index, actual_page_index, local_hit_index)
    sequence: List[Tuple[int, int, int]]


MAX_RENDER_PIXELS = 12_000_000

@dataclass(frozen=True, slots=True)
class RenderedPage:
    width: int
    height: int
    stride: int
    samples: bytes


def bounded_scale(width: float, height: float, requested: float) -> float:
    import math
    # Allow for the integer rounding done by MuPDF, including fractional origins.
    scale = min(requested, math.sqrt(MAX_RENDER_PIXELS / ((width + 2) * (height + 2))))
    while (math.ceil(width * scale) + 2) * (math.ceil(height * scale) + 2) > MAX_RENDER_PIXELS:
        scale *= 0.995
    return scale


# Per-process cache of open documents. Reopening parses the xref on every page;
# on a 3,000-page file that is ~15 ms per render versus ~1 ms with the document kept
# open. Documents are opened from memory so no file handle is held, and the key
# includes mtime/size so an edited file is reopened.
_DOC_CACHE: "OrderedDict[tuple[str, int, int], object]" = None  # type: ignore[assignment]
_DOC_CACHE_LIMIT = 2
_DOC_CACHE_MAX_BYTES = 256 * 1024 * 1024


def _cached_document(pdf_path: str):
    import os
    from collections import OrderedDict
    import fitz
    global _DOC_CACHE
    if _DOC_CACHE is None:
        _DOC_CACHE = OrderedDict()
    stat = os.stat(pdf_path)
    if stat.st_size > _DOC_CACHE_MAX_BYTES:
        return None
    key = (os.path.normcase(os.path.abspath(pdf_path)), stat.st_mtime_ns, stat.st_size)
    doc = _DOC_CACHE.get(key)
    if doc is not None:
        _DOC_CACHE.move_to_end(key)
        return doc
    with open(pdf_path, "rb") as handle:
        data = handle.read()
    doc = fitz.open(stream=data, filetype="pdf")
    _DOC_CACHE[key] = doc
    while len(_DOC_CACHE) > _DOC_CACHE_LIMIT:
        _, old = _DOC_CACHE.popitem(last=False)
        old.close()
    return doc


def _render_from(doc, page_index: int, zoom: float, quality: float) -> RenderedPage:
    import fitz
    page = doc.load_page(page_index)
    scale = bounded_scale(page.rect.width, page.rect.height, zoom * quality)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
    if pix.width * pix.height > MAX_RENDER_PIXELS:
        raise ValueError("Page exceeds the rendering budget")
    return RenderedPage(pix.width, pix.height, pix.stride, pix.samples)


def render_page_pixels(pdf_path: str, page_index: int, zoom: float, quality: float, password: str | None = None) -> RenderedPage:
    import fitz
    try:
        doc = _cached_document(pdf_path)
    except Exception:
        doc = None
    if doc is not None and doc.is_encrypted and password:
        doc.authenticate(password)
    # needs_pass stays set after a successful authenticate(); is_encrypted clears.
    if doc is not None and not doc.is_encrypted:
        return _render_from(doc, page_index, zoom, quality)
    with fitz.open(pdf_path) as doc:
        if doc.needs_pass and password:
            doc.authenticate(password)
        return _render_from(doc, page_index, zoom, quality)


def search_pdf_text(
    pdf_path: str,
    ordered_page_indices: List[int],
    query: str,
    password: str | None = None,
) -> SearchResult:
    """Search text in the PDF and return normalized hit rectangles per page."""
    import fitz  # local import

    q = (query or "").strip()
    if not q:
        return SearchResult(hits={}, sequence=[])

    doc = fitz.open(pdf_path)
    if doc.needs_pass and password:
        doc.authenticate(password)
    try:
        hits: Dict[int, List[Tuple[float, float, float, float]]] = {}
        sequence: List[Tuple[int, int, int]] = []
        for display_idx, actual_idx in enumerate(ordered_page_indices):
            if actual_idx < 0 or actual_idx >= doc.page_count:
                continue
            page = doc.load_page(actual_idx)
            rects = page.search_for(q)
            if not rects:
                continue
            normalized = [(float(r.x0), float(r.y0), float(r.x1), float(r.y1)) for r in rects]
            hits[actual_idx] = normalized
            for local_idx in range(len(normalized)):
                sequence.append((display_idx, actual_idx, local_idx))
        return SearchResult(hits=hits, sequence=sequence)
    finally:
        doc.close()
