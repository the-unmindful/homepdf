from __future__ import annotations

from collections.abc import Callable
from bisect import bisect_right

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import QWidget
from .selectable_page import SelectablePageLabel


class ContinuousPageView(SelectablePageLabel):
    pageActivated = Signal(int)

    def __init__(
        self,
        image_provider: Callable[[int, float], QImage | None],
        highlight_provider: Callable[[int], list[tuple[float, float, float, float]]] | None = None,
        active_highlight_provider: Callable[[], tuple[int, int] | None] | None = None,
        parent: QWidget | None = None,
        word_provider: Callable[[int], list[tuple]] | None = None,
    ) -> None:
        super().__init__(parent=parent)
        self._image_provider = image_provider
        self._highlight_provider = highlight_provider
        self._active_highlight_provider = active_highlight_provider
        self._word_provider = word_provider
        self._selection_row = -1
        self._page_indices: list[int] = []
        self._page_rects: list[QRect] = []
        self._tops = []
        self._zoom = 1.0
        self._margin = 18
        self._spacing = 18
        self._canvas_size = QSize(900, 1200)
        self.setMinimumSize(220, 220)

    def configure(self, page_indices: list[int], page_sizes: list[QSize], zoom: float) -> None:
        self.clear_selection()
        self._selection_row = -1
        self._page_indices = list(page_indices)
        self._zoom = float(zoom)
        self._page_rects.clear()
        self._tops.clear()

        if not page_sizes:
            self._canvas_size = QSize(900, 1200)
            self.setFixedSize(self._canvas_size)
            self.update()
            return

        max_width = max(size.width() for size in page_sizes)
        y = self._margin
        for size in page_sizes:
            x = self._margin + (max_width - size.width()) // 2
            rect = QRect(x, y, size.width(), size.height())
            self._page_rects.append(rect)
            self._tops.append(y)
            y += size.height() + self._spacing

        total_height = y - self._spacing + self._margin if self._page_rects else 1200
        self._canvas_size = QSize(max_width + self._margin * 2, max(220, total_height))
        self.setFixedSize(self._canvas_size)
        self.update()

    def _pixmap_rect(self) -> QRect:
        if 0 <= self._selection_row < len(self._page_rects):
            return self._page_rects[self._selection_row]
        return QRect()

    def mousePressEvent(self, event) -> None:
        if self._selection_enabled and event.button() == Qt.LeftButton:
            point = event.position().toPoint()
            row = self.page_at_offset(point.y())
            if self._page_rects and self._page_rects[row].contains(point):
                self._selection_row = row
                page = self._page_indices[row]
                self.set_page_words(self._word_provider(page) if self._word_provider else [], self._zoom)
                self.pageActivated.emit(row)
            else:
                self._selection_row = -1
                self.clear_selection()
        super().mousePressEvent(event)

    def page_count(self) -> int:
        return len(self._page_indices)

    def page_top(self, row_index: int) -> int:
        if row_index < 0 or row_index >= len(self._page_rects):
            return 0
        return self._page_rects[row_index].top()

    def page_at_offset(self, y_offset: int) -> int:
        if not self._page_rects:
            return 0
        return max(0, bisect_right(self._tops, y_offset) - 1)

    def update_page(self, page_index: int) -> None:
        for row, actual in enumerate(self._page_indices):
            if actual == page_index:
                self.update(self._page_rects[row].adjusted(-2, -2, 2, 2))

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(event.rect(), self.palette().color(QPalette.Window))

        if not self._page_rects:
            painter.end()
            return

        visible = event.rect()
        start = self.page_at_offset(visible.top())
        for row in range(start, len(self._page_rects)):
            rect = self._page_rects[row]
            if rect.top() > visible.bottom():
                break
            if not rect.intersects(visible):
                continue
            page_index = self._page_indices[row]
            painter.fillRect(rect.adjusted(-2, -2, 2, 2), self.palette().color(QPalette.Mid))
            painter.fillRect(rect, QColor("#ffffff"))

            image = self._image_provider(page_index, self._zoom)
            if image is not None:
                painter.drawImage(rect, image)
                if self._highlight_provider is not None:
                    highlights = self._highlight_provider(page_index)
                    if highlights:
                        active_hit = self._active_highlight_provider() if self._active_highlight_provider is not None else None
                        painter.save()
                        painter.setPen(Qt.NoPen)
                        for highlight_idx, (x0, y0, x1, y1) in enumerate(highlights):
                            x = rect.left() + int(x0 * self._zoom)
                            y = rect.top() + int(y0 * self._zoom)
                            w = max(2, int((x1 - x0) * self._zoom))
                            h = max(2, int((y1 - y0) * self._zoom))
                            if active_hit is not None and active_hit[0] == page_index and active_hit[1] == highlight_idx:
                                painter.setBrush(QColor(255, 196, 57, 168))
                                painter.drawRect(x, y, w, h)
                                painter.setPen(QColor(224, 129, 28, 210))
                                painter.drawRect(x, y, w, h)
                                painter.setPen(Qt.NoPen)
                            else:
                                painter.setBrush(QColor(255, 232, 120, 105))
                                painter.drawRect(x, y, w, h)
                        painter.restore()
            else:
                painter.setPen(QColor("#5f6b7f"))
                painter.drawText(rect, Qt.AlignCenter, "Rendering...")

        if self._selection_row >= 0:
            self.paint_selection(painter, self._pixmap_rect())
        painter.end()
