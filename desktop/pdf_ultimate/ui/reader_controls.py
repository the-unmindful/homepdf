"""Native outline icons without an image library or icon font."""
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QToolButton


def reader_icon(name: str) -> QIcon:
    icon = QIcon()
    app = QApplication.instance()
    color = app.palette().color(QPalette.ButtonText) if app else QColor('#29313d')
    for size in (20, 24, 32, 40, 48, 64):
        image = QPixmap(size, size)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(size / 24, size / 24)
        painter.setPen(QPen(color, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if name.startswith(('navigation', 'tools')):
            left = name.startswith('navigation')
            if left:
                # Page list: the content behind the navigation pane button.
                painter.drawRoundedRect(2, 3, 13, 18, 1.5, 1.5)
                for y in (8, 12, 16):
                    painter.drawPoint(5, y)
                    painter.drawLine(8, y, 12, y)
            else:
                # A familiar wrench keeps tools distinct from navigation.
                wrench = QPainterPath()
                wrench.moveTo(13, 3)
                wrench.cubicTo(9, 2, 7, 6, 9, 9)
                wrench.lineTo(3, 15)
                wrench.cubicTo(0, 18, 4, 22, 7, 19)
                wrench.lineTo(13, 13)
                wrench.cubicTo(17, 14, 19, 9, 17, 6)
                wrench.lineTo(14, 9)
                wrench.lineTo(11, 6)
                wrench.closeSubpath()
                painter.drawPath(wrench)
            # Secondary chevron indicates opening/closing a side pane.
            center = 21
            direction = -1 if (left == name.endswith('-open')) else 1
            painter.drawLine(center - direction, 9, center + direction, 12)
            painter.drawLine(center + direction, 12, center - direction, 15)
        elif name == 'search':
            painter.drawEllipse(4, 4, 12, 12)
            painter.drawLine(15, 15, 21, 21)
        elif name == 'fit-width':
            painter.drawLine(3, 4, 3, 20)
            painter.drawLine(21, 4, 21, 20)
            painter.drawLine(5, 12, 19, 12)
            for x, sign in ((5, 1), (19, -1)):
                painter.drawLine(x, 12, x + 3 * sign, 9)
                painter.drawLine(x, 12, x + 3 * sign, 15)
        elif name == 'fit-page':
            painter.drawRect(7, 5, 10, 14)
            for x, y, dx, dy in ((3, 2, 3, 3), (21, 2, -3, 3), (3, 22, 3, -3), (21, 22, -3, -3)):
                painter.drawLine(x, y, x + dx, y)
                painter.drawLine(x, y, x, y + dy)
        elif name == 'actual-size':
            painter.setFont(QFont('Segoe UI', 9, QFont.DemiBold))
            painter.drawText(QRect(0, 0, 24, 24), Qt.AlignCenter, '1:1')
        elif name == 'theme-dark':
            painter.drawArc(4, 4, 15, 15, 60 * 16, 250 * 16)
            painter.drawArc(8, 1, 13, 14, 110 * 16, 155 * 16)
        elif name == 'theme-light':
            painter.drawEllipse(8, 8, 8, 8)
            for x0, y0, x1, y1 in ((12, 2, 12, 5), (12, 19, 12, 22), (2, 12, 5, 12), (19, 12, 22, 12), (5, 5, 7, 7), (17, 17, 19, 19), (5, 19, 7, 17), (17, 7, 19, 5)):
                painter.drawLine(x0, y0, x1, y1)
        else:
            painter.drawRoundedRect(3, 4, 18, 13, 2, 2)
            painter.drawLine(12, 17, 12, 21)
            painter.drawLine(8, 21, 16, 21)
        painter.end()
        icon.addPixmap(image)
    return icon


def icon_button(name: str, label: str, *, checkable=False) -> QToolButton:
    button = QToolButton()
    button.setIcon(reader_icon(name))
    button.setIconSize(QSize(20, 20))
    button.setFixedSize(32, 32)
    button.setAutoRaise(True)
    button.setCheckable(checkable)
    button.setToolTip(label)
    button.setAccessibleName(label)
    return button
