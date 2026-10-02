import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desktop"))

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QTextEdit, QToolButton

from pdf_ultimate.ui import theme


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous_palette = self.app.palette()
        self.previous_stylesheet = self.app.styleSheet()

    def tearDown(self):
        self.app.setPalette(self.previous_palette)
        self.app.setStyleSheet(self.previous_stylesheet)

    def test_explicit_modes_do_not_consult_system_preference(self):
        with patch.object(QApplication, "styleHints", side_effect=AssertionError("system queried")):
            self.assertEqual(theme.apply_theme("light"), "light")
            self.assertEqual(theme.apply_theme("dark"), "dark")

    def test_applying_current_theme_skips_global_palette_and_stylesheet_updates(self):
        theme.apply_theme("dark")
        with patch.object(self.app, "setPalette", wraps=self.app.setPalette) as set_palette, patch.object(
            self.app, "setStyleSheet", wraps=self.app.setStyleSheet
        ) as set_stylesheet:
            self.assertEqual(theme.apply_theme("dark"), "dark")
            set_palette.assert_not_called()
            set_stylesheet.assert_not_called()

    def test_external_palette_change_is_corrected_without_reapplying_stylesheet(self):
        theme.apply_theme("dark")
        original = self.app.palette()
        changed = QPalette(original)
        changed.setColor(QPalette.ColorRole.WindowText, QColor("#ff0000"))
        self.app.setPalette(changed)
        with patch.object(self.app, "setStyleSheet", wraps=self.app.setStyleSheet) as set_stylesheet:
            theme.apply_theme("dark")
            self.assertEqual(self.app.palette(), original)
            set_stylesheet.assert_not_called()

    def test_external_stylesheet_change_is_corrected_for_current_theme(self):
        theme.apply_theme("dark")
        original = self.app.styleSheet()
        self.app.setStyleSheet("QWidget { color: #ff0000; }")
        theme.apply_theme("dark")
        self.assertEqual(self.app.styleSheet(), original)

    def test_dark_palette_covers_custom_painting_and_disabled_text(self):
        theme.apply_theme("dark")
        palette = self.app.palette()
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
            self.assertLess(palette.color(role).lightness(), 80)
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
            self.assertGreater(palette.color(role).lightness(), 190)
            disabled = palette.color(QPalette.ColorGroup.Disabled, role)
            self.assertLess(disabled.lightness(), palette.color(role).lightness())
            self.assertGreater(disabled.lightness(), palette.color(QPalette.ColorRole.Base).lightness())

    def test_switching_back_to_light_updates_existing_text_widgets(self):
        text = QTextEdit()
        try:
            text.show()
            self.app.processEvents()
            theme.apply_theme("dark")
            self.assertLess(text.palette().color(QPalette.ColorRole.Base).lightness(), 80)
            theme.apply_theme("light")
            self.assertGreater(text.palette().color(QPalette.ColorRole.Base).lightness(), 220)
            self.assertLess(text.palette().color(QPalette.ColorRole.Text).lightness(), 80)
        finally:
            text.deleteLater()
            self.app.processEvents()

    def test_icon_toolbar_controls_fit_their_32_pixel_bounds_in_both_themes(self):
        button = QToolButton()
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setFixedSize(32, 32)
        button.show()
        try:
            for mode in ("light", "dark"):
                with self.subTest(mode=mode):
                    theme.apply_theme(mode)
                    self.app.processEvents()
                    self.assertEqual(button.width(), 32)
                    self.assertEqual(button.height(), 32)
                    self.assertLessEqual(button.minimumSizeHint().width(), button.width())
                    self.assertLessEqual(button.minimumSizeHint().height(), button.height())
        finally:
            button.close()
            button.deleteLater()
            self.app.processEvents()

    def test_system_uses_known_qt_scheme_before_windows_registry(self):
        for scheme, expected in ((Qt.ColorScheme.Dark, "dark"), (Qt.ColorScheme.Light, "light")):
            with self.subTest(scheme=scheme), patch.object(
                QApplication, "styleHints", return_value=SimpleNamespace(colorScheme=lambda: scheme)
            ), patch.dict(sys.modules, {"winreg": None}), patch.object(theme.sys, "platform", "win32"):
                self.assertEqual(theme.resolve_theme("system"), expected)

    def test_unknown_qt_scheme_reads_windows_apps_use_light_theme(self):
        for value, expected in ((0, "dark"), (1, "light")):
            key = MagicMock()
            key.__enter__.return_value = key
            registry = SimpleNamespace(
                HKEY_CURRENT_USER=object(), OpenKey=MagicMock(return_value=key),
                QueryValueEx=MagicMock(return_value=(value, 4)),
            )
            with self.subTest(value=value), patch.object(
                QApplication, "styleHints", return_value=SimpleNamespace(colorScheme=lambda: Qt.ColorScheme.Unknown)
            ), patch.dict(sys.modules, {"winreg": registry}), patch.object(theme.sys, "platform", "win32"):
                self.assertEqual(theme.apply_theme("system"), expected)
                self.assertEqual(self.app.palette().color(QPalette.ColorRole.Window).lightness() < 80, value == 0)
                registry.OpenKey.assert_called_once_with(
                    registry.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                )
                registry.QueryValueEx.assert_called_once_with(key, "AppsUseLightTheme")

    def test_unknown_scheme_and_missing_windows_setting_default_to_light(self):
        registry = SimpleNamespace(HKEY_CURRENT_USER=object(), OpenKey=MagicMock(side_effect=OSError))
        with patch.object(
            QApplication, "styleHints", return_value=SimpleNamespace(colorScheme=lambda: Qt.ColorScheme.Unknown)
        ), patch.dict(sys.modules, {"winreg": registry}), patch.object(theme.sys, "platform", "win32"):
            self.assertEqual(theme.resolve_theme("system"), "light")

    def test_unknown_scheme_on_other_platform_defaults_to_light(self):
        with patch.object(
            QApplication, "styleHints", return_value=SimpleNamespace(colorScheme=lambda: Qt.ColorScheme.Unknown)
        ), patch.object(theme.sys, "platform", "linux"):
            self.assertEqual(theme.resolve_theme("system"), "light")

    def test_unsupported_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            theme.apply_theme("sepia")


if __name__ == "__main__":
    unittest.main()
