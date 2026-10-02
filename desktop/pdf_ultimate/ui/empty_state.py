from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget


class EmptyState(QWidget):
    """Start screen: one clear action, the recent files, and the drop hint."""

    openRequested = Signal()
    recentChosen = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("emptyState")
        self.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(2)

        column = QWidget()
        column.setObjectName("emptyColumn")
        column.setMaximumWidth(560)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        icon = QLabel()
        icon_path = Path(__file__).resolve().parent.parent / "resources" / "app.png"
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            dpr = self.devicePixelRatioF()
            scaled = pixmap.scaled(int(64 * dpr), int(64 * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            icon.setPixmap(scaled)
        icon.setAlignment(Qt.AlignHCenter)
        layout.addWidget(icon)

        title = QLabel("Open a PDF")
        title.setObjectName("emptyTitle")
        title.setAlignment(Qt.AlignHCenter)
        layout.addWidget(title)

        hint = QLabel("Drop a file anywhere in this window, or press Ctrl+O.")
        hint.setObjectName("subtitle")
        hint.setAlignment(Qt.AlignHCenter)
        layout.addWidget(hint)

        self.open_button = QPushButton("Open file…")
        self.open_button.setProperty("primary", True)
        self.open_button.setMinimumWidth(180)
        self.open_button.clicked.connect(self.openRequested)
        layout.addWidget(self.open_button, 0, Qt.AlignHCenter)

        layout.addSpacing(18)
        self.recent_title = QLabel("Recent")
        self.recent_title.setObjectName("sectionLabel")
        layout.addWidget(self.recent_title)
        self._recent_box = QVBoxLayout()
        self._recent_box.setSpacing(2)
        layout.addLayout(self._recent_box)

        outer.addWidget(column, 0, Qt.AlignHCenter)
        outer.addStretch(3)

    def set_recent(self, paths: list[Path]) -> None:
        while self._recent_box.count():
            item = self._recent_box.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        shown = [path for path in paths if path.exists()][:8]
        self.recent_title.setVisible(bool(shown))
        for path in shown:
            button = QPushButton(f"{path.name}    —  {path.parent}")
            button.setObjectName("recentFile")
            button.setToolTip(str(path))
            button.setMinimumWidth(440)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, p=path: self.recentChosen.emit(p))
            self._recent_box.addWidget(button)
