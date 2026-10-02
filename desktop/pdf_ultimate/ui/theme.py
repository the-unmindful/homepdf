from __future__ import annotations

import sys
from string import Template

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


# Chrome uses neutral surfaces. PDF page pixels retain their original paper color.
_LIGHT = {
    "window": "#f3f4f6", "surface": "#ffffff", "alternate": "#f7f8fa",
    "text": "#20242b", "secondary": "#586270", "border": "#c7cdd5",
    "soft_border": "#dce0e6", "button": "#fafbfc", "hover": "#e8edf3",
    "pressed": "#dbe2eb", "accent": "#0b63ce", "primary": "#0b63ce",
    "primary_hover": "#084fa8", "primary_pressed": "#06428e",
    "selection": "#dceaff", "selection_text": "#143c70",
    "disabled": "#7a8491", "disabled_surface": "#edf0f3",
    "scroll_handle": "#b5bdc8", "scroll_hover": "#929dab", "shadow": "#7b8593",
    "canvas": "#e4e7eb",
}
_DARK = {
    "window": "#202226", "surface": "#282b30", "alternate": "#2d3036",
    "text": "#eef0f4", "secondary": "#b2bac6", "border": "#505762",
    "soft_border": "#3b414a", "button": "#30343b", "hover": "#3b424c",
    "pressed": "#454e5b", "accent": "#78b4ff", "primary": "#2869ba",
    "primary_hover": "#3177ca", "primary_pressed": "#205a9f",
    "selection": "#254b76", "selection_text": "#f3f6fa",
    "disabled": "#858f9e", "disabled_surface": "#292d33",
    "scroll_handle": "#596271", "scroll_hover": "#727e90", "shadow": "#131518",
    "canvas": "#17191c",
}

