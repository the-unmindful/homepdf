import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
import fitz
from PySide6.QtWidgets import QApplication, QPushButton, QComboBox, QSpinBox, QScrollArea
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from pdf_ultimate.core import paths
from pdf_ultimate.ui.main_window import PdfUltimateMainWindow, SelectablePageLabel
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
        self.window.view_mode_combo.setCurrentText('Single')
        self.window._set_text_tool_mode('select')
        self.assertIs(self.window.page_scroll.widget(), self.window.page_stack)
        self.assertIs(self.window.page_stack.currentWidget(), self.window.page_image)
        self.assertTrue(self.window.page_image._page_words)

    def test_mode_selectors_are_visible_before_tools(self):
        for width in (1366, 1000, 760):
            self.window.resize(width, 768)
            self.app.processEvents()
            reader = self.window.view_mode_combo
            text = self.window.text_tool_combo
            tools = self.window.right_toggle_btn
            self.assertTrue(reader.isVisible())
            self.assertTrue(text.isVisible())
            self.assertLess(reader.mapTo(self.window, QPoint()).x(), text.mapTo(self.window, QPoint()).x())
            self.assertLess(text.mapTo(self.window, QPoint()).x(), tools.mapTo(self.window, QPoint()).x())
            for control in (reader, text, tools):
                self.assertGreaterEqual(control.width(), control.minimumSizeHint().width())

    def test_all_reader_text_combinations_preserve_reader_choice(self):
        self.open_reader()
        for layout in ('Single', 'Continuous'):
            self.window._set_text_tool_mode('view')
            self.window.view_mode_combo.setCurrentText(layout)
            for mode in ('select', 'convert', 'view'):
                with self.subTest(layout=layout, mode=mode):
                    self.window._set_text_tool_mode(mode)
                    self.assertEqual(self.window.view_mode_combo.currentText(), layout)
                    if mode == 'convert':
                        self.assertIs(self.window.page_scroll.widget(), self.window.page_stack)
                        self.assertIs(self.window.page_stack.currentWidget(), self.window.page_text_view)
                        self.assertFalse(self.window.view_mode_combo.isEnabled())
                    elif layout == 'Continuous':
                        self.assertIs(self.window.page_scroll.widget(), self.window.continuous_view)
                        self.assertEqual(self.window.continuous_view._selection_enabled, mode == 'select')
                    else:
                        self.assertIs(self.window.page_stack.currentWidget(), self.window.page_image)
                    if mode != 'convert':
                        self.assertTrue(self.window.view_mode_combo.isEnabled())

    def test_new_document_defaults_to_continuous_after_single_choice(self):
        self.open_reader()
        self.window.view_mode_combo.setCurrentText('Single')
        other = self.root / 'other.pdf'
        with fitz.open(self.source) as doc:
            doc.save(other)
        self.window._open_pdf(other, record_doc_history=False)
        self.assertEqual(self.window.view_mode, 'continuous')

    def test_selection_release_keeps_text_but_removes_drag_frame(self):
        label = SelectablePageLabel()
        label.resize(200, 100)
        label.set_page_words([(10, 10, 70, 30, 'selected', 0, 0, 0)], 1)
        label.set_selection_mode(True)
        label.show()
        try:
            QTest.mousePress(label, Qt.LeftButton, pos=QPoint(5, 5))
            QTest.mouseMove(label, QPoint(90, 40))
            QTest.mouseRelease(label, Qt.LeftButton, pos=QPoint(90, 40))
            self.assertEqual(label.selected_text(), 'selected')
            self.assertIsNone(label._drag_rect_page)
            QTest.keyClick(label, Qt.Key_C, Qt.ControlModifier)
            self.assertEqual(self.app.clipboard().text(), 'selected')
        finally:
            label.close()

    def wait_until(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertTrue(predicate())

    def test_search_survives_every_mode_combination_and_query_change(self):
        self.open_reader()
        self.window.search_input.setText('Reader test')
        self.wait_until(lambda: len(self.window.search_sequence) == 40)
        for layout in ('Single', 'Continuous'):
            self.window._set_text_tool_mode('view')
            self.window.view_mode_combo.setCurrentText(layout)
            for mode in ('view', 'select', 'convert'):
                with self.subTest(layout=layout, mode=mode):
                    self.window._set_text_tool_mode(mode)
                    if mode == 'convert':
                        self.wait_until(lambda: len(self.window._text_search_spans) == 40)
                        self.assertEqual(self.window.page_text_view.textCursor().selectedText(), 'Reader test')
                    else:
                        self.wait_until(lambda: len(self.window.search_sequence) == 40)
                    self.window._search_next()
                    self.assertIn('2 / 40', self.window.search_result_label.text())
                    self.window._search_prev()
                    self.assertEqual(self.window.search_result_label.text(), '1 / 40')
        self.window.search_input.setText('missing phrase')
        self.wait_until(lambda: self.window.search_result_label.text() == '0 / 0')
        self.window._set_text_tool_mode('view')
        self.wait_until(lambda: not self.window.search_jobs.busy and not self.window.search_sequence)
        self.assertEqual(self.window.search_result_label.text(), '0 / 0')

    def test_continuous_selection_copies_without_loading_all_pages(self):
        self.open_reader()
        self.window._set_text_tool_mode('select')
        view = self.window.continuous_view
        rect = view._page_rects[0]
        start = rect.topLeft() + QPoint(int(45 * self.window.zoom_factor), int(45 * self.window.zoom_factor))
        end = rect.topLeft() + QPoint(int(240 * self.window.zoom_factor), int(65 * self.window.zoom_factor))
        QTest.mousePress(view, Qt.LeftButton, pos=start)
        QTest.mouseMove(view, end)
        QTest.mouseRelease(view, Qt.LeftButton, pos=end)
        self.assertIn('Reader test page 1', view.selected_text())
        self.assertIsNone(view._drag_rect_page)
        QTest.keyClick(view, Qt.Key_C, Qt.ControlModifier)
        self.assertIn('Reader test page 1', self.app.clipboard().text())

    def test_zoom_dropdown_has_fit_and_actual_size_with_active_state(self):
        self.open_reader()
        labels = [self.window.zoom_combo.itemText(i) for i in range(self.window.zoom_combo.count())]
        self.assertIn('Fit Width', labels)
        self.assertIn('Fit Page', labels)
        self.assertIn('Actual Size (100%)', labels)
        self.window.zoom_combo.setCurrentText('Fit Width')
        self.window._on_zoom_combo_changed()
        self.assertEqual(self.window.fit_mode, 'width')
        self.assertTrue(self.window.fit_width_btn.isChecked())
        self.window.zoom_combo.setCurrentText('Actual Size (100%)')
        self.window._on_zoom_combo_changed()
        self.assertEqual(self.window.zoom_factor, 1)
        self.assertEqual(self.window.fit_mode, 'manual')
        self.assertIn('100%', self.window.zoom_combo.currentText())
        self.window.zoom_combo.setCurrentText('Fit Page')
        self.window._on_zoom_combo_changed()
        self.assertEqual(self.window.fit_mode, 'page')
        self.assertEqual(self.window.zoom_combo.currentIndex(), 1)
        self.window._actual_size()
        self.assertEqual(self.window.zoom_combo.currentIndex(), 2)

    def test_new_document_in_extracted_text_updates_page_navigation(self):
        self.open_reader()
        self.window._set_page(39)
        self.window._set_text_tool_mode('convert')
        other = self.root / 'short.pdf'
        with fitz.open(self.source) as doc:
            doc.select([0, 1, 2])
            doc.save(other)
        self.window._open_pdf(other, record_doc_history=False)
        self.assertEqual(self.window.page_label.text(), 'Page 1 / 3')

    def test_selection_maps_rotated_page_words_in_both_layouts(self):
        rotated = self.root / 'rotated.pdf'
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((50, 60), 'Rotated')
            page.set_rotation(90)
            doc.save(rotated)
        self.window._open_pdf(rotated, record_doc_history=False)
        for layout in ('Single', 'Continuous'):
            self.window.view_mode_combo.setCurrentText(layout)
            self.window._set_text_tool_mode('select')
            target = self.window.page_image if layout == 'Single' else self.window.continuous_view
            page = self.window.current_doc[0]
            word = page.get_text('words')[0]
            rect = fitz.Rect(word[:4]) * page.rotation_matrix
            origin = target._pixmap_rect().topLeft() if layout == 'Single' else target._page_rects[0].topLeft()
            start = origin + QPoint(int((rect.x0 - 2) * self.window.zoom_factor), int((rect.y0 - 2) * self.window.zoom_factor))
            end = origin + QPoint(int((rect.x1 + 2) * self.window.zoom_factor), int((rect.y1 + 2) * self.window.zoom_factor))
            QTest.mousePress(target, Qt.LeftButton, pos=start)
            QTest.mouseRelease(target, Qt.LeftButton, pos=end)
            self.assertEqual(target.selected_text(), 'Rotated')

    def test_both_panels_leave_controls_visible_and_panes_have_toggle_state(self):
        self.open_reader()
        self.window.resize(1366, 768)
        self.window._toggle_right_panel()
        self.app.processEvents()
        self.assertTrue(self.window.left_toggle_btn.isChecked())
        self.assertTrue(self.window.right_toggle_btn.isChecked())
        self.assertGreater(self.window.page_scroll.viewport().width(), 650)
        for control in (self.window.view_mode_combo, self.window.text_tool_combo, self.window.zoom_combo):
            self.assertTrue(control.isVisible())
            self.assertGreaterEqual(control.width(), control.minimumSizeHint().width())
        self.window._toggle_right_panel()
        self.assertFalse(self.window.right_toggle_btn.isChecked())

    def test_opening_panes_at_narrow_width_keeps_reader_controls_readable(self):
        for width in (760, 1000):
            self.window.resize(width, 768)
            self.window.main_splitter.setSizes([0, width, 0])
            self.app.processEvents()
            for toggle in (self.window._toggle_left_panel, self.window._toggle_right_panel,
                           self.window._toggle_left_panel):
                toggle()
                self.app.processEvents()
                toolbar = self.window.toolbar_row
                for control in (self.window.view_mode_combo, self.window.text_tool_combo,
                                self.window.zoom_combo, self.window.find_btn, self.window.right_toggle_btn):
                    self.assertGreaterEqual(control.width(), control.minimumSizeHint().width())
                    origin = control.mapTo(toolbar, QPoint())
                    self.assertGreaterEqual(origin.x(), 0)
                    self.assertLessEqual(origin.x() + control.width(), toolbar.width())

    def test_dragging_panes_cannot_squeeze_mode_selectors(self):
        self.window.resize(760, 768)
        self.app.processEvents()
        for sizes in ([180, 130, 418], [0, 128, 600], [570, 158, 0]):
            self.window.main_splitter.setSizes(sizes)
            self.window._on_layout_changed()
            self.app.processEvents()
            for control in (self.window.view_mode_combo, self.window.text_tool_combo):
                self.assertGreaterEqual(control.width(), control.minimumSizeHint().width())

    def test_fit_width_removes_horizontal_scrolling_with_both_panes(self):
        self.open_reader()
        self.window._toggle_right_panel()
        self.app.processEvents()
        self.window._fit_width()
        self.app.processEvents()
        self.assertEqual(self.window.page_scroll.horizontalScrollBar().maximum(), 0)

    def test_theme_menu_persists_choice_and_preserves_reading(self):
        self.open_reader()
        self.window._set_page(5)
        before = self.window.current_pdf
        self.window.theme_actions['dark'].trigger()
        self.assertEqual(self.window.state_store.get_ui('theme'), 'dark')
        self.assertTrue(self.window.theme_actions['dark'].isChecked())
        self.assertEqual(self.window.current_page_index, 5)
        self.assertEqual(self.window.current_pdf, before)
        self.window.theme_actions['light'].trigger()
        self.assertTrue(self.window.theme_actions['light'].isChecked())

    def test_extracted_text_search_keeps_unicode_cursor_positions(self):
        self.open_reader()
        self.window._set_text_tool_mode('convert')
        self.wait_until(lambda: not self.window.text_jobs.busy)
        self.window.page_text_view.setPlainText('Before \U0001f600 needle after')
        self.window._execute_text_search('needle')
        self.assertEqual(self.window.page_text_view.textCursor().selectedText(), 'needle')

    def test_pending_text_extraction_search_restarts_after_completion(self):
        self.open_reader()
        self.window._set_text_tool_mode('convert')
        self.window.search_input.setText('Reader test')
        self.window._execute_search()
        self.wait_until(lambda: len(self.window._text_search_spans) == 40)
        self.assertEqual(self.window.search_result_label.text(), '1 / 40')
        self.window._set_text_tool_mode('view')
        self.wait_until(lambda: not self.window.search_jobs.busy and len(self.window.search_sequence) == 40)
        self.assertEqual(self.window.search_result_label.text(), '1 / 40')

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
        self.open_reader()
        self.assertGreaterEqual(self.window.page_scroll.viewport().width(), 750)
        self.assertEqual(self.window.main_splitter.sizes()[2], 0)
        for widget in self.window.toolbar_row.findChildren(QPushButton):
            self.assertGreaterEqual(widget.width(), widget.minimumSizeHint().width())

    def test_tall_tool_forms_are_scrollable(self):
        for index in range(self.window.tools_stack.count()):
            self.assertIsInstance(self.window.tools_stack.widget(index), QScrollArea)

    def test_empty_state_replaces_reader_until_a_document_opens(self):
        self.assertIs(self.window.reader_stack.currentWidget(), self.window.empty_state)
        self.assertFalse(self.window.toolbar_row.isEnabled())
        self.open_reader()
        self.assertIs(self.window.reader_stack.currentWidget(), self.window.body_split)
        self.assertTrue(self.window.toolbar_row.isEnabled())
        self.assertIn('reader.pdf', self.window.windowTitle())

    def test_tool_page_fields_start_empty_and_fall_back_to_thumbnail_selection(self):
        self.open_reader()
        for field in (self.window.extract_selection_input, self.window.delete_selection_input,
                      self.window.rotate_selection_input, self.window.reorder_input):
            self.assertEqual(field.text(), '')
        with self.assertRaises(Exception):
            self.window._page_selection_text(self.window.delete_selection_input)
        self.assertEqual(self.window._page_selection_text(self.window.rotate_selection_input, default_all=True), '1-')
        self.window.thumbnail_list.item(2).setSelected(True)
        self.window.thumbnail_list.item(4).setSelected(True)
        self.assertEqual(self.window._page_selection_text(self.window.delete_selection_input), '3,5')

    def test_zoom_keeps_document_point_under_cursor(self):
        self.open_reader()
        self.window._set_page(3)
        self.app.processEvents()
        view, vbar, hbar = self.window.continuous_view, self.window.page_scroll.verticalScrollBar(), self.window.page_scroll.horizontalScrollBar()
        pos = QPoint(200, 150)

        def doc_point():
            y = vbar.value() + pos.y()
            row = view.page_at_offset(y)
            rect = view._page_rects[row]
            return row, (hbar.value() + pos.x() - rect.left()) / view._zoom, (y - rect.top()) / view._zoom

        before = doc_point()
        self.window._zoom_at(1.6, pos)
        self.app.processEvents()
        after = doc_point()
        self.assertEqual(before[0], after[0])
        self.assertAlmostEqual(before[1], after[1], delta=2)
        self.assertAlmostEqual(before[2], after[2], delta=2)

    def test_zoom_steps_land_on_standard_levels(self):
        self.open_reader()
        self.window._actual_size()
        self.window._step_zoom(1)
        self.assertAlmostEqual(self.window.zoom_factor, 1.1)
        self.window._step_zoom(-1)
        self.window._step_zoom(-1)
        self.assertAlmostEqual(self.window.zoom_factor, 0.9)

    def test_reload_when_file_changes_on_disk(self):
        self.open_reader()
        self.assertEqual(self.window.current_doc.page_count, 40)
        doc = fitz.open()
        for i in range(3):
            doc.new_page().insert_text((50, 60), f'Rewritten {i}')
        doc.save(self.source)  # the displayed file is not locked
        doc.close()
        self.window._reload_if_changed()
        self.assertEqual(self.window.current_doc.page_count, 3)

    def test_print_renders_requested_pages(self):
        from PySide6.QtPrintSupport import QPrinter
        self.open_reader()
        out = self.root / 'printed.pdf'
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(str(out))
        self.assertEqual(self.window._render_to_printer(printer, [0, 1, 5]), 3)
        with fitz.open(out) as printed:
            self.assertEqual(printed.page_count, 3)

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
