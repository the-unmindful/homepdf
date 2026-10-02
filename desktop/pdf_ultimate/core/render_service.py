from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from PySide6.QtCore import QObject, Signal, Qt
from pdf_ultimate.core.process_pool import get_process_pool
from pdf_ultimate.core.worker_tasks import render_page_pixels


class PdfRenderService(QObject):
    """Bounded process rendering. All scheduling mutations run on the Qt thread."""
    rendered = Signal(object, object)
    failed = Signal(object, str)
    _completed = Signal(object)

    def __init__(self, parent=None, max_workers=2):
        super().__init__(parent)
        self._executor = get_process_pool(max_workers=max_workers)
        self._limit = min(2, max_workers)
        self._generation = 0
        self._active = {}
        self._pending = OrderedDict()
        self._completed.connect(self._finish, Qt.QueuedConnection)

    @property
    def pending_count(self):
        return len(self._pending)

    def invalidate(self, *, keep_thumbnails=False):
        """Drop obsolete work. Thumbnails (priority 2) are zoom-independent, so a
        zoom-only change keeps them instead of discarding in-flight renders."""
        self._generation += 1
        if keep_thumbnails:
            self._pending = OrderedDict(
                (key, (job[0], self._generation, job[2])) for key, job in self._pending.items() if job[0] == 2
            )
        else:
            self._pending.clear()
        for future, (key, _generation, priority) in list(self._active.items()):
            if keep_thumbnails and priority == 2:
                self._active[future] = (key, self._generation, priority)
            else:
                future.cancel()

    def retain(self, keys):
        wanted = set(keys)
        self._pending = OrderedDict((key, job) for key, job in self._pending.items() if key in wanted or job[0] == 2)

    def queue_render(self, *, key, pdf_path: Path, page_index, zoom, quality, priority=0, password=None):
        if any(job[0] == key and job[1] == self._generation for job in self._active.values()):
            return
        old = self._pending.get(key)
        priority = min(priority, old[0]) if old else priority
        self._pending[key] = (priority, self._generation, (str(pdf_path), page_index, zoom, quality, password))
        while len(self._pending) > 24:
            worst = max(self._pending, key=lambda item: self._pending[item][0])
            self._pending.pop(worst)
        self._pump()

    def _pump(self):
        while len(self._active) < self._limit and self._pending:
            key = min(self._pending, key=lambda item: self._pending[item][0])
            priority, generation, args = self._pending.pop(key)
            future = self._executor.submit(render_page_pixels, *args)
            self._active[future] = (key, generation, priority)
            future.add_done_callback(self._done)

    def _done(self, future):
        try:
            self._completed.emit(future)
        except RuntimeError:
            pass

    def _finish(self, future):
        job = self._active.pop(future, None)
        if job and job[1] == self._generation:
            try:
                self.rendered.emit(job[0], future.result())
            except Exception as exc:
                self.failed.emit(job[0], str(exc))
        self._pump()