_STYLE = Template("""
QWidget {
  background: $window; color: $text; font-family: "Segoe UI"; font-size: 10pt;
}
QMainWindow, QDialog { background: $window; }
QFrame#panel { background: $window; border: none; }
QScrollArea#readerCanvas, QScrollArea#readerCanvas > QWidget > QWidget { background: $canvas; border: none; }
QWidget#emptyState { background: $canvas; }
QWidget#emptyColumn { background: transparent; }
QFrame#toolSection { background: $surface; border: 1px solid $soft_border; border-radius: 6px; }
QFrame#toolSection QWidget#sectionBody { background: $surface; }
QFrame#toolSection QWidget#sectionBody QLabel { color: $secondary; }
QPushButton#sectionHeader {
  background: transparent; border: none; padding: 8px 8px; font-weight: 600; text-align: left;
}
QPushButton#sectionHeader:hover { background: $hover; border-radius: 6px; }
QPushButton#sectionHeader:checked { background: transparent; color: $text; }
QLabel#emptyTitle { font-size: 16pt; font-weight: 600; }
QLabel#sectionLabel { color: $secondary; font-size: 9pt; font-weight: 600; }
QPushButton#recentFile {
  background: transparent; border: 1px solid transparent; border-radius: 6px;
  padding: 6px 10px; text-align: left; color: $text;
}
QPushButton#recentFile:hover { background: $hover; border-color: $soft_border; }
QListWidget#thumbnailList, QListWidget#thumbnailList::item, QListWidget#thumbnailList::item:selected,
QListWidget#thumbnailList::item:hover { background: $window; border: none; }
QLabel { background: transparent; border: none; }
QLabel#title { font-size: 12pt; font-weight: 600; }
QLabel#subtitle { color: $secondary; }
QWidget:disabled { color: $disabled; }
QPushButton {
  background: $button; color: $text; border: 1px solid $border;
  border-radius: 4px; padding: 5px 10px; min-height: 20px;
}
QPushButton:hover { background: $hover; border-color: $secondary; }
QPushButton:pressed { background: $pressed; }
QPushButton:checked { background: $selection; color: $selection_text; border-color: $accent; }
QPushButton:focus { border-color: $accent; }
QPushButton[primary="true"] {
  background: $primary; color: #ffffff; border-color: $primary; font-weight: 600;
}
QPushButton[primary="true"]:hover { background: $primary_hover; border-color: $primary_hover; }
QPushButton[primary="true"]:pressed { background: $primary_pressed; }
QPushButton[primary="true"]:focus { border: 2px solid #ffffff; padding: 4px 9px; }
QPushButton:disabled {
  background: $disabled_surface; color: $disabled; border-color: $soft_border;
}
QToolButton {
  background: transparent; color: $text; border: 1px solid transparent; border-radius: 4px;
  padding: 0; min-width: 27px; max-width: 30px; min-height: 27px; max-height: 30px;
}
QToolButton:hover { background: $hover; border-color: $border; }
QToolButton:pressed { background: $pressed; border-color: $border; }
QToolButton:checked { background: $selection; color: $selection_text; border-color: $accent; }
QToolButton:focus { border-color: $accent; }
QToolButton:disabled { background: transparent; color: $disabled; border-color: transparent; }
QLineEdit, QComboBox, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {
  background: $surface; color: $text; border: 1px solid $border; border-radius: 4px;
  padding: 5px 7px; selection-background-color: $selection; selection-color: $selection_text;
}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus { border-color: $accent; }
QLineEdit:disabled, QComboBox:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QSpinBox:disabled, QDoubleSpinBox:disabled {
  background: $disabled_surface; color: $disabled; border-color: $soft_border;
}
QComboBox { padding-right: 32px; min-height: 20px; }
QComboBox::drop-down {
  border-left: 1px solid $border; width: 26px; background: $alternate;
  border-top-right-radius: 3px; border-bottom-right-radius: 3px;
}
QComboBox::drop-down:hover { background: $hover; }
QComboBox::drop-down:disabled { background: $disabled_surface; border-color: $soft_border; }
QComboBox QAbstractItemView {
  background: $surface; color: $text; border: 1px solid $border; outline: 0;
  selection-background-color: $selection; selection-color: $selection_text;
}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
  background: $alternate; border-left: 1px solid $border; width: 18px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover { background: $hover; }
QMenuBar { background: $window; color: $text; padding: 2px; }
QMenuBar::item { background: transparent; padding: 4px 8px; }
QMenuBar::item:selected { background: $hover; border-radius: 3px; }
QMenuBar::item:pressed { background: $selection; color: $selection_text; }
QMenu {
  background: $surface; color: $text; border: 1px solid $border; padding: 4px;
}
QMenu::item { padding: 6px 28px 6px 24px; border-radius: 3px; }
QMenu::item:selected { background: $selection; color: $selection_text; }
QMenu::item:disabled { color: $disabled; }
QMenu::separator { height: 1px; background: $soft_border; margin: 4px 5px; }
QToolTip { background: $surface; color: $text; border: 1px solid $border; padding: 5px 7px; }
QTabWidget::pane { border: 1px solid $soft_border; background: $surface; }
QTabBar::tab {
  padding: 7px 10px; background: $window; color: $secondary;
  border: 1px solid transparent; border-bottom: 1px solid $soft_border;
}
QTabBar::tab:hover { background: $hover; color: $text; }
QTabBar::tab:selected {
  background: $surface; color: $text; border-color: $soft_border; border-bottom-color: $surface;
}
QTabBar::tab:disabled { color: $disabled; }
QListWidget, QListView, QTreeView, QTableView {
  background: $surface; color: $text; alternate-background-color: $alternate;
  border: 1px solid $soft_border; border-radius: 4px; outline: 0;
  selection-background-color: $selection; selection-color: $selection_text;
}
QListWidget::item, QListView::item { padding: 4px; }
QListWidget:focus, QListView:focus, QTreeView:focus, QTableView:focus { border-color: $accent; }
QListWidget::item:hover, QListView::item:hover { background: $hover; }
QListWidget::item:selected, QListView::item:selected { background: $selection; color: $selection_text; }
QListWidget::item:disabled, QListView::item:disabled { color: $disabled; }
QHeaderView::section {
  background: $alternate; color: $text; border: none; border-bottom: 1px solid $soft_border; padding: 5px;
}
QScrollArea { border: 1px solid $soft_border; background: $window; }
QStatusBar { background: $window; color: $secondary; border-top: 1px solid $soft_border; }
QStatusBar::item { border: none; }
QScrollBar:vertical { background: $window; width: 12px; margin: 0; }
QScrollBar:horizontal { background: $window; height: 12px; margin: 0; }
QScrollBar::handle:vertical { background: $scroll_handle; min-height: 24px; border-radius: 4px; margin: 2px; }
QScrollBar::handle:horizontal { background: $scroll_handle; min-width: 24px; border-radius: 4px; margin: 2px; }
QScrollBar::handle:hover { background: $scroll_hover; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: $window; }
QSplitter::handle:hover { background: $soft_border; }
QSplitter::handle:pressed { background: $border; }
QCheckBox, QRadioButton { background: transparent; spacing: 6px; }
QProgressBar {
  background: $surface; color: $text; border: 1px solid $border; border-radius: 3px; text-align: center;
}
QProgressBar::chunk { background: $selection; }
""")


