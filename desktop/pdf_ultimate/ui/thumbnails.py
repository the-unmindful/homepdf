from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

_LABEL_HEIGHT = 22
_PAD = 8
_DEFAULT_ASPECT = 595 / 842


class ThumbnailDelegate(QStyledItemDelegate):
    """Centered page thumbnail with its number underneath; selection is an accent frame."""

    def __init__(self, icon_size: QSize, parent=None) -> None:
        super().__init__(parent)
        self._icon_size = icon_size

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        return QSize(self._icon_size.width() + _PAD * 2, self._icon_size.height() + _LABEL_HEIGHT + _PAD * 2)

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        palette = option.palette
        cell = option.rect
        box_w, box_h = self._icon_size.width(), self._icon_size.height()

        icon = index.data(Qt.DecorationRole)
        pixmap = icon.pixmap(self._icon_size) if icon is not None and not icon.isNull() else None
        if pixmap is not None and not pixmap.isNull():
            dpr = pixmap.devicePixelRatio() or 1.0
            pw, ph = pixmap.width() / dpr, pixmap.height() / dpr
        else:
            ph = box_h
            pw = ph * _DEFAULT_ASPECT
        scale = min(box_w / max(1.0, pw), box_h / max(1.0, ph), 1.0 if pixmap is not None else 10.0)
        w, h = int(pw * scale), int(ph * scale)
        page = QRect(cell.left() + (cell.width() - w) // 2, cell.top() + _PAD + (box_h - h) // 2, w, h)

        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        accent = palette.color(QPalette.Accent)

        painter.fillRect(page.adjusted(1, 2, 1, 2), QColor(0, 0, 0, 40))
        painter.fillRect(page, QColor("#ffffff"))
        if pixmap is not None and not pixmap.isNull():
            painter.drawPixmap(page, pixmap)
        if selected or hovered:
            pen = QPen(accent if selected else palette.color(QPalette.Mid), 3 if selected else 1)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(page.adjusted(-3, -3, 3, 3), 3, 3)

        label = QRect(cell.left(), page.bottom() + 4, cell.width(), _LABEL_HEIGHT - 4)
        font = painter.font()
        font.setBold(selected)
        painter.setFont(font)
        painter.setPen(accent if selected else palette.color(QPalette.PlaceholderText))
        painter.drawText(label, Qt.AlignHCenter | Qt.AlignTop, str(index.data(Qt.DisplayRole) or ""))
        painter.restore()
