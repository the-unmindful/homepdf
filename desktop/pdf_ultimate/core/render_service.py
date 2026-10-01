from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from pdf_ultimate.core.process_pool import get_process_pool
from pdf_ultimate.core.worker_tasks import render_page_png_bytes


class PdfRenderService(QObject):
    """
    Renders PDF pages in a *separate process* and returns PNG bytes.

    Why processes?
    PyMuPDF (fitz) is not thread-safe. Rendering in threads can crash / hang.
    Running PyMuPDF work in a separate process avoids those issues and still
    uses multiple CPU cores.
    """

    rendered = Signal(object, object)  # (key, png_bytes)
    failed = Signal(object, str)  # (key, error_message)

    def __init__(self, parent: QObject | None = None, max_workers: int = 2) -> None:
        super().__init__(parent)
        self._executor = get_process_pool(max_workers=max_workers)
        self._futures: dict[object, object] = {}

    def queue_render(
        self,
        *,
        key: tuple[str, int, float, float],
        pdf_path: Path,
        page_index: int,
        zoom: float,
        quality: float,
    ) -> None:
        future = self._executor.submit(render_page_png_bytes, str(pdf_path), int(page_index), float(zoom), float(quality))
        self._futures[future] = key
        future.add_done_callback(self._on_future_done)

    def _on_future_done(self, future) -> None:  # pragma: no cover - callback path
        key = self._futures.pop(future, None)
        if key is None:
            return
        try:
            data: bytes = future.result()
        except Exception as exc:
            try:
                self.failed.emit(key, str(exc))
            except RuntimeError:
                return
            return
        try:
            self.rendered.emit(key, data)
        except RuntimeError:
            return