# Existing callers can keep importing the neutral light stylesheet.
STYLE_SHEET = _STYLE.substitute(_LIGHT)


def resolve_theme(mode: str) -> str:
    """Resolve an explicit theme or the operating system's application preference."""
    mode = mode.strip().lower()
    if mode in ("light", "dark"):
        return mode
    if mode != "system":
        raise ValueError(f"Unsupported theme: {mode}")

    if QApplication.instance() is not None:
        scheme = QApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        if scheme == Qt.ColorScheme.Light:
            return "light"

    # Older Windows/Qt platform combinations report Unknown. Read the same
    # per-user preference once, without a helper process or background polling.
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "dark" if value == 0 else "light"
        except (ImportError, OSError):
            pass
    return "light"


def apply_theme(mode: str) -> str:
    """Apply colors to the existing application and return ``light`` or ``dark``."""
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("A QApplication must exist before applying a theme")
    resolved = resolve_theme(mode)
    colors = _DARK if resolved == "dark" else _LIGHT
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: "window", QPalette.ColorRole.WindowText: "text",
        QPalette.ColorRole.Base: "surface", QPalette.ColorRole.AlternateBase: "alternate",
        QPalette.ColorRole.Text: "text", QPalette.ColorRole.Button: "button",
        QPalette.ColorRole.ButtonText: "text", QPalette.ColorRole.Highlight: "selection",
        QPalette.ColorRole.HighlightedText: "selection_text", QPalette.ColorRole.Link: "accent",
        QPalette.ColorRole.LinkVisited: "accent", QPalette.ColorRole.ToolTipBase: "surface",
        QPalette.ColorRole.ToolTipText: "text", QPalette.ColorRole.PlaceholderText: "secondary",
        QPalette.ColorRole.Light: "hover", QPalette.ColorRole.Midlight: "alternate",
        QPalette.ColorRole.Mid: "border", QPalette.ColorRole.Dark: "soft_border",
        QPalette.ColorRole.Shadow: "shadow", QPalette.ColorRole.Accent: "accent",
    }
    for role, token in roles.items():
        palette.setColor(role, QColor(colors[token]))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    for role in (
        QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText, QPalette.ColorRole.Link, QPalette.ColorRole.LinkVisited,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled"]))
    for role in (QPalette.ColorRole.Base, QPalette.ColorRole.Button):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled_surface"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, QColor(colors["hover"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText, QColor(colors["disabled"]))
    if app.palette() != palette:
        app.setPalette(palette)
    stylesheet = _STYLE.substitute(colors)
    if app.styleSheet() != stylesheet:
        app.setStyleSheet(stylesheet)
    return resolved
