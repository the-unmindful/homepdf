from __future__ import annotations

STYLE_SHEET = """
QWidget {
  background: #f3f6fb;
  color: #1d2735;
  font-family: "Segoe UI Variable", "Segoe UI", "Bahnschrift", sans-serif;
  font-size: 10.5pt;
}
QMainWindow {
  background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
    stop:0 #f8fbff, stop:0.48 #e9f0fb, stop:1 #d9e8fb);
}
QFrame#panel {
  background: rgba(255,255,255,0.93);
  border: 1px solid #d4dfed;
  border-radius: 16px;
}
QPushButton {
  background: #1f4ec6;
  color: white;
  border: none;
  border-radius: 11px;
  padding: 7px 13px;
  font-weight: 600;
}
QPushButton:hover { background: #1d43aa; }
QPushButton:pressed { background: #1a378d; }
QPushButton:disabled {
  background: #9ca3af;
  color: #e5e7eb;
}
QLineEdit, QComboBox, QTextEdit, QSpinBox, QDoubleSpinBox {
  background: #ffffff;
  border: 1px solid #c6d3e7;
  border-radius: 8px;
  padding: 5px 8px;
  selection-background-color: #cfe0ff;
}
QComboBox::drop-down {
  border: none;
  width: 22px;
}
QComboBox QAbstractItemView {
  border: 1px solid #ccd8eb;
  selection-background-color: #d9e7ff;
}
QTabWidget::pane {
  border: 1px solid #d7e1ef;
  border-radius: 12px;
  background: #ffffff;
}
QTabBar::tab {
  padding: 8px 13px;
  margin-right: 4px;
  background: #e8eef8;
  border-top-left-radius: 9px;
  border-top-right-radius: 9px;
}
QTabBar::tab:selected {
  background: #d7e6ff;
  color: #183a82;
}
QListWidget {
  border: 1px solid #d3ddec;
  border-radius: 10px;
  background: #ffffff;
}
QLabel#title {
  font-size: 15.5pt;
  font-weight: 700;
  color: #0f1f36;
}
QLabel#subtitle {
  color: #3b4c64;
}
QScrollArea {
  border: 1px solid #d3ddec;
  border-radius: 10px;
  background: #edf3fb;
}
QScrollBar:vertical {
  background: transparent;
  width: 12px;
  margin: 2px;
}
QScrollBar::handle:vertical {
  background: #b9c8df;
  min-height: 30px;
  border-radius: 6px;
}
QScrollBar::handle:vertical:hover {
  background: #a4b8d5;
}
QScrollBar:horizontal {
  background: transparent;
  height: 12px;
  margin: 2px;
}
QScrollBar::handle:horizontal {
  background: #b9c8df;
  min-width: 30px;
  border-radius: 6px;
}
QStatusBar {
  background: rgba(255,255,255,0.92);
  border-top: 1px solid #d3ddec;
}
"""
