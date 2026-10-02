from __future__ import annotations

# Qt Widgets uses sRGB colors; keep the paper neutral and the chrome quiet.
STYLE_SHEET = """
QWidget { background: #f4f7f5; color: #202b25; font-family: "Segoe UI"; font-size: 10pt; }
QMainWindow { background: #e9eeeb; }
QFrame#panel { background: #f9fbfa; border: 1px solid #d2dcd5; border-radius: 6px; }
QPushButton {
  background: #f8faf9; color: #263c30; border: 1px solid #bccbc1;
  border-radius: 4px; padding: 6px 10px; font-weight: 500;
}
QPushButton:hover { background: #e7eee9; border-color: #8ba695; }
QPushButton:pressed { background: #d6e4da; }
QPushButton:focus { border: 2px solid #17653c; padding: 5px 9px; }
QPushButton[primary="true"] { background: #17653c; color: #fafcfb; border-color: #17653c; font-weight: 600; }
QPushButton[primary="true"]:hover { background: #125332; }
QPushButton:disabled { background: #edf1ee; color: #79857d; border-color: #d3dbd5; }
QLineEdit, QComboBox, QTextEdit, QSpinBox, QDoubleSpinBox {
  background: #fafcfb; border: 1px solid #bdcbc2; border-radius: 4px;
  padding: 5px 7px; selection-background-color: #c6e2d0; selection-color: #173524;
}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
  border: 2px solid #17653c; padding: 4px 6px;
}
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView { background: #fafcfb; selection-background-color: #c6e2d0; }
QTabWidget::pane { border: 1px solid #ccd8d0; background: #fafcfb; }
QTabBar::tab { padding: 7px 10px; background: #e7eeea; }
QTabBar::tab:selected { background: #d1e5d8; color: #17442c; }
QListWidget { background: #fafcfb; border: 1px solid #d0dad3; border-radius: 4px; }
QListWidget::item:selected { background: #cee3d5; color: #17442c; }
QLabel#title { font-size: 12pt; font-weight: 650; color: #183e29; }
QLabel#subtitle { color: #506356; }
QScrollArea { border: 1px solid #d0dad3; background: #e9eeeb; }
QStatusBar { background: #f4f7f5; border-top: 1px solid #d0dad3; }
"""
