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


def render_page_png_bytes(pdf_path: str, page_index: int, zoom: float, quality: float) -> bytes:
    """Render a single PDF page to PNG bytes using PyMuPDF (fitz)."""
    import fitz  # local import: keep worker import-light

    doc = fitz.open(pdf_path)
    try:
        if page_index < 0 or page_index >= doc.page_count:
            raise ValueError(f"Invalid page index: {page_index}")
        page = doc.load_page(page_index)
        matrix = fitz.Matrix(zoom * quality, zoom * quality)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        # PyMuPDF Pixmap supports tobytes("png") (see PyMuPDF changelog / docs).
        return pix.tobytes("png")
    finally:
        doc.close()


def search_pdf_text(
    pdf_path: str,
    ordered_page_indices: List[int],
    query: str,
) -> SearchResult:
    """Search text in the PDF and return normalized hit rectangles per page."""
    import fitz  # local import

    q = (query or "").strip()
    if not q:
        return SearchResult(hits={}, sequence=[])

    doc = fitz.open(pdf_path)
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
