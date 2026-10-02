from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget


class ToolSection(QFrame):
    """A titled card whose body expands on demand."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("toolSection")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header = QPushButton()
        self.header.setObjectName("sectionHeader")
        self._title = title
        self.header.setCheckable(True)
        self.header.setFlat(True)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header.toggled.connect(self.set_expanded)
        outer.addWidget(self.header)
        self.body = QWidget()
        self.body.setObjectName("sectionBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(10, 4, 10, 10)
        self.body_layout.setSpacing(6)
        outer.addWidget(self.body)
        self.body.setVisible(False)
        self._update_header(False)

    def _update_header(self, expanded: bool) -> None:
        # Arrow types hide the text on Fusion, so the chevron is part of the label.
        self.header.setText(("▾  " if expanded else "▸  ") + self._title)

    def set_expanded(self, expanded: bool) -> None:
        self.header.blockSignals(True)
        self.header.setChecked(expanded)
        self.header.blockSignals(False)
        self._update_header(expanded)
        self.body.setVisible(expanded)


def accordionize(tab: QWidget, titles: set[str]) -> list[ToolSection]:
    """Regroup a flat 'title label, fields, button' form into collapsible cards.

    A QLabel whose text is in `titles` starts a new card; following widgets and
    layouts move into it. Only one card per form is open at a time.
    """
    layout = tab.layout()
    items = []
    while layout.count():
        items.append(layout.takeAt(0))
    sections: list[ToolSection] = []
    current: ToolSection | None = None
    for item in items:
        widget = item.widget()
        if isinstance(widget, QLabel) and widget.text() in titles:
            current = ToolSection(widget.text())
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
            sections.append(current)
            layout.addWidget(current)
            continue
        if item.spacerItem() is not None:
            continue
        target = current.body_layout if current is not None else layout
        if widget is not None:
            target.addWidget(widget)
        elif item.layout() is not None:
            target.addLayout(item.layout())
    layout.addStretch(1)
    layout.setSpacing(6)

    def open_only(opened: ToolSection, expanded: bool) -> None:
        if not expanded:
            return
        for section in sections:
            if section is not opened:
                section.set_expanded(False)

    for section in sections:
        section.header.toggled.connect(lambda expanded, s=section: open_only(s, expanded))
    if sections:
        sections[0].set_expanded(True)
    return sections
