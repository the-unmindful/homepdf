from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QWidget


class SelectablePageLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._selection_enabled = False
        self._zoom = 1.0
        self._page_words: list[tuple[float, float, float, float, str, int, int, int]] = []
        self._selected_word_indexes: set[int] = set()
        self._selected_text = ""
        self._drag_start_page: tuple[float, float] | None = None
        self._drag_rect_page: tuple[float, float, float, float] | None = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

    def set_selection_mode(self, enabled: bool) -> None:
        self._selection_enabled = bool(enabled)
        if not self._selection_enabled:
            self.clear_selection()
        self.update()

    def set_page_words(self, words: list[tuple], zoom: float) -> None:
        normalized: list[tuple[float, float, float, float, str, int, int, int]] = []
        for word in words:
            if len(word) < 8:
                continue
            normalized.append(
                (
                    float(word[0]),
                    float(word[1]),
                    float(word[2]),
                    float(word[3]),
                    str(word[4]),
                    int(word[5]),
                    int(word[6]),
                    int(word[7]),
                )
            )
        self._page_words = normalized
        self._zoom = max(0.001, float(zoom))
        self.clear_selection()

    def clear_selection(self) -> None:
        self._selected_word_indexes.clear()
        self._selected_text = ""
        self._drag_start_page = None
        self._drag_rect_page = None
        self.update()

    def selected_text(self) -> str:
        return self._selected_text

    def _pixmap_rect(self) -> QRect:
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            return QRect(0, 0, self.width(), self.height())
        logical = pixmap.deviceIndependentSize().toSize()
        width = max(1, logical.width())
        height = max(1, logical.height())
        x = 0
        y = 0
        alignment = self.alignment()
        if alignment & Qt.AlignHCenter:
            x = (self.width() - width) // 2
        elif alignment & Qt.AlignRight:
            x = self.width() - width
        if alignment & Qt.AlignVCenter:
            y = (self.height() - height) // 2
        elif alignment & Qt.AlignBottom:
            y = self.height() - height
        return QRect(x, y, width, height)

    def _point_to_page(self, point: QPoint, *, clamp: bool = False) -> tuple[float, float] | None:
        area = self._pixmap_rect()
        if area.width() <= 0 or area.height() <= 0:
            return None
        x = point.x()
        y = point.y()
        if clamp:
            x = max(area.left(), min(area.left() + area.width() - 1, x))
            y = max(area.top(), min(area.top() + area.height() - 1, y))
        elif not area.contains(point):
            return None
        return ((x - area.left()) / self._zoom, (y - area.top()) / self._zoom)

    @staticmethod
    def _rect_intersects(
        a: tuple[float, float, float, float],
        b: tuple[float, float, float, float],
    ) -> bool:
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        return not (ax1 < bx0 or ax0 > bx1 or ay1 < by0 or ay0 > by1)

    def _word_indexes_for_rect(self, rect: tuple[float, float, float, float]) -> set[int]:
        x0, y0, x1, y1 = rect
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        drag_rect = (x0, y0, x1, y1)
        result: set[int] = set()
        for idx, (wx0, wy0, wx1, wy1, _text, _block, _line, _word) in enumerate(self._page_words):
            if self._rect_intersects(drag_rect, (wx0, wy0, wx1, wy1)):
                result.add(idx)
        return result

    def _word_at(self, point: tuple[float, float], *, slack: float = 0.0) -> int | None:
        px, py = point
        for idx, (x0, y0, x1, y1, *_rest) in enumerate(self._page_words):
            if x0 - slack <= px <= x1 + slack and y0 - slack <= py <= y1 + slack:
                return idx
        return None

    def _nearest_word(self, point: tuple[float, float]) -> int | None:
        """Word under the point, else the closest word on the nearest line."""
        hit = self._word_at(point)
        if hit is not None or not self._page_words:
            return hit
        px, py = point

        def distance(item):
            x0, y0, x1, y1 = item[1][:4]
            dy = 0.0 if y0 <= py <= y1 else min(abs(py - y0), abs(py - y1))
            dx = 0.0 if x0 <= px <= x1 else min(abs(px - x0), abs(px - x1))
            return (dy, dx)

        return min(enumerate(self._page_words), key=distance)[0]

    def _indexes_for_drag(self, rect: tuple[float, float, float, float]) -> set[int]:
        if getattr(self, "_column_mode", False):
            return self._word_indexes_for_rect(rect)
        sx, sy, ex, ey = rect
        if abs(ex - sx) < 2 and abs(ey - sy) < 2:
            return set()
        start = self._nearest_word((sx, sy))
        end = self._nearest_word((ex, ey))
        if start is None or end is None:
            return set()
        low, high = min(start, end), max(start, end)
        return set(range(low, high + 1))

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if not self._selection_enabled or event.button() != Qt.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        page_point = self._point_to_page(event.position().toPoint(), clamp=False)
        word = self._word_at(page_point) if page_point is not None else None
        if word is not None:
            self._selected_word_indexes = {word}
            self._selected_text = self._build_selected_text(self._selected_word_indexes)
            self._drag_start_page = None
            self._drag_rect_page = None
            self.update()
        event.accept()

    def _build_selected_text(self, indexes: set[int]) -> str:
        if not indexes:
            return ""
        ordered = sorted((self._page_words[idx] for idx in indexes), key=lambda item: (item[5], item[6], item[7]))
        parts: list[str] = []
        prev_block: int | None = None
        prev_line: int | None = None
        for _x0, _y0, _x1, _y1, text, block, line, _word in ordered:
            if prev_block is None:
                parts.append(text)
            elif block != prev_block:
                parts.append("\n\n")
                parts.append(text)
            elif line != prev_line:
                parts.append("\n")
                parts.append(text)
            else:
                parts.append(" ")
                parts.append(text)
            prev_block = block
            prev_line = line
        return "".join(parts).strip()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if not self._selection_enabled or event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        page_point = self._point_to_page(event.position().toPoint(), clamp=False)
        if page_point is None:
            self.clear_selection()
            event.accept()
            return
        self.setFocus(Qt.MouseFocusReason)
        self._drag_start_page = page_point
        self._column_mode = bool(event.modifiers() & Qt.AltModifier)
        self._drag_rect_page = (page_point[0], page_point[1], page_point[0], page_point[1])
        self._selected_word_indexes = self._indexes_for_drag(self._drag_rect_page)
        self._selected_text = self._build_selected_text(self._selected_word_indexes)
        self.update()
        event.accept()

    def _hover_words(self, point: QPoint) -> bool:
        """Whether `point` is over a word (subclasses may load words lazily)."""
        page_point = self._point_to_page(point, clamp=False)
        return page_point is not None and self._word_at(page_point, slack=1.0) is not None

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._selection_enabled and self._drag_start_page is None:
            over_text = self._hover_words(event.position().toPoint())
            self.setCursor(Qt.IBeamCursor if over_text else Qt.ArrowCursor)
        if not self._selection_enabled or self._drag_start_page is None:
            super().mouseMoveEvent(event)
            return
        page_point = self._point_to_page(event.position().toPoint(), clamp=True)
        if page_point is None:
            return
        sx, sy = self._drag_start_page
        self._drag_rect_page = (sx, sy, page_point[0], page_point[1])
        self._selected_word_indexes = self._indexes_for_drag(self._drag_rect_page)
        self._selected_text = self._build_selected_text(self._selected_word_indexes)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if not self._selection_enabled or event.button() != Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self._drag_start_page is None:
            return
        page_point = self._point_to_page(event.position().toPoint(), clamp=True)
        if page_point is not None:
            sx, sy = self._drag_start_page
            self._drag_rect_page = (sx, sy, page_point[0], page_point[1])
            self._selected_word_indexes = self._indexes_for_drag(self._drag_rect_page)
            self._selected_text = self._build_selected_text(self._selected_word_indexes)
        self._drag_start_page = None
        self._drag_rect_page = None
        self.update()
        event.accept()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if self._selection_enabled and event.matches(QKeySequence.Copy):
            if self._selected_text:
                QApplication.clipboard().setText(self._selected_text)
                event.accept()
                return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if not self._selection_enabled:
            return
        if not self._selected_word_indexes and self._drag_rect_page is None:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        self.paint_selection(painter, self._pixmap_rect())
        painter.end()

    def paint_selection(self, painter: QPainter, area: QRect) -> None:
        if not self._selection_enabled:
            return
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(76, 141, 247, 88))
        for idx in self._selected_word_indexes:
            if idx < 0 or idx >= len(self._page_words):
                continue
            x0, y0, x1, y1, _text, _block, _line, _word = self._page_words[idx]
            x = area.left() + int(x0 * self._zoom)
            y = area.top() + int(y0 * self._zoom)
            w = max(2, int((x1 - x0) * self._zoom))
            h = max(2, int((y1 - y0) * self._zoom))
            painter.drawRect(x, y, w, h)

        if self._drag_rect_page is not None and getattr(self, "_column_mode", False):
            x0, y0, x1, y1 = self._drag_rect_page
            left = area.left() + int(min(x0, x1) * self._zoom)
            top = area.top() + int(min(y0, y1) * self._zoom)
            width = max(1, int(abs(x1 - x0) * self._zoom))
            height = max(1, int(abs(y1 - y0) * self._zoom))
            painter.setPen(QColor(47, 114, 225, 210))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(left, top, width, height)

        painter.restore()


