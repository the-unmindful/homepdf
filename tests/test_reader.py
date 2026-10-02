import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
import fitz
from PySide6.QtWidgets import QApplication, QPushButton, QComboBox, QSpinBox, QScrollArea
from PySide6.QtGui import QFontDatabase, QFont
from pdf_ultimate.core import paths
from pdf_ultimate.ui.main_window import PdfUltimateMainWindow
from pdf_ultimate.ui.theme import STYLE_SHEET


class IdleExecutor:
    def submit(self, *args, **kwargs):
        return Future()


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for name in ['segoeui.ttf', 'segoeuib.ttf']:
            path = Path('C:/Windows/Fonts') / name
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
        cls.app.setFont(QFont('Segoe UI', 10))
        cls.app.setStyle('Fusion')
        cls.app.setStyleSheet(STYLE_SHEET.replace('"Segoe UI Variable", "Segoe UI", "Bahnschrift", sans-serif', '"Segoe UI"'))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'HOME_PDF_APP_ROOT': str(self.root / 'state')})
        self.env.start()
        paths.app_root.cache_clear()
        self.pool = patch('pdf_ultimate.core.render_service.get_process_pool', return_value=IdleExecutor())
        self.pool.start()
        self.source = self.root / 'reader.pdf'
        doc = fitz.open()
        for i in range(40):
            page = doc.new_page(width=595, height=842)
            page.insert_text((50, 60), f'Reader test page {i+1}')
        doc.save(self.source)
        self.locked = self.root / 'locked.pdf'
        doc.select([0])
        doc.save(self.locked, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw='secret', owner_pw='owner')
        doc.close()
        self.window = PdfUltimateMainWindow()
        self.errors = []
        self.window._show_error = lambda error: self.errors.append(str(error))
        self.window.resize(1366, 768)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.pool.stop()
        self.env.stop()
        paths.app_root.cache_clear()
        self.temp.cleanup()

    def open_reader(self):
        self.assertTrue(self.window._open_pdf(self.source, record_doc_history=False))
        self.app.processEvents()

    def test_select_mode_attaches_actual_single_view(self):
        self.open_reader()
        self.window._set_text_tool_mode('select')
        self.assertIs(self.window.page_scroll.widget(), self.window.page_stack)
        self.assertIs(self.window.page_stack.currentWidget(), self.window.page_image)
        self.assertTrue(self.window.page_image._page_words)

    def test_text_mode_attaches_text_view(self):
        self.open_reader()
        self.window._set_text_tool_mode('convert')
        self.assertIs(self.window.page_scroll.widget(), self.window.page_stack)
        self.assertIs(self.window.page_stack.currentWidget(), self.window.page_text_view)

    def test_locked_pdf_clears_old_geometry_and_displays_lock(self):
        self.open_reader()
        self.window._open_pdf(self.locked, record_doc_history=False)
        self.assertEqual(self.window.page_order, [])
        self.assertEqual(self.window.continuous_view.page_count(), 0)
        self.assertEqual(self.window.page_jump_spin.maximum(), 1)
        self.assertIs(self.window.page_scroll.widget(), self.window.page_stack)
        self.assertIn('locked', self.window.page_image.text().lower())

    def test_unreadable_pdf_clears_closed_document_state(self):
        self.open_reader()
        bad = self.root / 'broken.pdf'
        bad.write_bytes(b'not a PDF')
        self.assertFalse(self.window._open_pdf(bad, record_doc_history=False))
        self.assertIsNone(self.window.current_doc)
        self.assertIsNone(self.window.current_pdf)
        self.assertEqual(self.window.page_order, [])
        self.assertTrue(self.window._open_pdf(self.source, record_doc_history=False))

    def test_zoom_preserves_position_within_page(self):
        self.open_reader()
        self.window.main_splitter.setSizes([0, 1300, 0])
        self.window.body_split.setSizes([0, 1250])
        self.app.processEvents()
        self.window._fit_width()
        self.window._set_page(10)
        bar = self.window.page_scroll.verticalScrollBar()
        bar.setValue(self.window.continuous_view.page_top(10) + 300)
        zoom = self.window.zoom_factor
        self.window._change_zoom(1.15)
        self.assertEqual(self.window.current_page_index, 10)
        offset = bar.value() - self.window.continuous_view.page_top(10)
        self.assertAlmostEqual(offset / self.window.zoom_factor, 300 / zoom, delta=2)

    def test_tab_shortcuts_and_nonmodal_completion(self):
        from PySide6.QtWidgets import QMessageBox
        shortcuts = {action.shortcut().toString() for action in self.window.actions()}
        self.assertIn("Ctrl+Tab", shortcuts)
        self.assertIn("Ctrl+Shift+Tab", shortcuts)
        with patch.object(QMessageBox, "information") as dialog:
            self.window._show_result([self.source.with_suffix('.txt')], 'Saved.')
            dialog.assert_not_called()
        self.assertTrue(self.window.result_folder_button.isVisible())

    def test_ocr_tuning_is_collapsed_and_logs_are_bounded(self):
        self.assertTrue(self.window.ocr_advanced.isHidden())
        self.assertFalse(self.window.ocr_live_terminal_box.isChecked())
        self.window._append_ocr_log("line\n" * 3000)
        self.assertLessEqual(self.window.ocr_log_view.document().blockCount(), 1000)
        self.window._set_ocr_busy(True)
        self.assertFalse(self.window.ocr_run_button.isEnabled())
        self.assertTrue(self.window.ocr_cancel_button.isEnabled())
        self.window._reset_ocr_job_tracking()
        self.assertTrue(self.window.ocr_run_button.isEnabled())
        self.assertFalse(self.window.ocr_cancel_button.isEnabled())

    def test_close_cancels_owned_ocr(self):
        with patch.object(self.window, '_cancel_ocr') as cancel:
            self.window.close()
            cancel.assert_called_once()

    def test_failed_worker_start_does_not_leave_disabled_tools(self):
        def fail(*_args, **_kwargs):
            self.window.tool_jobs.failed.emit('Failed to start')
        with patch.object(self.window.tool_jobs, 'start', side_effect=fail):
            self.window._submit_tool('compress', self.source, self.root / 'out.pdf')
        self.assertTrue(self.window.tools_stack.isEnabled())
        self.assertTrue(self.window.job_cancel_button.isHidden())
        self.assertIsNone(self.window._tool_callback)

    def test_fit_large_page_allows_scale_below_manual_minimum(self):
        path = self.root / 'a0.pdf'
        with fitz.open() as doc:
            doc.new_page(width=2384, height=3370); doc.save(path)
        self.window._open_pdf(path, record_doc_history=False)
        self.window._fit_page(); self.app.processEvents()
        page_height = 3370 * self.window.zoom_factor
        self.assertLessEqual(page_height, self.window.page_scroll.viewport().height() - 12)

    @unittest.skipUnless(os.name == 'nt', 'Windows process tree ownership')
    def test_close_terminates_ocr_descendants(self):
        from PySide6.QtCore import QProcess
        import subprocess, time
        proc = QProcess(self.window)
        command = "import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(child.pid,flush=True); time.sleep(60)"
        proc.start(sys.executable, ['-c', command])
        self.assertTrue(proc.waitForStarted(3000))
        output = b''; deadline = time.monotonic() + 5
        while b'\n' not in output and time.monotonic() < deadline:
            self.app.processEvents(); output += bytes(proc.readAllStandardOutput()); time.sleep(.01)
        child_pid = int(output.strip())
        self.window._ocr_process = proc
        self.window._ocr_job_active = True
        proc.finished.connect(self.window._on_ocr_job_finished)
        try:
            self.window.close()
            result = subprocess.run(['tasklist.exe', '/FI', f'PID eq {child_pid}', '/FO', 'CSV', '/NH'], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertNotIn(f'"{child_pid}"', result.stdout)
        finally:
            if proc.state() != QProcess.NotRunning: proc.kill(); proc.waitForFinished(1000)
            subprocess.run(['taskkill.exe', '/PID', str(child_pid), '/T', '/F'], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

    def test_fit_page_fits_height_in_continuous_mode(self):
        self.open_reader()
        self.window.main_splitter.setSizes([0, 1300, 0])
        self.app.processEvents()
        self.window._fit_page()
        self.assertEqual(self.window.fit_mode, 'page')
        self.assertLessEqual(842 * self.window.zoom_factor, self.window.page_scroll.viewport().height())

    def test_default_layout_prioritizes_reading_at_laptop_width(self):
        self.assertGreaterEqual(self.window.page_scroll.viewport().width(), 750)
        self.assertEqual(self.window.main_splitter.sizes()[2], 0)
        for widget in self.window.toolbar_row.findChildren(QPushButton):
            self.assertGreaterEqual(widget.width(), widget.minimumSizeHint().width())

    def test_tall_tool_forms_are_scrollable(self):
        for index in range(self.window.tools_stack.count()):
            self.assertIsInstance(self.window.tools_stack.widget(index), QScrollArea)

    def test_navigation_switches_outline_and_thumbnails_in_one_pane(self):
        self.assertIs(self.window.thumbnail_list.parentWidget(), self.window.navigation_stack)
        self.assertEqual(self.window.body_split.sizes()[0], 0)

    def test_narrow_window_preserves_essential_control_sizes(self):
        self.window.resize(1000, 700)
        self.app.processEvents()
        for widget in self.window.toolbar_row.findChildren(QPushButton):
            self.assertGreaterEqual(widget.width(), widget.minimumSizeHint().width())


if __name__ == '__main__':
    unittest.main()
