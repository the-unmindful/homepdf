from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import os
import shutil
import subprocess
import sys

import fitz
from PySide6.QtCore import QByteArray, QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, Signal, QProcess, QProcessEnvironment, QUrl
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QImage, QKeySequence, QPainter, QPixmap, QTextCursor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pdf_ultimate.core.paths import output_root
from pdf_ultimate.core.pdf_tools import PdfToolkit, PdfToolkitError, ProtectOptions
from pdf_ultimate.core.process_pool import get_process_pool
from pdf_ultimate.core.worker_tasks import SearchResult, search_pdf_text, bounded_scale
from pdf_ultimate.core.render_service import PdfRenderService
from pdf_ultimate.core.state_store import AppStateStore, DocumentViewState
from pdf_ultimate.ui.continuous_view import ContinuousPageView


class FileDropListWidget(QListWidget):
    filesDropped = Signal(list)
    reordered = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.model().rowsMoved.connect(self._emit_reordered)

    def _emit_reordered(self, *_args) -> None:
        self.reordered.emit()

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            files = [url for url in event.mimeData().urls() if url.isLocalFile()]
            if files:
                event.acceptProposedAction()
                return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            files = [url for url in event.mimeData().urls() if url.isLocalFile()]
            if files:
                event.acceptProposedAction()
                return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            files = [
                Path(url.toLocalFile())
                for url in event.mimeData().urls()
                if url.isLocalFile()
            ]
            if files:
                self.filesDropped.emit(files)
                event.acceptProposedAction()
                return
        super().dropEvent(event)
        self.reordered.emit()


class _AsyncBridge(QObject):
    """
    Bridges callbacks from non-Qt threads into the Qt event loop using signals.

    We use this for ProcessPoolExecutor done-callbacks (which run in a Python thread).
    """
    searchCompleted = Signal(object)  # payload: (job_id:int, doc_token:str, query:str, SearchResult)
    searchFailed = Signal(object)     # payload: (job_id:int, doc_token:str, query:str, error_message:str)



class SelectablePageLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._selection_enabled = False
        self._zoom = 1.0
        self._page_words: list[tuple[float, float, float, float, str, int, int, int]] = []
        self._selected_word_indexes: set[int] = set()
        self._selected_text = ""
        self._drag_start_page: tuple[float, float] | None = None
        self._drag_rect_page: tuple[float, float, float, float] | None = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

    def set_selection_mode(self, enabled: bool) -> None:
        self._selection_enabled = bool(enabled)
        if not self._selection_enabled:
            self.clear_selection()
        self.update()

    def set_page_words(self, words: list[tuple], zoom: float) -> None:
        normalized: list[tuple[float, float, float, float, str, int, int, int]] = []
        for word in words:
            if len(word) < 8:
                continue
            normalized.append(
                (
                    float(word[0]),
                    float(word[1]),
                    float(word[2]),
                    float(word[3]),
                    str(word[4]),
                    int(word[5]),
                    int(word[6]),
                    int(word[7]),
                )
            )
        self._page_words = normalized
        self._zoom = max(0.001, float(zoom))
        self.clear_selection()

    def clear_selection(self) -> None:
        self._selected_word_indexes.clear()
        self._selected_text = ""
        self._drag_start_page = None
        self._drag_rect_page = None
        self.update()

    def selected_text(self) -> str:
        return self._selected_text

    def _pixmap_rect(self) -> QRect:
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            return QRect(0, 0, self.width(), self.height())
        logical = pixmap.deviceIndependentSize().toSize()
        width = max(1, logical.width())
        height = max(1, logical.height())
        x = 0
        y = 0
        alignment = self.alignment()
        if alignment & Qt.AlignHCenter:
            x = (self.width() - width) // 2
        elif alignment & Qt.AlignRight:
            x = self.width() - width
        if alignment & Qt.AlignVCenter:
            y = (self.height() - height) // 2
        elif alignment & Qt.AlignBottom:
            y = self.height() - height
        return QRect(x, y, width, height)

    def _point_to_page(self, point: QPoint, *, clamp: bool = False) -> tuple[float, float] | None:
        area = self._pixmap_rect()
        if area.width() <= 0 or area.height() <= 0:
            return None
        x = point.x()
        y = point.y()
        if clamp:
            x = max(area.left(), min(area.left() + area.width() - 1, x))
            y = max(area.top(), min(area.top() + area.height() - 1, y))
        elif not area.contains(point):
            return None
        return ((x - area.left()) / self._zoom, (y - area.top()) / self._zoom)

    @staticmethod
    def _rect_intersects(
        a: tuple[float, float, float, float],
        b: tuple[float, float, float, float],
    ) -> bool:
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        return not (ax1 < bx0 or ax0 > bx1 or ay1 < by0 or ay0 > by1)

    def _word_indexes_for_rect(self, rect: tuple[float, float, float, float]) -> set[int]:
        x0, y0, x1, y1 = rect
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        drag_rect = (x0, y0, x1, y1)
        result: set[int] = set()
        for idx, (wx0, wy0, wx1, wy1, _text, _block, _line, _word) in enumerate(self._page_words):
            if self._rect_intersects(drag_rect, (wx0, wy0, wx1, wy1)):
                result.add(idx)
        return result

    def _build_selected_text(self, indexes: set[int]) -> str:
        if not indexes:
            return ""
        ordered = sorted((self._page_words[idx] for idx in indexes), key=lambda item: (item[5], item[6], item[7]))
        parts: list[str] = []
        prev_block: int | None = None
        prev_line: int | None = None
        for _x0, _y0, _x1, _y1, text, block, line, _word in ordered:
            if prev_block is None:
                parts.append(text)
            elif block != prev_block:
                parts.append("\n\n")
                parts.append(text)
            elif line != prev_line:
                parts.append("\n")
                parts.append(text)
            else:
                parts.append(" ")
                parts.append(text)
            prev_block = block
            prev_line = line
        return "".join(parts).strip()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if not self._selection_enabled or event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        page_point = self._point_to_page(event.position().toPoint(), clamp=False)
        if page_point is None:
            self.clear_selection()
            event.accept()
            return
        self.setFocus(Qt.MouseFocusReason)
        self._drag_start_page = page_point
        self._drag_rect_page = (page_point[0], page_point[1], page_point[0], page_point[1])
        self._selected_word_indexes = self._word_indexes_for_rect(self._drag_rect_page)
        self._selected_text = self._build_selected_text(self._selected_word_indexes)
        self.update()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if not self._selection_enabled or self._drag_start_page is None:
            super().mouseMoveEvent(event)
            return
        page_point = self._point_to_page(event.position().toPoint(), clamp=True)
        if page_point is None:
            return
        sx, sy = self._drag_start_page
        self._drag_rect_page = (sx, sy, page_point[0], page_point[1])
        self._selected_word_indexes = self._word_indexes_for_rect(self._drag_rect_page)
        self._selected_text = self._build_selected_text(self._selected_word_indexes)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if not self._selection_enabled or event.button() != Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self._drag_start_page is None:
            return
        page_point = self._point_to_page(event.position().toPoint(), clamp=True)
        if page_point is not None:
            sx, sy = self._drag_start_page
            self._drag_rect_page = (sx, sy, page_point[0], page_point[1])
            self._selected_word_indexes = self._word_indexes_for_rect(self._drag_rect_page)
            self._selected_text = self._build_selected_text(self._selected_word_indexes)
        self._drag_start_page = None
        self.update()
        event.accept()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if self._selection_enabled and event.matches(QKeySequence.Copy):
            if self._selected_text:
                QApplication.clipboard().setText(self._selected_text)
                event.accept()
                return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if not self._selection_enabled:
            return
        if not self._selected_word_indexes and self._drag_rect_page is None:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        area = self._pixmap_rect()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(76, 141, 247, 88))
        for idx in self._selected_word_indexes:
            if idx < 0 or idx >= len(self._page_words):
                continue
            x0, y0, x1, y1, _text, _block, _line, _word = self._page_words[idx]
            x = area.left() + int(x0 * self._zoom)
            y = area.top() + int(y0 * self._zoom)
            w = max(2, int((x1 - x0) * self._zoom))
            h = max(2, int((y1 - y0) * self._zoom))
            painter.drawRect(x, y, w, h)

        if self._drag_rect_page is not None:
            x0, y0, x1, y1 = self._drag_rect_page
            left = area.left() + int(min(x0, x1) * self._zoom)
            top = area.top() + int(min(y0, y1) * self._zoom)
            width = max(1, int(abs(x1 - x0) * self._zoom))
            height = max(1, int(abs(y1 - y0) * self._zoom))
            painter.setPen(QColor(47, 114, 225, 210))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(left, top, width, height)

        painter.end()


class FlexibleWidthRow(QWidget):
    """A row widget that does not force the window minimum width."""

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        hint = super().minimumSizeHint()
        return QSize(120, hint.height())


class PdfUltimateMainWindow(QMainWindow):
    _MAX_RECENT_FILES = 18
    _MAX_TAB_TITLE_CHARS = 22

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HOME PDF")
        self.resize(1366, 820)
        self.setMinimumSize(760, 560)
        self.setAcceptDrops(True)

        self.state_store = AppStateStore()
        self.toolkit = PdfToolkit(output_root=output_root())
        # OCR integration (external runner)
        self._ocr_process: QProcess | None = None
        self._ocr_job_active = False
        self._ocr_completion_marker: Path | None = None
        self._last_ocr_output_dir: Path | None = None
        self._last_ocr_output_file: Path | None = None
        self._last_ocr_expected_outputs: list[Path] = []
        self._last_ocr_job_name: str | None = None
        self._last_ocr_format: str | None = None
        self._ocr_completion_timer = QTimer(self)
        self._ocr_completion_timer.setInterval(1000)
        self._ocr_completion_timer.timeout.connect(self._check_live_ocr_completion)
        self.current_pdf: Path | None = None
        self.current_doc: fitz.Document | None = None
        self.current_page_index = 0
        self.zoom_factor = 1.0
        self.fit_mode = "width"  # width | page | manual
        default_view = str(self.state_store.get_ui("default_view_mode", "continuous")).lower()
        self.view_mode = "continuous" if default_view.startswith("continuous") else "single"
        self.merge_sources: list[Path] = []
        self.to_pdf_sources: list[Path] = []
        self.page_order: list[int] = []
        self.preview_cache: OrderedDict[tuple[str, int, float, float], QImage] = OrderedDict()
        self._preview_cache_limit = 220
        self._preview_cache_max_bytes = 96 * 1024 * 1024
        self._preview_cache_bytes = 0
        self._preview_cache_sizes: dict[tuple[str, int, float, float], int] = {}
        self._inflight_renders: set[tuple[str, int, float, float]] = set()
        self.text_tool_mode = "view"  # view | select | convert
        self._converted_text_cache = ""
        self._converted_text_doc_token = ""
        self._text_search_spans: list[tuple[int, int]] = []
        self._text_search_cursor = -1
        self._thumb_zoom = 0.19
        self._thumb_quality = 1.0
        self._thumbnail_items_by_page: dict[int, QListWidgetItem] = {}
        self.outline_targets: list[int] = []
        self._default_main_sizes = [220, 1100, 0]
        self._default_body_sizes = [0, 1100]
        self._last_left_size = self._default_main_sizes[0]
        self._last_right_size = 340
        self._last_thumb_size = self._default_body_sizes[0]
        self.zoom_presets = ["50%", "67%", "75%", "90%", "100%", "110%", "125%", "150%", "175%", "200%", "250%"]
        self.search_hits: dict[int, list[tuple[float, float, float, float]]] = {}
        self.search_sequence: list[tuple[int, int, int]] = []
        self.search_cursor = -1
        self.nav_history: list[int] = []
        self.nav_history_cursor = -1
        self._suspend_history = False
        self.operation_doc_history: list[Path] = []
        self.operation_doc_history_cursor = -1
        self._tab_change_record_doc_history = False
        self.renderer = PdfRenderService(self, max_workers=2)
        self.renderer.rendered.connect(self._on_render_ready)
        self.renderer.failed.connect(self._on_render_failed)

        self._async_bridge = _AsyncBridge(self)
        self._async_bridge.searchCompleted.connect(self._on_async_search_completed)
        self._async_bridge.searchFailed.connect(self._on_async_search_failed)
        self._search_job_counter = 0
        self._active_search_job_id = 0
        self._active_search_query = ""
        self._active_search_doc_token = ""
        self._active_search_future = None

        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._apply_fit_after_layout_change)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._execute_search)
        self._state_save_timer = QTimer(self)
        self._state_save_timer.setSingleShot(True)
        self._state_save_timer.timeout.connect(self._save_current_document_state)
        self._layout_state_timer = QTimer(self)
        self._layout_state_timer.setSingleShot(True)
        self._layout_state_timer.timeout.connect(self._save_layout_state)
        self._thumbnail_render_timer = QTimer(self)
        self._thumbnail_render_timer.setSingleShot(True)
        self._thumbnail_render_timer.timeout.connect(self._queue_visible_thumbnail_renders)

        self._build_menu()
        self._build_ui()
        self._setup_reader_shortcuts()
        self._update_operation_history_actions()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.setStatusBar(QStatusBar(self))
        self._refresh_recent_files_menu()
        self.statusBar().showMessage("Ready. Drop a PDF or click Open PDF.")

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        self.file_menu = file_menu

        open_action = QAction("Open PDF", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._pick_open_pdf)
        file_menu.addAction(open_action)

        save_copy_action = QAction("Extract Pages As...", self)
        save_copy_action.triggered.connect(self._extract_pages_from_ui)
        file_menu.addAction(save_copy_action)

        close_tab_action = QAction("Close Tab", self)
        close_tab_action.setShortcut("Ctrl+W")
        close_tab_action.triggered.connect(self._close_current_document_tab)
        file_menu.addAction(close_tab_action)

        file_menu.addSeparator()
        self.recent_files_menu = file_menu.addMenu("History")

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        edit_menu = self.menuBar().addMenu("Edit")
        self.edit_menu = edit_menu
        self.undo_operation_action = QAction("Undo File Operation", self)
        self.undo_operation_action.setShortcut("Ctrl+Z")
        self.undo_operation_action.triggered.connect(self._undo_file_operation)
        edit_menu.addAction(self.undo_operation_action)

        self.redo_operation_action = QAction("Redo File Operation", self)
        self.redo_operation_action.setShortcut("Ctrl+Y")
        self.redo_operation_action.triggered.connect(self._redo_file_operation)
        edit_menu.addAction(self.redo_operation_action)

        view_menu = self.menuBar().addMenu("View")
        self.view_menu = view_menu
        zoom_in = QAction("Zoom In", self)
        zoom_in.setShortcut("Ctrl++")
        zoom_in.triggered.connect(lambda: self._change_zoom(1.15))
        view_menu.addAction(zoom_in)

        zoom_out = QAction("Zoom Out", self)
        zoom_out.setShortcut("Ctrl+-")
        zoom_out.triggered.connect(lambda: self._change_zoom(1 / 1.15))
        view_menu.addAction(zoom_out)

        fit_width_action = QAction("Fit Width", self)
        fit_width_action.setShortcut("Ctrl+9")
        fit_width_action.triggered.connect(self._fit_width)
        view_menu.addAction(fit_width_action)

        fit_action = QAction("Fit Page", self)
        fit_action.setShortcut("Ctrl+0")
        fit_action.triggered.connect(self._fit_page)
        view_menu.addAction(fit_action)

        actual_action = QAction("Actual Size", self)
        actual_action.setShortcut("Ctrl+8")
        actual_action.triggered.connect(self._actual_size)
        view_menu.addAction(actual_action)

        toggle_left = QAction("Toggle Left Panel", self)
        toggle_left.setShortcut("Ctrl+1")
        toggle_left.triggered.connect(self._toggle_left_panel)
        view_menu.addAction(toggle_left)

        toggle_right = QAction("Toggle Tool Studio", self)
        toggle_right.setShortcut("Ctrl+2")
        toggle_right.triggered.connect(self._toggle_right_panel)
        view_menu.addAction(toggle_right)

        toggle_thumbs = QAction("Toggle Thumbnails", self)
        toggle_thumbs.setShortcut("Ctrl+3")
        toggle_thumbs.triggered.connect(self._toggle_thumbnail_panel)
        view_menu.addAction(toggle_thumbs)

        reset_layout_action = QAction("Restore Layout Defaults", self)
        reset_layout_action.triggered.connect(self._restore_layout_defaults)
        view_menu.addAction(reset_layout_action)

        text_tool_action = QAction("Cycle Text Tool", self)
        text_tool_action.setShortcut("Ctrl+4")
        text_tool_action.triggered.connect(self._cycle_text_tool_mode)
        view_menu.addAction(text_tool_action)

        fullscreen_action = QAction("Toggle Fullscreen", self)
        fullscreen_action.setShortcut("F11")
        fullscreen_action.triggered.connect(self._toggle_fullscreen)
        view_menu.addAction(fullscreen_action)

        view_menu.addSeparator()
        navigate_back_action = QAction("Navigate Back", self)
        navigate_back_action.setShortcut("Alt+Left")
        navigate_back_action.triggered.connect(self._history_back)
        view_menu.addAction(navigate_back_action)

        navigate_forward_action = QAction("Navigate Forward", self)
        navigate_forward_action.setShortcut("Alt+Right")
        navigate_forward_action.triggered.connect(self._history_forward)
        view_menu.addAction(navigate_forward_action)

        view_menu.addSeparator()
        find_action = QAction("Find", self)
        find_action.setShortcut("Ctrl+F")
        find_action.triggered.connect(self._show_search_bar)
        view_menu.addAction(find_action)

        find_next_action = QAction("Find Next", self)
        find_next_action.setShortcut("F3")
        find_next_action.triggered.connect(self._search_next)
        view_menu.addAction(find_next_action)

        find_prev_action = QAction("Find Previous", self)
        find_prev_action.setShortcut("Shift+F3")
        find_prev_action.triggered.connect(self._search_prev)
        view_menu.addAction(find_prev_action)

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(12)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setHandleWidth(10)
        self.main_splitter.setOpaqueResize(False)
        self.main_splitter.setCollapsible(0, True)
        self.main_splitter.setCollapsible(2, True)
        self.main_splitter.splitterMoved.connect(self._on_layout_changed)

        self.left_panel = self._build_left_panel()
        self.center_panel = self._build_center_panel()
        self.right_panel = self._build_right_panel()

        self.main_splitter.addWidget(self.left_panel)
        self.main_splitter.addWidget(self.center_panel)
        self.main_splitter.addWidget(self.right_panel)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes(self._default_main_sizes)
        self._register_splitter_handles(self.main_splitter, "main")
        root_layout.addWidget(self.main_splitter)

        self.setCentralWidget(root)
        self._restore_layout_state()
        self._restore_window_geometry()

    def _panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        return panel

    def _build_left_panel(self) -> QWidget:
        panel = self._panel()
        panel.setMinimumWidth(180)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("HOME PDF")
        title.setObjectName("title")
        layout.addWidget(title)

        open_btn = QPushButton("Open PDF")
        open_btn.setProperty("primary", True)
        open_btn.clicked.connect(self._pick_open_pdf)
        layout.addWidget(open_btn)

        self.meta_label = QLabel("No document loaded.")
        self.meta_label.setWordWrap(True)
        self.meta_label.setStyleSheet("color:#1e293b;background:#eef2ff;padding:10px;border-radius:10px;")
        layout.addWidget(self.meta_label)

        self.navigation_combo = QComboBox()
        self.navigation_combo.addItems(['Pages', 'Outline', 'Merge'])
        self.navigation_stack = QStackedWidget()
        self.navigation_combo.currentIndexChanged.connect(self.navigation_stack.setCurrentIndex)
        layout.addWidget(self.navigation_combo)

        outline_tab = QWidget()
        outline_layout = QVBoxLayout(outline_tab)
        outline_layout.setContentsMargins(6, 6, 6, 6)
        outline_layout.setSpacing(6)
        self.outline_list = QListWidget()
        self.outline_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.outline_list.itemActivated.connect(self._open_outline_item)
        self.outline_list.itemClicked.connect(self._open_outline_item)
        outline_layout.addWidget(self.outline_list, 1)

        merge_tab = QWidget()
        merge_layout = QVBoxLayout(merge_tab)
        merge_layout.setContentsMargins(6, 6, 6, 6)
        merge_layout.setSpacing(8)
        self.merge_list = FileDropListWidget()
        self.merge_list.filesDropped.connect(self._on_merge_files_dropped)
        self.merge_list.reordered.connect(self._sync_merge_sources_from_widget)
        merge_layout.addWidget(self.merge_list, 1)

        queue_buttons = QHBoxLayout()
        add_files = QPushButton("Add")
        add_files.clicked.connect(self._add_merge_files)
        remove_files = QPushButton("Remove")
        remove_files.clicked.connect(self._remove_merge_files)
        clear_files = QPushButton("Clear")
        clear_files.clicked.connect(self._clear_merge_files)
        queue_buttons.addWidget(add_files)
        queue_buttons.addWidget(remove_files)
        queue_buttons.addWidget(clear_files)
        merge_layout.addLayout(queue_buttons)

        merge_now = QPushButton("Merge Queue")
        merge_now.clicked.connect(self._merge_queue)
        merge_layout.addWidget(merge_now)

        self.navigation_stack.addWidget(outline_tab)
        self.navigation_stack.addWidget(merge_tab)
        layout.addWidget(self.navigation_stack, 1)
        return panel

    def _build_center_panel(self) -> QWidget:
        panel = self._panel()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.document_tabs = QTabBar()
        self.document_tabs.setDocumentMode(True)
        self.document_tabs.setMovable(True)
        self.document_tabs.setTabsClosable(True)
        self.document_tabs.setUsesScrollButtons(True)
        self.document_tabs.currentChanged.connect(self._on_document_tab_changed)
        self.document_tabs.tabCloseRequested.connect(self._close_document_tab)
        self.document_tabs.installEventFilter(self)
        self.document_tabs.hide()
        layout.addWidget(self.document_tabs, 0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(10)
        self.left_toggle_btn = QPushButton("Navigation")
        self.left_toggle_btn.clicked.connect(self._toggle_left_panel)
        prev_btn = QPushButton("Prev")
        prev_btn.clicked.connect(lambda: self._set_page(self.current_page_index - 1))
        next_btn = QPushButton("Next")
        next_btn.clicked.connect(lambda: self._set_page(self.current_page_index + 1))
        self.view_mode_combo = QComboBox()
        self.view_mode_combo.addItems(["Single", "Continuous"])
        self.view_mode_combo.setCurrentText("Continuous" if self.view_mode == "continuous" else "Single")
        self.view_mode_combo.currentTextChanged.connect(self._on_view_mode_changed)
        self.thumb_toggle_btn = QPushButton("Hide Thumbs")
        self.thumb_toggle_btn.clicked.connect(self._toggle_thumbnail_panel)
        zoom_out = QPushButton("-")
        zoom_out.clicked.connect(lambda: self._change_zoom(1 / 1.15))
        zoom_in = QPushButton("+")
        zoom_in.clicked.connect(lambda: self._change_zoom(1.15))
        fit_width_btn = QPushButton("Fit Width")
        fit_width_btn.clicked.connect(self._fit_width)
        fit_btn = QPushButton("Fit Page")
        fit_btn.clicked.connect(self._fit_page)
        actual_size_btn = QPushButton("100%")
        actual_size_btn.clicked.connect(self._actual_size)
        self.zoom_combo = QComboBox()
        self.zoom_combo.setEditable(True)
        self.zoom_combo.setMaximumWidth(96)
        self.zoom_combo.addItems(self.zoom_presets)
        self.zoom_combo.activated.connect(self._on_zoom_combo_changed)
        if self.zoom_combo.lineEdit() is not None:
            self.zoom_combo.lineEdit().editingFinished.connect(self._on_zoom_combo_changed)
        self.text_tool_combo = QComboBox()
        self.text_tool_combo.addItems(["View", "Select Text", "Convert2Text"])
        self.text_tool_combo.setCurrentText("View")
        self.text_tool_combo.currentTextChanged.connect(self._on_text_tool_combo_changed)
        self.right_toggle_btn = QPushButton("Tools")
        self.right_toggle_btn.clicked.connect(self._toggle_right_panel)
        self.page_jump_spin = QSpinBox()
        self.page_jump_spin.setRange(1, 1)
        self.page_jump_spin.setMaximumWidth(78)
        self.page_jump_spin.setToolTip("Go to page")
        go_page_btn = QPushButton("Go")
        go_page_btn.clicked.connect(self._jump_to_page_from_spin)

        self.page_label = QLabel("Page - / -")
        self.page_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.page_label.setStyleSheet("font-weight:700;color:#1e3a8a;")

        find_btn = QPushButton('Find')
        find_btn.clicked.connect(self._show_search_bar)
        self.page_jump_spin.setKeyboardTracking(False)
        self.page_jump_spin.valueChanged.connect(lambda value: self._set_page(value - 1))
        for control in [self.left_toggle_btn, self.page_jump_spin, self.page_label,
                        self.zoom_combo, fit_width_btn, find_btn, self.right_toggle_btn]:
            toolbar.addWidget(control)
        toolbar.addStretch(1)
        for control in [self.left_toggle_btn, fit_width_btn, find_btn, self.right_toggle_btn]:
            control.setMinimumWidth(control.minimumSizeHint().width())
        # Secondary reader choices remain available through View.
        options_menu = self.view_menu.addMenu('Reader mode')
        for name in ['Single', 'Continuous']:
            action = options_menu.addAction(name)
            action.triggered.connect(lambda checked=False, value=name: self.view_mode_combo.setCurrentText(value))
        text_menu = self.view_menu.addMenu('Text')
        for name in ['View', 'Select Text', 'Convert2Text']:
            action = text_menu.addAction(name)
            action.triggered.connect(lambda checked=False, value=name: self.text_tool_combo.setCurrentText(value))
        self.toolbar_row = FlexibleWidthRow()
        self.toolbar_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toolbar_row.setLayout(toolbar)
        layout.addWidget(self.toolbar_row, 0)

        self.search_row = FlexibleWidthRow()
        self.search_row.setVisible(False)
        self.search_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        search_layout = QHBoxLayout(self.search_row)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find in document")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(lambda _t: self._search_timer.start(220))
        self.search_input.returnPressed.connect(self._search_next)
        search_prev_btn = QPushButton("Prev")
        search_prev_btn.clicked.connect(self._search_prev)
        search_next_btn = QPushButton("Next")
        search_next_btn.clicked.connect(self._search_next)
        self.search_result_label = QLabel("0 / 0")
        self.search_result_label.setStyleSheet("color:#1e3a8a;font-weight:600;")
        close_search_btn = QPushButton("Close")
        close_search_btn.clicked.connect(self._hide_search_bar)
        search_layout.addWidget(self.search_input, 1)
        search_layout.addWidget(search_prev_btn)
        search_layout.addWidget(search_next_btn)
        search_layout.addWidget(self.search_result_label)
        search_layout.addWidget(close_search_btn)
        layout.addWidget(self.search_row, 0)

        self.body_split = QSplitter(Qt.Horizontal)
        self.body_split.setHandleWidth(10)
        self.body_split.setOpaqueResize(False)
        self.body_split.setCollapsible(0, True)
        self.thumbnail_list = QListWidget()
        self.thumbnail_list.setIconSize(QSize(90, 124))
        self.thumbnail_list.setMinimumWidth(96)
        self.thumbnail_list.setMaximumWidth(380)
        self.thumbnail_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.thumbnail_list.setDragEnabled(True)
        self.thumbnail_list.setAcceptDrops(True)
        self.thumbnail_list.setDropIndicatorShown(True)
        self.thumbnail_list.currentRowChanged.connect(self._set_page)
        self.thumbnail_list.model().rowsMoved.connect(self._on_thumbnail_reordered)
        self.thumbnail_list.verticalScrollBar().valueChanged.connect(self._schedule_visible_thumbnail_renders)
        self.thumbnail_list.viewport().installEventFilter(self)
        self.navigation_stack.insertWidget(0, self.thumbnail_list)
        self.navigation_stack.setCurrentIndex(0)
        self.legacy_thumbnail_slot = QWidget()
        self.legacy_thumbnail_slot.setMaximumWidth(0)
        self.body_split.addWidget(self.legacy_thumbnail_slot)

        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(False)
        self.page_scroll.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.page_scroll.setStyleSheet(
            "QScrollArea{background:#e8eef8;border:1px solid #d0dceb;border-radius:12px;}"
        )
        self.page_scroll.viewport().installEventFilter(self)
        self.page_scroll.verticalScrollBar().valueChanged.connect(self._on_view_scroll)

        self.page_stack = QStackedWidget()
        self.page_image = SelectablePageLabel("Open a PDF to preview")
        self.page_image.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.page_image.setContentsMargins(0, 0, 0, 0)
        self.page_image.setStyleSheet("background:#ffffff;border:1px solid #d7e2f2;border-radius:8px;")
        self.page_text_view = QTextEdit()
        self.page_text_view.setReadOnly(True)
        self.page_text_view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.page_text_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.page_text_view.setStyleSheet("background:#ffffff;border:1px solid #d7e2f2;border-radius:8px;")
        self.page_stack.addWidget(self.page_image)
        self.page_stack.addWidget(self.page_text_view)
        self.page_stack.setCurrentWidget(self.page_image)
        self._set_display_size(860, 1160)
        self.continuous_view = ContinuousPageView(
            self._get_or_request_page_image,
            highlight_provider=self._search_highlights_for_page,
            active_highlight_provider=self._active_search_hit,
        )
        self.page_scroll.setWidget(self.page_stack)
        self.body_split.addWidget(self.page_scroll)
        self.body_split.setStretchFactor(0, 0)
        self.body_split.setStretchFactor(1, 1)
        self.body_split.setSizes(self._default_body_sizes)
        self._register_splitter_handles(self.body_split, "body")
        layout.addWidget(self.body_split, 1)
        layout.setStretch(0, 0)
        layout.setStretch(1, 1)

        self._update_zoom_label()
        self._sync_thumbnail_toggle_label()
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = self._panel()
        panel.setMinimumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        tools_title = QLabel("Tool Studio")
        tools_title.setObjectName("title")
        tools_title.setStyleSheet("font-size:14pt;")
        layout.addWidget(tools_title)

        self.tools_combo = QComboBox()
        self.tools_combo.addItems(["Organize", "Convert", "OCR", "Security", "Enhance"])
        self.tools_combo.currentIndexChanged.connect(self._on_tool_mode_changed)
        layout.addWidget(self.tools_combo)

        self.tools_stack = QStackedWidget()
        for build in [self._build_organize_tab, self._build_convert_tab, self._build_ocr_tab,
                      self._build_security_tab, self._build_enhance_tab]:
            form = build()
            for label in form.findChildren(QLabel):
                label.setWordWrap(True)
            form.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(form)
            self.tools_stack.addWidget(scroll)
        self.tools_stack.setCurrentIndex(0)
        layout.addWidget(self.tools_stack, 1)
        return panel

    def _on_tool_mode_changed(self, index: int) -> None:
        if not hasattr(self, "tools_stack"):
            return
        if index < 0 or index >= self.tools_stack.count():
            return
        self.tools_stack.setCurrentIndex(index)

    @staticmethod
    def _text_tool_label(mode: str) -> str:
        if mode == "select":
            return "Select Text"
        if mode == "convert":
            return "Convert2Text"
        return "View"

    @staticmethod
    def _normalize_text_tool_mode(value: str) -> str:
        lowered = value.strip().lower()
        if lowered.startswith("select"):
            return "select"
        if lowered.startswith("convert"):
            return "convert"
        return "view"

    def _on_text_tool_combo_changed(self, text: str) -> None:
        self._set_text_tool_mode(self._normalize_text_tool_mode(text))

    def _cycle_text_tool_mode(self) -> None:
        order = ["view", "select", "convert"]
        current = self.text_tool_mode if self.text_tool_mode in order else "view"
        idx = order.index(current)
        self._set_text_tool_mode(order[(idx + 1) % len(order)])

    def _text_select_active(self) -> bool:
        return self.text_tool_mode == "select"

    def _convert_text_active(self) -> bool:
        return self.text_tool_mode == "convert"

    def _set_text_tool_mode(self, mode: str) -> None:
        normalized = self._normalize_text_tool_mode(mode)
        if normalized in {"select", "convert"} and self.view_mode == "continuous":
            self.view_mode_combo.blockSignals(True)
            self.view_mode_combo.setCurrentText("Single")
            self.view_mode_combo.blockSignals(False)
            self.view_mode = "single"

        if normalized == self.text_tool_mode and self.text_tool_combo.currentText() == self._text_tool_label(normalized):
            if self.current_doc is not None and self.view_mode == "single":
                self._set_page(self.current_page_index, record_history=False)
            return

        self.text_tool_mode = normalized
        self.page_image.set_selection_mode(self._text_select_active())
        self.text_tool_combo.blockSignals(True)
        self.text_tool_combo.setCurrentText(self._text_tool_label(self.text_tool_mode))
        self.text_tool_combo.blockSignals(False)
        if not self._convert_text_active():
            self._text_search_spans = []
            self._text_search_cursor = -1
            self.page_text_view.setExtraSelections([])
        self._update_search_result_label()
        if self.current_doc is not None:
            self._refresh_view()

    def _setup_reader_shortcuts(self) -> None:
        bindings = [
            ("Down", lambda: self._reader_scroll_vertical(54)),
            ("Up", lambda: self._reader_scroll_vertical(-54)),
            ("PageDown", lambda: self._reader_page_scroll(True)),
            ("PageUp", lambda: self._reader_page_scroll(False)),
            ("Space", lambda: self._reader_page_scroll(True)),
            ("Shift+Space", lambda: self._reader_page_scroll(False)),
            ("Right", lambda: self._reader_scroll_horizontal(54)),
            ("Left", lambda: self._reader_scroll_horizontal(-54)),
            ("Home", self._reader_go_home),
            ("End", self._reader_go_end),
        ]
        for shortcut, handler in bindings:
            action = QAction(self)
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.WindowShortcut)
            action.triggered.connect(handler)
            self.addAction(action)

    def _jump_to_page_from_spin(self) -> None:
        if self.current_doc is None:
            return
        target = max(1, int(self.page_jump_spin.value())) - 1
        self._set_page(target)

    def _sync_page_jump_controls(self) -> None:
        total = len(self.page_order) if self.page_order else (self.current_doc.page_count if self.current_doc else 0)
        total = max(1, int(total))
        self.page_jump_spin.blockSignals(True)
        self.page_jump_spin.setRange(1, total)
        self.page_jump_spin.setValue(max(1, min(total, self.current_page_index + 1)))
        self.page_jump_spin.blockSignals(False)

    def _load_outline(self) -> None:
        self.outline_targets = []
        self.outline_list.clear()
        if self.current_doc is None:
            return
        toc = self.current_doc.get_toc(simple=True)
        if not toc:
            item = QListWidgetItem("(No outline)")
            item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.outline_list.addItem(item)
            return

        total = self.current_doc.page_count
        for level, title, page_number in toc:
            page_idx = max(0, min(total - 1, int(page_number) - 1))
            prefix = "  " * max(0, int(level) - 1)
            item = QListWidgetItem(f"{prefix}{title}")
            item.setToolTip(title)
            item.setData(Qt.UserRole, page_idx)
            self.outline_targets.append(page_idx)
            self.outline_list.addItem(item)

        self._sync_outline_selection()

    def _open_outline_item(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        data = item.data(Qt.UserRole)
        if not isinstance(data, int):
            return
        actual_idx = max(0, int(data))
        if self.current_doc is None:
            return
        if self.page_order:
            try:
                display_idx = self.page_order.index(actual_idx)
            except ValueError:
                return
        else:
            display_idx = actual_idx
        self._set_page(display_idx)

    def _sync_outline_selection(self) -> None:
        if not hasattr(self, "outline_list"):
            return
        if self.outline_list.count() <= 0:
            return
        if self.current_doc is None:
            return
        actual_idx = self.page_order[self.current_page_index] if self.page_order else self.current_page_index
        target_row = -1
        for row in range(self.outline_list.count()):
            item = self.outline_list.item(row)
            if item is None:
                continue
            data = item.data(Qt.UserRole)
            if isinstance(data, int) and data == actual_idx:
                target_row = row
                break
        if target_row >= 0 and self.outline_list.currentRow() != target_row:
            self.outline_list.blockSignals(True)
            self.outline_list.setCurrentRow(target_row)
            self.outline_list.blockSignals(False)

    def _reader_shortcuts_enabled(self) -> bool:
        if self.current_doc is None:
            return False
        focused = self.focusWidget()
        if focused is None:
            return True
        if isinstance(focused, (QLineEdit, QTextEdit)):
            return False
        if isinstance(focused, QComboBox):
            return False
        return True

    def _reader_scroll_vertical(self, delta: int) -> None:
        if not self._reader_shortcuts_enabled():
            return
        if self.view_mode == "continuous":
            bar = self.page_scroll.verticalScrollBar()
            bar.setValue(max(0, min(bar.maximum(), bar.value() + delta)))
            return

        total = len(self.page_order) if self.page_order else (self.current_doc.page_count if self.current_doc else 0)
        if total <= 0:
            return
        bar = self.page_scroll.verticalScrollBar()
        new_val = bar.value() + delta
        if delta > 0 and bar.value() >= bar.maximum() - 2 and self.current_page_index < total - 1:
            self._set_page(self.current_page_index + 1)
            self.page_scroll.verticalScrollBar().setValue(0)
            return
        if delta < 0 and bar.value() <= 2 and self.current_page_index > 0:
            self._set_page(self.current_page_index - 1)
            back_bar = self.page_scroll.verticalScrollBar()
            back_bar.setValue(max(0, back_bar.maximum() - 24))
            return
        bar.setValue(max(0, min(bar.maximum(), new_val)))

    def _reader_scroll_horizontal(self, delta: int) -> None:
        if not self._reader_shortcuts_enabled():
            return
        bar = self.page_scroll.horizontalScrollBar()
        bar.setValue(max(0, min(bar.maximum(), bar.value() + delta)))

    def _reader_page_scroll(self, forward: bool) -> None:
        if not self._reader_shortcuts_enabled():
            return
        viewport_step = max(120, int(self.page_scroll.viewport().height() * 0.88))
        self._reader_scroll_vertical(viewport_step if forward else -viewport_step)

    def _reader_go_home(self) -> None:
        if not self._reader_shortcuts_enabled():
            return
        if self.view_mode == "continuous":
            self.page_scroll.verticalScrollBar().setValue(0)
            self.current_page_index = 0
            if self.thumbnail_list.currentRow() != 0:
                self.thumbnail_list.blockSignals(True)
                self.thumbnail_list.setCurrentRow(0)
                self.thumbnail_list.blockSignals(False)
            self.page_label.setText(f"Page 1 / {self.current_doc.page_count if self.current_doc else 0}")
            self._sync_page_jump_controls()
            self._sync_outline_selection()
            return
        self._set_page(0)

    def _reader_go_end(self) -> None:
        if not self._reader_shortcuts_enabled() or self.current_doc is None:
            return
        total = len(self.page_order) if self.page_order else self.current_doc.page_count
        if total <= 0:
            return
        if self.view_mode == "continuous":
            self.page_scroll.verticalScrollBar().setValue(self.page_scroll.verticalScrollBar().maximum())
            self.current_page_index = total - 1
            if self.thumbnail_list.currentRow() != self.current_page_index:
                self.thumbnail_list.blockSignals(True)
                self.thumbnail_list.setCurrentRow(self.current_page_index)
                self.thumbnail_list.blockSignals(False)
            self.page_label.setText(f"Page {total} / {total}")
            self._sync_page_jump_controls()
            self._sync_outline_selection()
            return
        self._set_page(total - 1)
        self.page_scroll.verticalScrollBar().setValue(self.page_scroll.verticalScrollBar().maximum())

    def _handle_reader_keypress(self, event) -> bool:
        if not self._reader_shortcuts_enabled():
            return False

        key = event.key()
        mods = event.modifiers()
        shift_only = mods == Qt.ShiftModifier
        no_mods = mods == Qt.NoModifier

        if no_mods and key == Qt.Key_Down:
            self._reader_scroll_vertical(54)
            return True
        if no_mods and key == Qt.Key_Up:
            self._reader_scroll_vertical(-54)
            return True
        if no_mods and key == Qt.Key_PageDown:
            self._reader_page_scroll(True)
            return True
        if no_mods and key == Qt.Key_PageUp:
            self._reader_page_scroll(False)
            return True
        if no_mods and key == Qt.Key_Space:
            self._reader_page_scroll(True)
            return True
        if shift_only and key == Qt.Key_Space:
            self._reader_page_scroll(False)
            return True
        if no_mods and key == Qt.Key_Right:
            self._reader_scroll_horizontal(54)
            return True
        if no_mods and key == Qt.Key_Left:
            self._reader_scroll_horizontal(-54)
            return True
        if no_mods and key == Qt.Key_Home:
            self._reader_go_home()
            return True
        if no_mods and key == Qt.Key_End:
            self._reader_go_end()
            return True
        return False

    def _handle_single_mode_wheel_edge_transition(self, event) -> bool:
        if self.current_doc is None or self.view_mode != "single":
            return False
        if self._convert_text_active():
            return False
        if event.modifiers() & Qt.ControlModifier:
            return False

        delta = event.angleDelta().y()
        if delta == 0:
            return False

        total = len(self.page_order) if self.page_order else self.current_doc.page_count
        if total <= 0:
            return False

        bar = self.page_scroll.verticalScrollBar()
        if delta < 0 and bar.value() >= bar.maximum() - 2 and self.current_page_index < total - 1:
            self._set_page(self.current_page_index + 1)
            self.page_scroll.verticalScrollBar().setValue(0)
            return True
        if delta > 0 and bar.value() <= 2 and self.current_page_index > 0:
            self._set_page(self.current_page_index - 1)
            prev = self.page_scroll.verticalScrollBar()
            prev.setValue(max(0, prev.maximum() - 12))
            return True
        return False

    def _build_organize_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)

        self.extract_selection_input = QLineEdit("1-3")
        self.extract_selection_input.setPlaceholderText("Extract pages, e.g. 1-3,8")
        extract_btn = QPushButton("Extract Selected Pages")
        extract_btn.clicked.connect(self._extract_pages_from_ui)
        layout.addWidget(QLabel("Extract Pages"))
        layout.addWidget(self.extract_selection_input)
        layout.addWidget(extract_btn)

        self.delete_selection_input = QLineEdit("2")
        self.delete_selection_input.setPlaceholderText("Delete pages, e.g. 2,4-5")
        delete_btn = QPushButton("Delete Pages")
        delete_btn.clicked.connect(self._delete_pages)
        layout.addWidget(QLabel("Delete Pages"))
        layout.addWidget(self.delete_selection_input)
        layout.addWidget(delete_btn)

        self.reorder_input = QLineEdit("1,3,2,4-6")
        self.reorder_input.setPlaceholderText("New order, e.g. 1,3,2,4-6")
        reorder_btn = QPushButton("Reorder Pages")
        reorder_btn.clicked.connect(self._reorder_pages)
        apply_thumb_reorder_btn = QPushButton("Apply Thumbnail Drag Order")
        apply_thumb_reorder_btn.clicked.connect(self._apply_thumbnail_order)
        layout.addWidget(QLabel("Reorder"))
        layout.addWidget(self.reorder_input)
        layout.addWidget(reorder_btn)
        layout.addWidget(apply_thumb_reorder_btn)

        rotate_row = QHBoxLayout()
        self.rotate_selection_input = QLineEdit("1-")
        self.rotate_selection_input.setPlaceholderText("Pages to rotate")
        self.rotate_degrees = QComboBox()
        self.rotate_degrees.addItems(["90", "180", "270"])
        rotate_row.addWidget(self.rotate_selection_input)
        rotate_row.addWidget(self.rotate_degrees)
        rotate_btn = QPushButton("Rotate")
        rotate_btn.clicked.connect(self._rotate_pages)
        layout.addWidget(QLabel("Rotate Pages"))
        layout.addLayout(rotate_row)
        layout.addWidget(rotate_btn)

        self.split_ranges_input = QLineEdit("1-3,4-8")
        self.split_ranges_input.setPlaceholderText("Ranges for split, e.g. 1-3,4-8")
        split_ranges_btn = QPushButton("Split by Ranges")
        split_ranges_btn.clicked.connect(self._split_ranges)
        layout.addWidget(QLabel("Split by Ranges"))
        layout.addWidget(self.split_ranges_input)
        layout.addWidget(split_ranges_btn)

        every_row = QHBoxLayout()
        self.split_every_spin = QSpinBox()
        self.split_every_spin.setRange(1, 5000)
        self.split_every_spin.setValue(1)
        split_every_btn = QPushButton("Split Every N Pages")
        split_every_btn.clicked.connect(self._split_every)
        every_row.addWidget(self.split_every_spin)
        every_row.addWidget(split_every_btn)
        layout.addWidget(QLabel("Split by Chunk Size"))
        layout.addLayout(every_row)
        layout.addStretch(1)
        return tab

    def _build_convert_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)

        self.convert_target = QComboBox()
        self.convert_target.addItems(["docx", "txt", "md", "html", "json", "rtf", "png", "jpg"])
        convert_btn = QPushButton("Convert Document")
        convert_btn.clicked.connect(self._convert_document)
        layout.addWidget(QLabel("Convert PDF To"))
        layout.addWidget(self.convert_target)
        layout.addWidget(convert_btn)

        layout.addSpacing(6)
        layout.addWidget(QLabel("Convert To PDF (drop files and reorder)"))
        self.to_pdf_list = FileDropListWidget()
        self.to_pdf_list.setMinimumHeight(120)
        self.to_pdf_list.filesDropped.connect(self._on_to_pdf_files_dropped)
        self.to_pdf_list.reordered.connect(self._sync_to_pdf_sources_from_widget)
        layout.addWidget(self.to_pdf_list)
        to_pdf_btn_row = QHBoxLayout()
        add_to_pdf_btn = QPushButton("Add Files")
        add_to_pdf_btn.clicked.connect(self._add_to_pdf_files)
        remove_to_pdf_btn = QPushButton("Remove")
        remove_to_pdf_btn.clicked.connect(self._remove_to_pdf_files)
        clear_to_pdf_btn = QPushButton("Clear")
        clear_to_pdf_btn.clicked.connect(self._clear_to_pdf_files)
        to_pdf_btn_row.addWidget(add_to_pdf_btn)
        to_pdf_btn_row.addWidget(remove_to_pdf_btn)
        to_pdf_btn_row.addWidget(clear_to_pdf_btn)
        layout.addLayout(to_pdf_btn_row)
        convert_to_pdf_btn = QPushButton("Convert Selection To PDF")
        convert_to_pdf_btn.clicked.connect(self._convert_to_pdf)
        layout.addWidget(convert_to_pdf_btn)

        self.reflow_output = QTextEdit()
        self.reflow_output.setPlaceholderText("Reflowed text appears here...")
        self.reflow_output.setMinimumHeight(180)
        reflow_btn = QPushButton("Extract Reflow Text")
        reflow_btn.clicked.connect(self._reflow_text)
        save_reflow_btn = QPushButton("Save Reflow Text")
        save_reflow_btn.clicked.connect(self._save_reflow_text)

        layout.addWidget(QLabel("Text Reflow"))
        layout.addWidget(reflow_btn)
        layout.addWidget(self.reflow_output)
        layout.addWidget(save_reflow_btn)
        layout.addStretch(1)
        return tab

    def _ocr_candidate_roots(self) -> list[Path]:
        candidate_roots: list[Path] = []
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidate_roots.extend([exe_dir, exe_dir.parent, exe_dir.parent.parent])
        candidate_roots.extend([Path(__file__).resolve().parents[3], Path.cwd()])
        return candidate_roots

    def _is_python_interpreter_command(self, command: str) -> bool:
        raw = command.strip().strip('"').strip()
        if not raw:
            return False
        name = Path(raw).name.lower()
        stem = Path(raw).stem.lower()
        return name in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"} or stem in {
            "python",
            "python3",
            "py",
        }

    def _find_workspace_ocr_script(self) -> Path | None:
        seen: set[Path] = set()
        for root in self._ocr_candidate_roots():
            try:
                resolved_root = root.resolve()
            except Exception:
                continue
            if resolved_root in seen:
                continue
            seen.add(resolved_root)
            candidate = resolved_root / "ocr_tool" / "OCR.py"
            if candidate.exists():
                return candidate
        return None

    def _resolve_ocr_launch(self, command: str, args: list[str]) -> tuple[str, list[str], str]:
        launch_command = command
        launch_args = list(args)
        note = ""
        if self._is_python_interpreter_command(command):
            script_path = self._find_workspace_ocr_script()
            if script_path is None:
                raise PdfToolkitError("OCR.py was not found in the workspace for python-based OCR launch.")
            launch_args = [str(script_path), *launch_args]
            note = f"[OCR] Python interpreter detected; launching with script: {script_path}\n"
        return launch_command, launch_args, note

    def _default_ocr_command(self) -> str:
        stored = str(self.state_store.get_ui("ocr_command", "")).strip()
        if self._is_python_interpreter_command(stored):
            stored = ""

        seen: set[Path] = set()
        for root in self._ocr_candidate_roots():
            try:
                resolved_root = root.resolve()
            except Exception:
                continue
            if resolved_root in seen:
                continue
            seen.add(resolved_root)
            candidate = resolved_root / "scripts" / "ocr.bat"
            if candidate.exists():
                if stored.lower() == "ocr" or not stored:
                    return str(candidate)
                return stored
        if stored:
            return stored
        return "ocr"


    def _build_ocr_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)

        info = QLabel(
            "OCR (Local): This uses the rebuilt GLM-OCR sub-tool in this workspace by default. "
            "You can OCR the current PDF, a folder of images, one PDF in a folder, or all PDFs in a folder."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        layout.addWidget(QLabel("OCR Command"))
        cmd_row = QHBoxLayout()
        self.ocr_command_input = QLineEdit(self._default_ocr_command())
        self.ocr_command_input.setPlaceholderText(r"e.g. E:\Wisdom-app\Tools\Home_PDF\scripts\ocr.bat")
        pick_cmd_btn = QPushButton("Browse")
        pick_cmd_btn.clicked.connect(self._pick_ocr_command)
        cmd_row.addWidget(self.ocr_command_input)
        cmd_row.addWidget(pick_cmd_btn)
        layout.addLayout(cmd_row)

        layout.addWidget(QLabel("Source / PDF Mode"))
        source_row = QHBoxLayout()
        self.ocr_source_combo = QComboBox()
        self.ocr_source_combo.addItem("Current PDF", "current_pdf")
        self.ocr_source_combo.addItem("Folder Auto", "auto_folder")
        self.ocr_source_combo.addItem("Folder Single PDF", "pdf_folder")
        self.ocr_source_combo.addItem("Folder Images", "images_folder")
        self.ocr_source_combo.addItem("Folder All PDFs", "all_pdfs_folder")
        stored_source_mode = str(self.state_store.get_ui("ocr_source_mode", "current_pdf"))
        source_index = self.ocr_source_combo.findData(stored_source_mode)
        self.ocr_source_combo.setCurrentIndex(source_index if source_index >= 0 else 0)
        self.ocr_pdf_mode_combo = QComboBox()
        self.ocr_pdf_mode_combo.addItems(["pages", "direct"])
        self.ocr_pdf_mode_combo.setCurrentText(str(self.state_store.get_ui("ocr_pdf_mode", "pages")))
        source_row.addWidget(self.ocr_source_combo, 2)
        source_row.addWidget(QLabel("PDF Mode"))
        source_row.addWidget(self.ocr_pdf_mode_combo, 1)
        layout.addLayout(source_row)

        layout.addWidget(QLabel("Page Selection (PDF modes only; e.g. 2, 1-3, 5, 8-)"))
        self.ocr_pages_input = QLineEdit(str(self.state_store.get_ui("ocr_pages", "")))
        self.ocr_pages_input.setPlaceholderText("Leave empty for all pages")
        layout.addWidget(self.ocr_pages_input)

        layout.addWidget(QLabel("Input Folder (used for folder modes)"))
        input_row = QHBoxLayout()
        self.ocr_input_dir_input = QLineEdit(str(self.state_store.get_ui("ocr_input_dir", "")))
        self.ocr_input_dir_input.setPlaceholderText(r"e.g. E:\Scans")
        self.ocr_pick_input_btn = QPushButton("Browse")
        self.ocr_pick_input_btn.clicked.connect(self._pick_ocr_input_dir)
        input_row.addWidget(self.ocr_input_dir_input)
        input_row.addWidget(self.ocr_pick_input_btn)
        layout.addLayout(input_row)

        layout.addWidget(QLabel("Poppler bin folder (optional, only needed if pdf2image can't find Poppler)"))
        poppler_row = QHBoxLayout()
        self.ocr_poppler_input = QLineEdit(str(self.state_store.get_ui("ocr_poppler_path", "")))
        self.ocr_poppler_input.setPlaceholderText(r"e.g. C:\poppler\Library\bin")
        pick_poppler_btn = QPushButton("Browse")
        pick_poppler_btn.clicked.connect(self._pick_poppler_path)
        poppler_row.addWidget(self.ocr_poppler_input)
        poppler_row.addWidget(pick_poppler_btn)
        layout.addLayout(poppler_row)

        layout.addWidget(QLabel("Task / Output Format / Processor"))
        task_row = QHBoxLayout()
        self.ocr_task_combo = QComboBox()
        self.ocr_task_combo.addItems(["text", "table", "formula", "extract", "custom"])
        self.ocr_task_combo.setCurrentText(str(self.state_store.get_ui("ocr_task", "text")))
        self.ocr_format_combo = QComboBox()
        self.ocr_format_combo.addItems(["md", "txt", "json", "html"])
        self.ocr_format_combo.setCurrentText(str(self.state_store.get_ui("ocr_format", "md")))
        self.ocr_use_fast_combo = QComboBox()
        self.ocr_use_fast_combo.addItems(["auto", "true", "false"])
        stored_use_fast = str(self.state_store.get_ui("ocr_use_fast", "true")).strip().lower()
        if stored_use_fast not in {"true", "false"}:
            stored_use_fast = "true"
        self.ocr_use_fast_combo.setCurrentText(stored_use_fast)
        task_row.addWidget(self.ocr_task_combo)
        task_row.addWidget(self.ocr_format_combo)
        task_row.addWidget(QLabel("use_fast"))
        task_row.addWidget(self.ocr_use_fast_combo)
        layout.addLayout(task_row)

        layout.addWidget(QLabel("Model / Job Name"))
        model_row = QHBoxLayout()
        self.ocr_model_input = QLineEdit(str(self.state_store.get_ui("ocr_model", "zai-org/GLM-OCR")))
        self.ocr_model_input.setPlaceholderText("zai-org/GLM-OCR")
        self.ocr_job_name_input = QLineEdit(str(self.state_store.get_ui("ocr_job_name", "")))
        self.ocr_job_name_input.setPlaceholderText("Optional override")
        model_row.addWidget(self.ocr_model_input, 2)
        model_row.addWidget(QLabel("Job"))
        model_row.addWidget(self.ocr_job_name_input, 1)
        layout.addLayout(model_row)

        layout.addWidget(QLabel("Custom Prompt (used for task=custom)"))
        self.ocr_custom_prompt_edit = QTextEdit()
        self.ocr_custom_prompt_edit.setMinimumHeight(72)
        self.ocr_custom_prompt_edit.setPlaceholderText("Only used when task is custom.")
        self.ocr_custom_prompt_edit.setPlainText(str(self.state_store.get_ui("ocr_custom_prompt", "")))
        layout.addWidget(self.ocr_custom_prompt_edit)

        layout.addWidget(QLabel("Schema / Instructions File (used for task=extract)"))
        schema_row = QHBoxLayout()
        self.ocr_schema_input = QLineEdit(str(self.state_store.get_ui("ocr_schema", "")))
        self.ocr_schema_input.setPlaceholderText(r"e.g. .\ocr_tool\schemas\example_extract_schema.txt")
        self.ocr_pick_schema_btn = QPushButton("Browse")
        self.ocr_pick_schema_btn.clicked.connect(self._pick_ocr_schema)
        schema_row.addWidget(self.ocr_schema_input)
        schema_row.addWidget(self.ocr_pick_schema_btn)
        layout.addLayout(schema_row)

        layout.addWidget(QLabel("PDF Rendering (OCR side)"))
        perf_row = QHBoxLayout()
        self.ocr_dpi_spin = QSpinBox()
        self.ocr_dpi_spin.setRange(72, 600)
        self.ocr_dpi_spin.setValue(int(self.state_store.get_ui("ocr_dpi", 200)))
        self.ocr_batch_pages_spin = QSpinBox()
        self.ocr_batch_pages_spin.setRange(1, 128)
        self.ocr_batch_pages_spin.setValue(int(self.state_store.get_ui("ocr_batch_pages", 8)))
        self.ocr_max_pages_spin = QSpinBox()
        self.ocr_max_pages_spin.setRange(1, 5000)
        self.ocr_max_pages_spin.setValue(int(self.state_store.get_ui("ocr_max_pages", 1500)))
        perf_row.addWidget(QLabel("DPI"))
        perf_row.addWidget(self.ocr_dpi_spin)
        perf_row.addWidget(QLabel("Batch"))
        perf_row.addWidget(self.ocr_batch_pages_spin)
        perf_row.addWidget(QLabel("Max Pages"))
        perf_row.addWidget(self.ocr_max_pages_spin)
        layout.addLayout(perf_row)

        layout.addWidget(QLabel("Image / Generation Options"))
        options_row = QHBoxLayout()
        self.ocr_max_side_spin = QSpinBox()
        self.ocr_max_side_spin.setRange(0, 6000)
        self.ocr_max_side_spin.setValue(int(self.state_store.get_ui("ocr_max_side", 1800)))
        self.ocr_max_new_tokens_spin = QSpinBox()
        self.ocr_max_new_tokens_spin.setRange(64, 8192)
        self.ocr_max_new_tokens_spin.setSingleStep(128)
        self.ocr_max_new_tokens_spin.setValue(int(self.state_store.get_ui("ocr_max_new_tokens", 2048)))
        options_row.addWidget(QLabel("Max Side"))
        options_row.addWidget(self.ocr_max_side_spin)
        options_row.addWidget(QLabel("Max Tokens"))
        options_row.addWidget(self.ocr_max_new_tokens_spin)
        layout.addLayout(options_row)

        toggle_row = QHBoxLayout()
        self.ocr_per_page_box = QCheckBox("Per-page files")
        self.ocr_per_page_box.setChecked(bool(self.state_store.get_ui("ocr_per_page_files", False)))
        self.ocr_force_fp16_box = QCheckBox("Force fp16")
        self.ocr_force_fp16_box.setChecked(bool(self.state_store.get_ui("ocr_force_fp16", False)))
        self.ocr_trust_remote_code_box = QCheckBox("Trust remote code")
        self.ocr_trust_remote_code_box.setChecked(bool(self.state_store.get_ui("ocr_trust_remote_code", False)))
        self.ocr_live_terminal_box = QCheckBox("Live terminal")
        self.ocr_live_terminal_box.setChecked(bool(self.state_store.get_ui("ocr_live_terminal", True)))
        toggle_row.addWidget(self.ocr_per_page_box)
        toggle_row.addWidget(self.ocr_force_fp16_box)
        toggle_row.addWidget(self.ocr_trust_remote_code_box)
        toggle_row.addWidget(self.ocr_live_terminal_box)
        toggle_row.addStretch(1)
        layout.addLayout(toggle_row)

        btn_row = QHBoxLayout()
        run_btn = QPushButton("Run OCR Job")
        run_btn.clicked.connect(self._run_ocr_job)
        self.ocr_open_output_btn = QPushButton("Open OCR Output Folder")
        self.ocr_open_output_btn.setEnabled(False)
        self.ocr_open_output_btn.clicked.connect(self._open_last_ocr_output_folder)
        btn_row.addWidget(run_btn)
        btn_row.addWidget(self.ocr_open_output_btn)
        layout.addLayout(btn_row)

        self.ocr_log_view = QTextEdit()
        self.ocr_log_view.setReadOnly(True)
        self.ocr_log_view.setMinimumHeight(180)
        self.ocr_log_view.setPlaceholderText("OCR log + preview will appear here…")
        layout.addWidget(self.ocr_log_view, 1)
        self.ocr_source_combo.currentIndexChanged.connect(self._sync_ocr_mode_fields)
        self.ocr_task_combo.currentIndexChanged.connect(self._sync_ocr_task_fields)
        self._sync_ocr_mode_fields()
        self._sync_ocr_task_fields()
        return tab

    def _pick_ocr_command(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose OCR Runner",
            "",
            "OCR Runner (*.bat *.cmd *.exe *.ps1);;All Files (*)",
        )
        if path:
            self.ocr_command_input.setText(path)

    def _pick_ocr_input_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose OCR Input Folder", "")
        if folder:
            self.ocr_input_dir_input.setText(folder)

    def _pick_ocr_schema(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose OCR Schema / Instructions File",
            "",
            "Text Files (*.txt *.json *.md);;All Files (*)",
        )
        if path:
            self.ocr_schema_input.setText(path)

    def _pick_poppler_path(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Poppler bin folder", "")
        if folder:
            self.ocr_poppler_input.setText(folder)

    def _sync_ocr_mode_fields(self) -> None:
        if not hasattr(self, "ocr_source_combo"):
            return
        source_mode = self.ocr_source_combo.currentData()
        needs_folder = source_mode != "current_pdf"
        self.ocr_input_dir_input.setEnabled(needs_folder)
        self.ocr_pick_input_btn.setEnabled(needs_folder)
        supports_page_selection = source_mode != "images_folder"
        if hasattr(self, "ocr_pages_input"):
            self.ocr_pages_input.setEnabled(supports_page_selection)

    def _sync_ocr_task_fields(self) -> None:
        if not hasattr(self, "ocr_task_combo"):
            return
        task = self.ocr_task_combo.currentText()
        is_custom = task == "custom"
        is_extract = task == "extract"
        self.ocr_custom_prompt_edit.setEnabled(is_custom)
        self.ocr_schema_input.setEnabled(is_extract)
        self.ocr_pick_schema_btn.setEnabled(is_extract)

    def _append_ocr_log(self, text: str) -> None:
        if not hasattr(self, "ocr_log_view"):
            return
        self.ocr_log_view.moveCursor(QTextCursor.End)
        self.ocr_log_view.insertPlainText(text)
        self.ocr_log_view.moveCursor(QTextCursor.End)

    def _reset_ocr_job_tracking(self) -> None:
        self._ocr_process = None
        self._ocr_job_active = False
        self._ocr_completion_marker = None
        if self._ocr_completion_timer.isActive():
            self._ocr_completion_timer.stop()

    def _resolve_ocr_output_files(self) -> tuple[Path | None, list[Path]]:
        out_dir = self._last_ocr_output_dir
        fmt = self._last_ocr_format
        if not out_dir or not fmt:
            return out_dir, []

        found_files = [path for path in self._last_ocr_expected_outputs if path.exists()]
        if not found_files and out_dir.exists():
            found_files = sorted(out_dir.glob(f"*.{fmt}"), key=lambda path: path.name.lower())
        return out_dir, found_files

    def _complete_ocr_job(self, exit_code: int, source: str) -> None:
        self.statusBar().showMessage(f"OCR finished (exit code {exit_code}).", 10000)
        self._append_ocr_log(f"\n[OCR COMPLETE] source={source} exit_code={exit_code}\n")

        out_dir, found_files = self._resolve_ocr_output_files()
        if not out_dir:
            self._reset_ocr_job_tracking()
            return

        if not found_files:
            self.ocr_open_output_btn.setEnabled(bool(out_dir))
            self._append_ocr_log(f"[WARNING] No OCR output files found in: {out_dir}\n")
            self._reset_ocr_job_tracking()
            if exit_code == 0:
                QMessageBox.information(
                    self,
                    "OCR Complete",
                    f"OCR finished.\n\nNo output file was detected yet.\n\nOutput folder:\n{out_dir}",
                )
            else:
                QMessageBox.warning(
                    self,
                    "OCR Failed",
                    f"OCR finished with exit code {exit_code}.\n\nOutput folder:\n{out_dir}",
                )
            return

        self._last_ocr_output_file = found_files[0]
        self.ocr_open_output_btn.setEnabled(True)
        self._append_ocr_log(f"[OCR OUTPUT FILES] {len(found_files)} file(s)\n")
        for path in found_files[:20]:
            self._append_ocr_log(f"- {path.name}\n")
        if len(found_files) > 20:
            self._append_ocr_log(f"... and {len(found_files) - 20} more\n")

        preview_file = found_files[0]
        try:
            content = preview_file.read_text(encoding="utf-8", errors="replace")
            if len(content) > 40000:
                content = content[:40000] + "\n\n...(truncated preview)...\n"
            self._append_ocr_log(f"\n[OCR OUTPUT PREVIEW] {preview_file.name}\n")
            self._append_ocr_log(content)
        except Exception as exc:
            self._append_ocr_log(f"\n[WARNING] Could not read OCR output: {exc}\n")

        self._reset_ocr_job_tracking()
        if exit_code == 0:
            QMessageBox.information(
                self,
                "OCR Complete",
                f"OCR finished successfully.\n\nOutput folder:\n{out_dir}",
            )
        else:
            QMessageBox.warning(
                self,
                "OCR Finished With Errors",
                f"OCR finished with exit code {exit_code}.\n\nOutput folder:\n{out_dir}",
            )

    def _check_live_ocr_completion(self) -> None:
        marker = self._ocr_completion_marker
        if not marker or not marker.exists():
            return

        try:
            raw_exit_code = marker.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            raw_exit_code = ""

        if not raw_exit_code:
            return

        exit_code = int(raw_exit_code) if raw_exit_code.lstrip("-").isdigit() else 1
        self._complete_ocr_job(exit_code, source="live_terminal")

    def _run_ocr_on_current_pdf(self) -> None:
        try:
            source = self._require_current_pdf()

            if self._ocr_process is not None and self._ocr_process.state() != QProcess.NotRunning:
                raise PdfToolkitError("OCR is already running.")

            command = (self.ocr_command_input.text() if hasattr(self, "ocr_command_input") else "").strip()
            if not command:
                raise PdfToolkitError("Set OCR Command first (see Readme.txt).")

            task = self.ocr_task_combo.currentText() if hasattr(self, "ocr_task_combo") else "text"
            fmt = self.ocr_format_combo.currentText() if hasattr(self, "ocr_format_combo") else "md"
            dpi = int(self.ocr_dpi_spin.value()) if hasattr(self, "ocr_dpi_spin") else 200
            max_pages = int(self.ocr_max_pages_spin.value()) if hasattr(self, "ocr_max_pages_spin") else 1500
            poppler_path = (self.ocr_poppler_input.text() if hasattr(self, "ocr_poppler_input") else "").strip()

            # Persist settings
            self.state_store.set_ui("ocr_command", command)
            self.state_store.set_ui("ocr_task", task)
            self.state_store.set_ui("ocr_format", fmt)
            self.state_store.set_ui("ocr_dpi", dpi)
            self.state_store.set_ui("ocr_max_pages", max_pages)
            self.state_store.set_ui("ocr_poppler_path", poppler_path)

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_dir = output_root() / f"{source.stem}_ocr_{stamp}"
            input_dir = run_dir / "input"
            output_dir = run_dir / "output"
            input_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            # Copy the PDF into OCR input folder
            target_pdf = input_dir / source.name
            shutil.copy2(str(source), str(target_pdf))

            job_name = f"{source.stem}_ocr_{stamp}"

            args: list[str] = [
                "--input_dir",
                str(input_dir),
                "--output_dir",
                str(output_dir),
                "--mode",
                "pdf",
                "--job_name",
                job_name,
                "--task",
                task,
                "--output_format",
                fmt,
                "--dpi",
                str(dpi),
                "--max_pages",
                str(max_pages),
            ]
            if poppler_path:
                args.extend(["--poppler_path", poppler_path])
            launch_command, launch_args, launch_note = self._resolve_ocr_launch(command, args)

            self._last_ocr_output_dir = output_dir
            self._last_ocr_job_name = job_name
            self._last_ocr_format = fmt
            self._last_ocr_output_file = None
            self.ocr_open_output_btn.setEnabled(False)

            if hasattr(self, "ocr_log_view"):
                self.ocr_log_view.clear()
                self._append_ocr_log(f"[OCR] Source: {source}\n")
                self._append_ocr_log(f"[OCR] Output: {output_dir}\n")
                self._append_ocr_log(f"[OCR] Command: {launch_command}\n")
                self._append_ocr_log(f"[OCR] Args: {' '.join(launch_args)}\n")
                if launch_note:
                    self._append_ocr_log(launch_note)
                self._append_ocr_log("\n")

            proc = QProcess(self)
            proc_env = QProcessEnvironment.systemEnvironment()
            proc_env.insert("PYTHONUNBUFFERED", "1")
            proc_env.insert("PYTHONIOENCODING", "utf-8")
            proc_env.insert("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            proc.setProcessEnvironment(proc_env)
            proc.setProcessChannelMode(QProcess.SeparateChannels)
            proc.readyReadStandardOutput.connect(lambda: self._append_ocr_log(bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")))
            proc.readyReadStandardError.connect(lambda: self._append_ocr_log(bytes(proc.readAllStandardError()).decode("utf-8", errors="replace")))
            proc.finished.connect(self._on_ocr_finished)

            # Windows: run bat/cmd via cmd.exe
            suffix = Path(launch_command).suffix.lower()
            if os.name == "nt" and (suffix in {".bat", ".cmd"} or suffix == ""):
                cmd_part = launch_command
                if " " in cmd_part and not cmd_part.startswith('"'):
                    cmd_part = f'"{cmd_part}"'
                proc.start("cmd.exe", ["/c", cmd_part, *launch_args])
            else:
                proc.start(launch_command, launch_args)

            if not proc.waitForStarted(4000):
                raise PdfToolkitError("Failed to start OCR process. Check OCR Command and PATH.")

            self._ocr_process = proc
            self.statusBar().showMessage("OCR running…")
        except Exception as exc:
            self._show_error(exc)

    def _on_ocr_finished(self, exit_code: int, _exit_status) -> None:
        self.statusBar().showMessage(f"OCR finished (exit code {exit_code}).")
        proc = self._ocr_process
        self._ocr_process = None

        out_dir = self._last_ocr_output_dir
        job_name = self._last_ocr_job_name
        fmt = self._last_ocr_format
        if not out_dir or not job_name or not fmt:
            return

        out_file = out_dir / f"{job_name}.{fmt}"
        if out_file.exists():
            self._last_ocr_output_file = out_file
            self.ocr_open_output_btn.setEnabled(True)
            try:
                content = out_file.read_text(encoding="utf-8", errors="replace")
                if len(content) > 40000:
                    content = content[:40000] + "\n\n…(truncated preview)…\n"
                self._append_ocr_log("\n\n[OCR OUTPUT PREVIEW]\n")
                self._append_ocr_log(content)
            except Exception as exc:
                self._append_ocr_log(f"\n[WARNING] Could not read OCR output: {exc}\n")
        else:
            self.ocr_open_output_btn.setEnabled(bool(out_dir))
            self._append_ocr_log(f"\n[WARNING] Expected output not found: {out_file}\n")

    def _open_last_ocr_output_folder(self) -> None:
        if not self._last_ocr_output_dir:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_ocr_output_dir)))

    def _run_ocr_job(self) -> None:
        try:
            if self._ocr_job_active or (self._ocr_process is not None and self._ocr_process.state() != QProcess.NotRunning):
                raise PdfToolkitError("OCR is already running.")

            command = (self.ocr_command_input.text() if hasattr(self, "ocr_command_input") else "").strip()
            if not command:
                raise PdfToolkitError("Set OCR Command first.")

            source_mode = self.ocr_source_combo.currentData() if hasattr(self, "ocr_source_combo") else "current_pdf"
            task = self.ocr_task_combo.currentText() if hasattr(self, "ocr_task_combo") else "text"
            fmt = self.ocr_format_combo.currentText() if hasattr(self, "ocr_format_combo") else "md"
            pdf_mode = self.ocr_pdf_mode_combo.currentText() if hasattr(self, "ocr_pdf_mode_combo") else "pages"
            use_fast = self.ocr_use_fast_combo.currentText() if hasattr(self, "ocr_use_fast_combo") else "true"
            model_id = (self.ocr_model_input.text() if hasattr(self, "ocr_model_input") else "").strip() or "zai-org/GLM-OCR"
            job_name_override = (self.ocr_job_name_input.text() if hasattr(self, "ocr_job_name_input") else "").strip()
            custom_prompt = self.ocr_custom_prompt_edit.toPlainText().strip() if hasattr(self, "ocr_custom_prompt_edit") else ""
            schema_path = (self.ocr_schema_input.text() if hasattr(self, "ocr_schema_input") else "").strip()
            pages_selection = (self.ocr_pages_input.text() if hasattr(self, "ocr_pages_input") else "").strip()
            dpi = int(self.ocr_dpi_spin.value()) if hasattr(self, "ocr_dpi_spin") else 200
            batch_pages = int(self.ocr_batch_pages_spin.value()) if hasattr(self, "ocr_batch_pages_spin") else 8
            max_pages = int(self.ocr_max_pages_spin.value()) if hasattr(self, "ocr_max_pages_spin") else 1500
            max_side = int(self.ocr_max_side_spin.value()) if hasattr(self, "ocr_max_side_spin") else 1800
            max_new_tokens = int(self.ocr_max_new_tokens_spin.value()) if hasattr(self, "ocr_max_new_tokens_spin") else 2048
            poppler_path = (self.ocr_poppler_input.text() if hasattr(self, "ocr_poppler_input") else "").strip()
            per_page_files = bool(self.ocr_per_page_box.isChecked()) if hasattr(self, "ocr_per_page_box") else False
            force_fp16 = bool(self.ocr_force_fp16_box.isChecked()) if hasattr(self, "ocr_force_fp16_box") else False
            trust_remote_code = bool(self.ocr_trust_remote_code_box.isChecked()) if hasattr(self, "ocr_trust_remote_code_box") else False
            live_terminal = bool(self.ocr_live_terminal_box.isChecked()) if hasattr(self, "ocr_live_terminal_box") else False

            if task == "custom" and not custom_prompt:
                raise PdfToolkitError("Task 'custom' requires a custom prompt.")
            if task == "extract" and not schema_path:
                raise PdfToolkitError("Task 'extract' requires a schema/instructions file.")
            if pages_selection and pdf_mode == "direct":
                raise PdfToolkitError("Page selection requires PDF Mode 'pages'.")
            if pages_selection and source_mode == "images_folder":
                raise PdfToolkitError("Page selection is only available for PDF OCR modes.")

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            expected_outputs: list[Path] = []
            run_dir: Path
            input_dir: Path
            output_dir: Path
            script_mode: str
            job_name: str | None = None

            if source_mode == "current_pdf":
                source = self._require_current_pdf()
                run_dir = output_root() / f"{source.stem}_ocr_{stamp}"
                input_dir = run_dir / "input"
                output_dir = run_dir / "output"
                input_dir.mkdir(parents=True, exist_ok=True)
                output_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(source), str(input_dir / source.name))
                script_mode = "pdf"
                job_name = job_name_override or f"{source.stem}_ocr_{stamp}"
                expected_outputs = [output_dir / f"{job_name}.{fmt}"]
                source_label = str(source)
            else:
                input_dir_text = (self.ocr_input_dir_input.text() if hasattr(self, "ocr_input_dir_input") else "").strip()
                if not input_dir_text:
                    raise PdfToolkitError("Choose an input folder for folder OCR modes.")
                input_dir = Path(input_dir_text).expanduser().resolve()
                if not input_dir.exists() or not input_dir.is_dir():
                    raise PdfToolkitError(f"OCR input folder not found: {input_dir}")

                folder_name = input_dir.name or "ocr_input"
                run_dir = output_root() / f"{folder_name}_ocr_{stamp}"
                output_dir = run_dir / "output"
                output_dir.mkdir(parents=True, exist_ok=True)

                script_mode_map = {
                    "auto_folder": "auto",
                    "pdf_folder": "pdf",
                    "images_folder": "images",
                    "all_pdfs_folder": "all_pdfs",
                }
                script_mode = script_mode_map.get(str(source_mode), "auto")
                if script_mode != "all_pdfs":
                    job_name = job_name_override or f"{folder_name}_ocr_{stamp}"
                    expected_outputs = [output_dir / f"{job_name}.{fmt}"]
                source_label = str(input_dir)

            self.state_store.set_ui("ocr_command", command)
            self.state_store.set_ui("ocr_source_mode", source_mode)
            self.state_store.set_ui("ocr_input_dir", str(self.ocr_input_dir_input.text() if hasattr(self, "ocr_input_dir_input") else ""))
            self.state_store.set_ui("ocr_pdf_mode", pdf_mode)
            self.state_store.set_ui("ocr_task", task)
            self.state_store.set_ui("ocr_format", fmt)
            self.state_store.set_ui("ocr_use_fast", use_fast)
            self.state_store.set_ui("ocr_model", model_id)
            self.state_store.set_ui("ocr_job_name", job_name_override)
            self.state_store.set_ui("ocr_custom_prompt", custom_prompt)
            self.state_store.set_ui("ocr_schema", schema_path)
            self.state_store.set_ui("ocr_pages", pages_selection)
            self.state_store.set_ui("ocr_dpi", dpi)
            self.state_store.set_ui("ocr_batch_pages", batch_pages)
            self.state_store.set_ui("ocr_max_pages", max_pages)
            self.state_store.set_ui("ocr_max_side", max_side)
            self.state_store.set_ui("ocr_max_new_tokens", max_new_tokens)
            self.state_store.set_ui("ocr_poppler_path", poppler_path)
            self.state_store.set_ui("ocr_per_page_files", per_page_files)
            self.state_store.set_ui("ocr_force_fp16", force_fp16)
            self.state_store.set_ui("ocr_trust_remote_code", trust_remote_code)
            self.state_store.set_ui("ocr_live_terminal", live_terminal)

            args: list[str] = [
                "--input_dir",
                str(input_dir),
                "--output_dir",
                str(output_dir),
                "--mode",
                script_mode,
                "--pdf_mode",
                pdf_mode,
                "--model",
                model_id,
                "--use_fast",
                use_fast,
                "--task",
                task,
                "--output_format",
                fmt,
                "--dpi",
                str(dpi),
                "--batch_pages",
                str(batch_pages),
                "--max_side",
                str(max_side),
                "--max_new_tokens",
                str(max_new_tokens),
            ]
            if pages_selection:
                args.extend(["--pages", pages_selection])
            else:
                args.extend(["--max_pages", str(max_pages)])
            if job_name:
                args.extend(["--job_name", job_name])
            if poppler_path:
                args.extend(["--poppler_path", poppler_path])
            if custom_prompt:
                args.extend(["--custom_prompt", custom_prompt])
            if schema_path:
                args.extend(["--schema", schema_path])
            if per_page_files:
                args.append("--per_page_files")
            if force_fp16:
                args.append("--force_fp16")
            if trust_remote_code:
                args.append("--trust_remote_code")
            launch_command, launch_args, launch_note = self._resolve_ocr_launch(command, args)

            self._last_ocr_output_dir = output_dir
            self._last_ocr_output_file = None
            self._last_ocr_expected_outputs = expected_outputs
            self._last_ocr_job_name = job_name
            self._last_ocr_format = fmt
            self.ocr_open_output_btn.setEnabled(False)

            self.ocr_log_view.clear()
            self._append_ocr_log(f"[OCR] Source: {source_label}\n")
            self._append_ocr_log(f"[OCR] Output: {output_dir}\n")
            self._append_ocr_log(f"[OCR] Command: {launch_command}\n")
            self._append_ocr_log(f"[OCR] Args: {' '.join(launch_args)}\n")
            if launch_note:
                self._append_ocr_log(launch_note)
            self._append_ocr_log("\n")
            if pages_selection:
                self._append_ocr_log(f"[OCR] Page selection: {pages_selection}\n")
            self._append_ocr_log(f"[OCR] Live terminal: {'on' if live_terminal else 'off'}\n\n")

            suffix = Path(launch_command).suffix.lower()
            if live_terminal and os.name == "nt":
                launcher_script = run_dir / "run_ocr_live.cmd"
                completion_marker = run_dir / "ocr_live_exit_code.txt"
                if suffix in {".bat", ".cmd"}:
                    launch_line = "call " + subprocess.list2cmdline([launch_command, *launch_args])
                elif suffix == ".ps1":
                    launch_line = subprocess.list2cmdline(
                        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", launch_command, *launch_args]
                    )
                else:
                    launch_line = subprocess.list2cmdline([launch_command, *launch_args])

                try:
                    launcher_script.write_text(
                        "\r\n".join(
                            [
                                "@echo off",
                                "title HOME PDF OCR",
                                f'cd /d "{Path.cwd()}"',
                                "echo [OCR] Live terminal started.",
                                launch_line,
                                'set "EXIT_CODE=%ERRORLEVEL%"',
                                f'> "{completion_marker}" echo %EXIT_CODE%',
                                "echo.",
                                "echo [OCR] Finished with exit code %EXIT_CODE%.",
                                "pause",
                                "exit /b %EXIT_CODE%",
                                "",
                            ]
                        ),
                        encoding="ascii",
                    )
                    os.startfile(str(launcher_script))
                except OSError as exc:
                    raise PdfToolkitError(f"Failed to open OCR terminal window: {exc}") from exc

                self.ocr_open_output_btn.setEnabled(True)
                self._ocr_job_active = True
                self._ocr_completion_marker = completion_marker
                self._ocr_completion_timer.start()
                self._append_ocr_log("[OCR] Live terminal launched. Follow progress in that window.\n")
                self._append_ocr_log(f"[OCR] Launcher: {launcher_script}\n")
                self._append_ocr_log("[OCR] Output preview will be available after the job completes.\n")
                self.statusBar().showMessage("OCR running in external terminal...")
                return

            if live_terminal and os.name != "nt":
                self._append_ocr_log("[OCR] Live terminal is only supported on Windows; using in-app log instead.\n")

            proc = QProcess(self)
            proc.setProcessChannelMode(QProcess.SeparateChannels)
            proc.readyReadStandardOutput.connect(
                lambda: self._append_ocr_log(bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace"))
            )
            proc.readyReadStandardError.connect(
                lambda: self._append_ocr_log(bytes(proc.readAllStandardError()).decode("utf-8", errors="replace"))
            )
            proc.finished.connect(self._on_ocr_job_finished)

            if os.name == "nt" and suffix in {".bat", ".cmd"}:
                cmd_part = launch_command
                if " " in cmd_part and not cmd_part.startswith('"'):
                    cmd_part = f'"{cmd_part}"'
                proc.start("cmd.exe", ["/c", cmd_part, *launch_args])
            elif os.name == "nt" and suffix == ".ps1":
                proc.start("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", launch_command, *launch_args])
            else:
                proc.start(launch_command, launch_args)

            if not proc.waitForStarted(4000):
                raise PdfToolkitError("Failed to start OCR process. Check OCR Command and PATH.")

            self._ocr_process = proc
            self._ocr_job_active = True
            self.statusBar().showMessage("OCR running...")
        except Exception as exc:
            self._reset_ocr_job_tracking()
            self._show_error(exc)

    def _on_ocr_job_finished(self, exit_code: int, _exit_status) -> None:
        self._complete_ocr_job(exit_code, source="integrated")

    def _build_security_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)

        self.protect_user_password = QLineEdit()
        self.protect_user_password.setEchoMode(QLineEdit.Password)
        self.protect_user_password.setPlaceholderText("Required user password")
        self.protect_owner_password = QLineEdit()
        self.protect_owner_password.setEchoMode(QLineEdit.Password)
        self.protect_owner_password.setPlaceholderText("Owner password (optional)")

        self.allow_print_box = QCheckBox("Allow printing")
        self.allow_print_box.setChecked(True)
        self.allow_copy_box = QCheckBox("Allow copy/extract")
        self.allow_copy_box.setChecked(True)
        self.allow_modify_box = QCheckBox("Allow editing")
        self.allow_modify_box.setChecked(False)
        self.allow_annotate_box = QCheckBox("Allow annotations")
        self.allow_annotate_box.setChecked(True)

        protect_btn = QPushButton("Protect PDF")
        protect_btn.clicked.connect(self._protect_pdf)
        layout.addWidget(QLabel("Protect with Password"))
        layout.addWidget(self.protect_user_password)
        layout.addWidget(self.protect_owner_password)
        layout.addWidget(self.allow_print_box)
        layout.addWidget(self.allow_copy_box)
        layout.addWidget(self.allow_modify_box)
        layout.addWidget(self.allow_annotate_box)
        layout.addWidget(protect_btn)

        self.unlock_password = QLineEdit()
        self.unlock_password.setEchoMode(QLineEdit.Password)
        self.unlock_password.setPlaceholderText("Password to unlock")
        unlock_btn = QPushButton("Unlock PDF")
        unlock_btn.clicked.connect(self._unlock_pdf)
        layout.addWidget(QLabel("Remove Password"))
        layout.addWidget(self.unlock_password)
        layout.addWidget(unlock_btn)
        layout.addStretch(1)
        return tab

    def _build_enhance_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)

        self.watermark_text = QLineEdit("CONFIDENTIAL")
        self.watermark_pages = QLineEdit("1-")
        self.watermark_pages.setPlaceholderText("Pages for watermark, e.g. 1- or 2,4")
        self.watermark_opacity = QDoubleSpinBox()
        self.watermark_opacity.setRange(0.05, 1.0)
        self.watermark_opacity.setSingleStep(0.05)
        self.watermark_opacity.setValue(0.2)

        mark_btn = QPushButton("Apply Text Watermark")
        mark_btn.clicked.connect(self._watermark_pdf)
        compress_btn = QPushButton("Optimize / Compress PDF")
        compress_btn.clicked.connect(self._compress_pdf)

        layout.addWidget(QLabel("Watermark Text"))
        layout.addWidget(self.watermark_text)
        layout.addWidget(self.watermark_pages)
        layout.addWidget(self.watermark_opacity)
        layout.addWidget(mark_btn)
        layout.addSpacing(6)
        layout.addWidget(compress_btn)
        layout.addSpacing(10)

        layout.addWidget(QLabel("Annotate Text Matches"))
        self.annotate_query_input = QLineEdit()
        self.annotate_query_input.setPlaceholderText("Text to mark")
        annotate_row = QHBoxLayout()
        self.annotate_pages_input = QLineEdit("1-")
        self.annotate_pages_input.setPlaceholderText("Pages")
        self.annotate_style = QComboBox()
        self.annotate_style.addItems(["Highlight", "Underline", "Strikeout"])
        annotate_row.addWidget(self.annotate_pages_input)
        annotate_row.addWidget(self.annotate_style)
        annotate_btn = QPushButton("Annotate Matches")
        annotate_btn.clicked.connect(self._annotate_matches)
        layout.addWidget(self.annotate_query_input)
        layout.addLayout(annotate_row)
        layout.addWidget(annotate_btn)

        layout.addSpacing(8)
        layout.addWidget(QLabel("Redact Text Matches"))
        self.redact_query_input = QLineEdit()
        self.redact_query_input.setPlaceholderText("Text to redact permanently")
        self.redact_pages_input = QLineEdit("1-")
        self.redact_pages_input.setPlaceholderText("Pages")
        redact_btn = QPushButton("Redact Matches")
        redact_btn.clicked.connect(self._redact_matches)
        layout.addWidget(self.redact_query_input)
        layout.addWidget(self.redact_pages_input)
        layout.addWidget(redact_btn)

        layout.addSpacing(8)
        layout.addWidget(QLabel("Stamp / Signature Image"))
        stamp_pick_row = QHBoxLayout()
        self.stamp_image_path = QLineEdit()
        self.stamp_image_path.setPlaceholderText("Choose PNG/JPG signature or stamp")
        pick_stamp_btn = QPushButton("Browse")
        pick_stamp_btn.clicked.connect(self._pick_stamp_image)
        stamp_pick_row.addWidget(self.stamp_image_path)
        stamp_pick_row.addWidget(pick_stamp_btn)
        stamp_opts_row = QHBoxLayout()
        self.stamp_pages_input = QLineEdit("1-")
        self.stamp_pages_input.setPlaceholderText("Pages")
        self.stamp_anchor = QComboBox()
        self.stamp_anchor.addItems(["Bottom Right", "Bottom Left", "Top Right", "Top Left", "Center"])
        self.stamp_scale = QDoubleSpinBox()
        self.stamp_scale.setRange(0.05, 0.9)
        self.stamp_scale.setSingleStep(0.01)
        self.stamp_scale.setValue(0.22)
        stamp_opts_row.addWidget(self.stamp_pages_input)
        stamp_opts_row.addWidget(self.stamp_anchor)
        stamp_opts_row.addWidget(self.stamp_scale)
        apply_stamp_btn = QPushButton("Apply Stamp")
        apply_stamp_btn.clicked.connect(self._apply_stamp_image)
        layout.addLayout(stamp_pick_row)
        layout.addLayout(stamp_opts_row)
        layout.addWidget(apply_stamp_btn)
        layout.addStretch(1)
        return tab

    @staticmethod
    def _path_token(path: Path) -> str:
        return str(path.resolve()).lower()

    @classmethod
    def _format_tab_title(cls, path: Path) -> str:
        name = path.name
        max_chars = cls._MAX_TAB_TITLE_CHARS
        if len(name) <= max_chars:
            return name
        tail = max(6, min(11, len(path.suffix) + 4))
        head = max(5, max_chars - tail - 3)
        return f"{name[:head]}...{name[-tail:]}"

    def _read_recent_file_paths(self) -> list[Path]:
        raw = self.state_store.get_ui("recent_files", [])
        if not isinstance(raw, list):
            return []
        result: list[Path] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, str):
                continue
            candidate = Path(item)
            if not candidate.exists():
                continue
            token = self._path_token(candidate)
            if token in seen:
                continue
            seen.add(token)
            result.append(candidate)
        return result

    def _save_recent_file_paths(self, paths: list[Path]) -> None:
        cleaned: list[str] = []
        seen: set[str] = set()
        for path in paths:
            token = self._path_token(path)
            if token in seen:
                continue
            seen.add(token)
            cleaned.append(str(path.resolve()))
            if len(cleaned) >= self._MAX_RECENT_FILES:
                break
        self.state_store.set_ui("recent_files", cleaned)

    def _add_recent_file(self, path: Path) -> None:
        resolved = path.resolve()
        existing = [p for p in self._read_recent_file_paths() if self._path_token(p) != self._path_token(resolved)]
        merged = [resolved]
        merged.extend(existing)
        self._save_recent_file_paths(merged)
        self._refresh_recent_files_menu()

    def _clear_recent_files(self) -> None:
        self.state_store.set_ui("recent_files", [])
        self._refresh_recent_files_menu()
        self.statusBar().showMessage("File history cleared.")

    def _refresh_recent_files_menu(self) -> None:
        if not hasattr(self, "recent_files_menu"):
            return
        self.recent_files_menu.clear()
        paths = self._read_recent_file_paths()
        if not paths:
            placeholder = QAction("(No history)", self)
            placeholder.setEnabled(False)
            self.recent_files_menu.addAction(placeholder)
            return
        self._save_recent_file_paths(paths)

        for path in paths:
            label = path.name
            action = QAction(label, self)
            action.setToolTip(str(path))
            action.setStatusTip(str(path))
            action.triggered.connect(lambda _checked=False, p=path: self.open_documents([p]))
            self.recent_files_menu.addAction(action)
        self.recent_files_menu.addSeparator()
        clear_action = QAction("Clear History", self)
        clear_action.triggered.connect(self._clear_recent_files)
        self.recent_files_menu.addAction(clear_action)

    def _save_tab_session(self) -> None:
        open_paths: list[str] = []
        for idx in range(self.document_tabs.count()):
            tab_path = self._tab_path(idx)
            if tab_path is None:
                continue
            open_paths.append(str(tab_path.resolve()))
        self.state_store.set_ui("session_open_tabs", open_paths)

        active = self._tab_path(self.document_tabs.currentIndex())
        self.state_store.set_ui("session_active_tab", str(active.resolve()) if active is not None else "")

    def restore_previous_session(self) -> bool:
        raw_paths = self.state_store.get_ui("session_open_tabs", [])
        if not isinstance(raw_paths, list) or not raw_paths:
            return False

        paths: list[Path] = []
        seen: set[str] = set()
        for item in raw_paths:
            if not isinstance(item, str):
                continue
            candidate = Path(item).expanduser()
            if candidate.suffix.lower() != ".pdf" or not candidate.exists():
                continue
            token = self._path_token(candidate)
            if token in seen:
                continue
            seen.add(token)
            paths.append(candidate.resolve())

        if not paths:
            return False

        self.open_documents(paths, record_doc_history=False)
        active_raw = self.state_store.get_ui("session_active_tab", "")
        if isinstance(active_raw, str) and active_raw.strip():
            active_idx = self._find_document_tab(Path(active_raw))
            if active_idx >= 0:
                self.document_tabs.setCurrentIndex(active_idx)
        return True

    def _tab_path(self, index: int) -> Path | None:
        if index < 0 or index >= self.document_tabs.count():
            return None
        data = self.document_tabs.tabData(index)
        if not data:
            return None
        return Path(str(data))

    def _find_document_tab(self, path: Path) -> int:
        target = self._path_token(path)
        for idx in range(self.document_tabs.count()):
            tab_path = self._tab_path(idx)
            if tab_path is not None and self._path_token(tab_path) == target:
                return idx
        return -1

    def _sync_document_tabs_visibility(self) -> None:
        self.document_tabs.setVisible(self.document_tabs.count() > 0)

    def _ensure_document_tab(self, path: Path) -> int:
        resolved = path.resolve()
        existing = self._find_document_tab(resolved)
        if existing >= 0:
            self.document_tabs.setTabText(existing, self._format_tab_title(resolved))
            self.document_tabs.setTabToolTip(existing, str(resolved))
            self.document_tabs.setTabData(existing, str(resolved))
            self._sync_document_tabs_visibility()
            return existing

        index = self.document_tabs.addTab(self._format_tab_title(resolved))
        self.document_tabs.setTabToolTip(index, str(resolved))
        self.document_tabs.setTabData(index, str(resolved))
        self._sync_document_tabs_visibility()
        self._save_tab_session()
        return index

    def _sync_active_tab_with_current_pdf(self) -> None:
        if self.current_pdf is None:
            return
        index = self._ensure_document_tab(self.current_pdf)
        self.document_tabs.setTabText(index, self._format_tab_title(self.current_pdf))
        self.document_tabs.setTabToolTip(index, str(self.current_pdf))
        self.document_tabs.setTabData(index, str(self.current_pdf))
        if self.document_tabs.currentIndex() != index:
            self.document_tabs.blockSignals(True)
            self.document_tabs.setCurrentIndex(index)
            self.document_tabs.blockSignals(False)
        self._save_tab_session()

    def _set_active_document_tab(self, path: Path, *, record_doc_history: bool) -> None:
        index = self._ensure_document_tab(path)
        if self.document_tabs.currentIndex() == index:
            self._open_pdf(path, record_doc_history=record_doc_history)
            return
        self._tab_change_record_doc_history = bool(record_doc_history)
        self.document_tabs.setCurrentIndex(index)

    def open_documents(self, paths: list[Path], *, record_doc_history: bool = True) -> None:
        normalized: list[Path] = []
        for raw in paths:
            path = Path(raw).expanduser()
            if path.suffix.lower() != ".pdf":
                continue
            if not path.exists():
                continue
            resolved = path.resolve()
            if all(self._path_token(resolved) != self._path_token(existing) for existing in normalized):
                normalized.append(resolved)
        if not normalized:
            return

        for path in normalized:
            self._ensure_document_tab(path)
        self._set_active_document_tab(normalized[-1], record_doc_history=record_doc_history)
        self._save_tab_session()

    def _on_document_tab_changed(self, index: int) -> None:
        if index < 0:
            return
        path = self._tab_path(index)
        if path is None:
            return

        record_doc_history = self._tab_change_record_doc_history
        self._tab_change_record_doc_history = False

        if self.current_pdf is not None and self._path_token(self.current_pdf) == self._path_token(path):
            self._save_tab_session()
            return

        opened = self._open_pdf(path, record_doc_history=record_doc_history)
        if opened:
            self._save_tab_session()
            return

        self.document_tabs.blockSignals(True)
        self.document_tabs.removeTab(index)
        self.document_tabs.blockSignals(False)
        self._sync_document_tabs_visibility()
        if self.document_tabs.count() <= 0:
            self._clear_document_workspace()
            return
        current = max(0, min(self.document_tabs.currentIndex(), self.document_tabs.count() - 1))
        self._on_document_tab_changed(current)

    def _close_document_tab(self, index: int) -> None:
        if index < 0 or index >= self.document_tabs.count():
            return
        self.document_tabs.removeTab(index)
        self._sync_document_tabs_visibility()
        self._save_tab_session()
        if self.document_tabs.count() > 0:
            return
        self._clear_document_workspace()

    def _close_current_document_tab(self) -> None:
        self._close_document_tab(self.document_tabs.currentIndex())

    def _clear_document_workspace(self) -> None:
        self._save_current_document_state()
        if self.current_doc is not None:
            self.current_doc.close()
            self.current_doc = None
        self.current_pdf = None
        self.page_order = []
        self.continuous_view.configure([], [], 1.0)
        self.current_page_index = 0
        self.zoom_factor = 1.0
        self.fit_mode = "width"
        self._clear_preview_cache()
        self._inflight_renders.clear()
        self.renderer.invalidate()
        self._clear_search()
        self._converted_text_cache = ""
        self._converted_text_doc_token = ""
        self._text_search_spans = []
        self._text_search_cursor = -1
        self.thumbnail_list.clear()
        self._thumbnail_items_by_page.clear()
        self.outline_list.clear()
        self.outline_targets = []
        self.meta_label.setText("No document loaded.")
        self.page_image.clear()
        self.page_image.setText("Open a PDF to preview")
        self.page_text_view.clear()
        self.page_stack.setCurrentWidget(self.page_image)
        self._set_scroll_content_widget(self.page_stack)
        self._set_display_size(860, 1160)
        self.page_label.setText("Page - / -")
        self.page_jump_spin.blockSignals(True)
        self.page_jump_spin.setRange(1, 1)
        self.page_jump_spin.setValue(1)
        self.page_jump_spin.blockSignals(False)
        self._update_zoom_label()
        self._save_tab_session()
        self.statusBar().showMessage("Ready. Drop a PDF or click Open PDF.")

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        files = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf")
        ]
        if not files:
            return
        self.open_documents(files)

    def _pick_open_pdf(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Open PDF", "", "PDF Files (*.pdf)")
        if paths:
            self.open_documents([Path(path) for path in paths])

    def _open_pdf(self, pdf_path: Path, *, record_doc_history: bool = True) -> bool:
        try:
            self._save_current_document_state()
            if self.current_doc is not None:
                self.current_doc.close()
                self.current_doc = None
            self.current_pdf = pdf_path.resolve()
            self.current_doc = fitz.open(str(self.current_pdf))
            if self.current_doc.needs_pass:
                self.current_doc.close()
                self.current_doc = None
                self.page_order = []
                self.current_page_index = 0
                self.continuous_view.configure([], [], 1.0)
                self.page_stack.setCurrentWidget(self.page_image)
                self._set_scroll_content_widget(self.page_stack)
                self._set_display_size(max(220, self.page_scroll.viewport().width()),
                                       max(220, self.page_scroll.viewport().height()))
                self._clear_preview_cache()
                self._inflight_renders.clear()
                self.renderer.invalidate()
                self._clear_search()
                self.thumbnail_list.clear()
                self._thumbnail_items_by_page.clear()
                self.outline_list.clear()
                self.outline_targets = []
                self.page_image.clear()
                self.page_image.setText("Encrypted preview is locked. Use Security -> Unlock PDF.")
                self.page_label.setText("Page - / -")
                self._sync_page_jump_controls()
                self.meta_label.setText(
                    f"File: {self.current_pdf.name}\nEncrypted: Yes\nPreview: Locked until unlocked"
                )
                self._sync_active_tab_with_current_pdf()
                self._add_recent_file(self.current_pdf)
                self.statusBar().showMessage("Encrypted PDF loaded. Unlock it to preview/edit.")
                return True

            self._clear_preview_cache()
            self._inflight_renders.clear()
            self.renderer.invalidate()
            self._clear_search()
            self._converted_text_cache = ""
            self._converted_text_doc_token = ""
            self._text_search_spans = []
            self._text_search_cursor = -1
            self.current_page_index = 0
            self.continuous_view.configure([], [], 1.0)
            self.zoom_factor = 1.0
            self.fit_mode = "width"
            self._load_metadata()
            self._load_thumbnails()
            self._load_outline()
            self._history_reset(0)
            self._restore_document_state_or_default()
            if record_doc_history:
                self._push_operation_document(self.current_pdf)
            self._sync_active_tab_with_current_pdf()
            self._add_recent_file(self.current_pdf)
            self.statusBar().showMessage(f"Loaded {self.current_pdf.name}")
            return True
        except Exception as exc:
            self.current_doc = None
            self.current_pdf = None
            self._clear_document_workspace()
            self._show_error(exc)
            return False

    def _load_metadata(self) -> None:
        if not self.current_pdf:
            return
        info = self.toolkit.inspect(self.current_pdf)
        details = [
            f"File: {info.path.name}",
            f"Pages: {info.page_count}",
            f"Encrypted: {'Yes' if info.encrypted else 'No'}",
        ]
        if info.title:
            details.append(f"Title: {info.title}")
        if info.author:
            details.append(f"Author: {info.author}")
        self.meta_label.setText("\n".join(details))

    def _load_thumbnails(self) -> None:
        self.thumbnail_list.blockSignals(True)
        self.thumbnail_list.clear()
        self._thumbnail_items_by_page.clear()
        if not self.current_doc:
            self.thumbnail_list.blockSignals(False)
            return

        self.page_order = list(range(self.current_doc.page_count))
        for i in range(self.current_doc.page_count):
            item = QListWidgetItem(f"{i + 1}")
            item.setData(Qt.UserRole, i)
            self.thumbnail_list.addItem(item)
            self._thumbnail_items_by_page[i] = item

        self.thumbnail_list.blockSignals(False)
        self._sync_page_jump_controls()
        self._schedule_visible_thumbnail_renders()

    def _history_reset(self, page_index: int) -> None:
        self.nav_history = [max(0, page_index)]
        self.nav_history_cursor = 0

    def _history_push(self, page_index: int) -> None:
        if self._suspend_history:
            return
        page = max(0, page_index)
        if self.nav_history and self.nav_history_cursor >= 0:
            current = self.nav_history[self.nav_history_cursor]
            if current == page:
                return
            self.nav_history = self.nav_history[: self.nav_history_cursor + 1]
        else:
            self.nav_history = []
        self.nav_history.append(page)
        self.nav_history_cursor = len(self.nav_history) - 1

    def _history_back(self) -> None:
        if self.current_doc is None or self.nav_history_cursor <= 0:
            return
        self.nav_history_cursor -= 1
        target = self.nav_history[self.nav_history_cursor]
        self._suspend_history = True
        try:
            self._set_page(target, record_history=False)
        finally:
            self._suspend_history = False

    def _history_forward(self) -> None:
        if self.current_doc is None or self.nav_history_cursor < 0:
            return
        if self.nav_history_cursor >= len(self.nav_history) - 1:
            return
        self.nav_history_cursor += 1
        target = self.nav_history[self.nav_history_cursor]
        self._suspend_history = True
        try:
            self._set_page(target, record_history=False)
        finally:
            self._suspend_history = False

    def _focus_widget_can_undo_redo(self) -> tuple[bool, QWidget | None]:
        focused = self.focusWidget()
        if isinstance(focused, QLineEdit):
            return True, focused
        if isinstance(focused, QTextEdit) and not focused.isReadOnly():
            return True, focused
        return False, focused

    def _push_operation_document(self, pdf_path: Path) -> None:
        resolved = pdf_path.resolve()
        if self.operation_doc_history and 0 <= self.operation_doc_history_cursor < len(self.operation_doc_history):
            current = self.operation_doc_history[self.operation_doc_history_cursor]
            if current == resolved:
                self._update_operation_history_actions()
                return
            self.operation_doc_history = self.operation_doc_history[: self.operation_doc_history_cursor + 1]

        self.operation_doc_history.append(resolved)
        if len(self.operation_doc_history) > 120:
            self.operation_doc_history = self.operation_doc_history[-120:]
        self.operation_doc_history_cursor = len(self.operation_doc_history) - 1
        self._update_operation_history_actions()

    def _update_operation_history_actions(self) -> None:
        if not hasattr(self, "undo_operation_action"):
            return
        can_undo = self.operation_doc_history_cursor > 0
        can_redo = 0 <= self.operation_doc_history_cursor < len(self.operation_doc_history) - 1
        self.undo_operation_action.setEnabled(can_undo)
        self.redo_operation_action.setEnabled(can_redo)

    def _undo_file_operation(self) -> None:
        can_delegate, focused = self._focus_widget_can_undo_redo()
        if can_delegate and focused is not None and hasattr(focused, "undo"):
            focused.undo()  # type: ignore[call-arg]
            return
        if self.operation_doc_history_cursor <= 0:
            return
        target_cursor = self.operation_doc_history_cursor - 1
        target = self.operation_doc_history[target_cursor]
        if not target.exists():
            self.statusBar().showMessage(f"Undo target is missing: {target.name}")
            return
        previous_cursor = self.operation_doc_history_cursor
        self.operation_doc_history_cursor = target_cursor
        self._update_operation_history_actions()
        self._set_active_document_tab(target, record_doc_history=False)
        if self.current_pdf is None or self._path_token(self.current_pdf) != self._path_token(target):
            self.operation_doc_history_cursor = previous_cursor
            self._update_operation_history_actions()
            return
        self.statusBar().showMessage(f"Undo file operation -> {target.name}")

    def _redo_file_operation(self) -> None:
        can_delegate, focused = self._focus_widget_can_undo_redo()
        if can_delegate and focused is not None and hasattr(focused, "redo"):
            focused.redo()  # type: ignore[call-arg]
            return
        if self.operation_doc_history_cursor < 0:
            return
        if self.operation_doc_history_cursor >= len(self.operation_doc_history) - 1:
            return
        target_cursor = self.operation_doc_history_cursor + 1
        target = self.operation_doc_history[target_cursor]
        if not target.exists():
            self.statusBar().showMessage(f"Redo target is missing: {target.name}")
            return
        previous_cursor = self.operation_doc_history_cursor
        self.operation_doc_history_cursor = target_cursor
        self._update_operation_history_actions()
        self._set_active_document_tab(target, record_doc_history=False)
        if self.current_pdf is None or self._path_token(self.current_pdf) != self._path_token(target):
            self.operation_doc_history_cursor = previous_cursor
            self._update_operation_history_actions()
            return
        self.statusBar().showMessage(f"Redo file operation -> {target.name}")

    def _set_view_mode_silent(self, mode: str) -> None:
        normalized = "continuous" if mode == "continuous" else "single"
        self.view_mode = normalized
        self.view_mode_combo.blockSignals(True)
        self.view_mode_combo.setCurrentText("Continuous" if normalized == "continuous" else "Single")
        self.view_mode_combo.blockSignals(False)

    def _restore_document_state_or_default(self) -> None:
        if self.current_doc is None or self.current_pdf is None:
            return
        total = len(self.page_order) if self.page_order else self.current_doc.page_count
        if total <= 0:
            return

        restored = self.state_store.get_document(self.current_pdf)
        if restored is None:
            self._set_view_mode_silent(self.view_mode)
            if self.view_mode == "continuous" and self.text_tool_mode != "view":
                self._set_text_tool_mode("view")
            self._fit_width()
            self._history_reset(self.current_page_index)
            return

        self._set_view_mode_silent(restored.view_mode)
        if self.view_mode == "continuous" and self.text_tool_mode != "view":
            self._set_text_tool_mode("view")
        self.current_page_index = max(0, min(total - 1, restored.page))

        if restored.fit_mode == "page":
            self.fit_mode = "page"
            self._fit_page()
        elif restored.fit_mode == "width":
            self.fit_mode = "width"
            self._fit_width()
        else:
            self.fit_mode = "manual"
            self.zoom_factor = max(0.2, min(6.0, restored.zoom))
            self._refresh_view()

        self._set_page(self.current_page_index, record_history=False)
        self._history_reset(self.current_page_index)

    def _schedule_state_save(self) -> None:
        if self.current_pdf is None or self.current_doc is None:
            return
        self._state_save_timer.start(600)

    def _save_current_document_state(self) -> None:
        if self.current_pdf is None or self.current_doc is None:
            return
        state = DocumentViewState(
            page=max(0, self.current_page_index),
            zoom=max(0.2, min(6.0, self.zoom_factor)),
            fit_mode=self.fit_mode,
            view_mode=self.view_mode,
        )
        self.state_store.set_document(self.current_pdf, state)

    def _refresh_thumbnail_labels(self) -> None:
        for row in range(self.thumbnail_list.count()):
            item = self.thumbnail_list.item(row)
            if item is not None:
                item.setText(str(row + 1))

    def _sync_page_order_from_thumbnails(self) -> None:
        order: list[int] = []
        for row in range(self.thumbnail_list.count()):
            item = self.thumbnail_list.item(row)
            if item is None:
                continue
            data = item.data(Qt.UserRole)
            if isinstance(data, int):
                order.append(data)
        if order:
            self.page_order = order

    def _on_thumbnail_reordered(self, *_args) -> None:
        self._sync_page_order_from_thumbnails()
        self._refresh_thumbnail_labels()
        self._sync_page_jump_controls()
        if self.current_doc is not None and self.current_page_index >= len(self.page_order):
            self.current_page_index = max(0, len(self.page_order) - 1)
        self._sync_outline_selection()
        self._rebuild_search_sequence_from_hits()
        self._schedule_visible_thumbnail_renders()
        self._refresh_view()
        self._schedule_state_save()

    def _on_view_mode_changed(self, text: str) -> None:
        mode = "continuous" if text.lower().startswith("continuous") else "single"
        if mode == self.view_mode:
            return
        self.view_mode = mode
        self.state_store.set_ui("default_view_mode", self.view_mode)
        if self.view_mode == "continuous" and self.text_tool_mode != "view":
            self._set_text_tool_mode("view")
            return
        self._refresh_view()
        self._schedule_state_save()

    def _set_display_size(self, width: int, height: int) -> None:
        w = max(120, int(width))
        h = max(120, int(height))
        self.page_stack.setFixedSize(w, h)
        self.page_image.setFixedSize(w, h)
        self.page_text_view.setFixedSize(w, h)

    def _set_scroll_content_widget(self, widget: QWidget) -> None:
        current = self.page_scroll.widget()
        if current is widget:
            return
        old = self.page_scroll.takeWidget()
        if old is not None:
            old.setParent(None)
        self.page_scroll.setWidget(widget)

    def _active_doc_token(self) -> str:
        return str(self.current_pdf or "")

    def _cache_key(self, index: int, zoom: float, quality: float) -> tuple[str, int, float, float]:
        return (self._active_doc_token(), index, round(zoom, 3), round(quality, 2))

    def _cache_get_image(self, key: tuple[str, int, float, float]) -> QImage | None:
        image = self.preview_cache.get(key)
        if image is None:
            return None
        self.preview_cache.move_to_end(key)
        return image

    def _clear_preview_cache(self) -> None:
        self.preview_cache.clear()
        self._preview_cache_sizes.clear()
        self._preview_cache_bytes = 0

    def _cache_put_image(self, key: tuple[str, int, float, float], image: QImage) -> None:
        previous_size = self._preview_cache_sizes.get(key, 0)
        if previous_size:
            self._preview_cache_bytes = max(0, self._preview_cache_bytes - previous_size)
        self.preview_cache[key] = image
        size_bytes = max(1, int(image.sizeInBytes()))
        self._preview_cache_sizes[key] = size_bytes
        self._preview_cache_bytes += size_bytes
        self.preview_cache.move_to_end(key)
        while len(self.preview_cache) > self._preview_cache_limit or (
            self._preview_cache_bytes > self._preview_cache_max_bytes
        ):
            old_key, _ = self.preview_cache.popitem(last=False)
            freed = self._preview_cache_sizes.pop(old_key, 0)
            if freed:
                self._preview_cache_bytes = max(0, self._preview_cache_bytes - freed)

    def _render_page_image_sync(self, index: int, zoom: float, quality: float) -> QImage | None:
        if self.current_doc is None:
            return None
        page = self.current_doc.load_page(index)
        matrix = fitz.Matrix(zoom * quality, zoom * quality)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        return QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()

    @staticmethod
    def _image_to_pixmap(image: QImage, quality: float) -> QPixmap:
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(quality)
        return pixmap

    def _request_render(self, key: tuple[str, int, float, float], priority: int = 0) -> None:
        if self.current_pdf is None:
            return
        _, page_index, zoom, quality = key
        self.renderer.queue_render(
            key=key,
            pdf_path=self.current_pdf,
            page_index=page_index,
            zoom=zoom,
            quality=quality,
            priority=priority,
        )

    def _get_or_request_page_image(self, index: int, zoom: float) -> QImage | None:
        quality = self._effective_render_quality(index, zoom)
        key = self._cache_key(index, zoom, quality)
        image = self._cache_get_image(key)
        if image is not None:
            return image
        self._request_render(key)
        for previous, cached in reversed(self.preview_cache.items()):
            if previous[:2] == key[:2] and not self._is_thumbnail_key(previous):
                return cached
        return None

    def _prefetch_neighbor_pages(self, center_row_index: int, span: int = 2) -> None:
        if self.current_doc is None:
            return
        total = len(self.page_order) if self.page_order else self.current_doc.page_count
        for delta in range(1, span + 1):
            for row in (center_row_index - delta, center_row_index + delta):
                if row < 0 or row >= total:
                    continue
                actual_idx = self.page_order[row] if self.page_order else row
                quality = self._effective_render_quality(actual_idx, self.zoom_factor)
                key = self._cache_key(actual_idx, self.zoom_factor, quality)
                if self._cache_get_image(key) is None:
                    self._request_render(key, priority=1)

    def _thumbnail_key(self, actual_idx: int) -> tuple[str, int, float, float]:
        return self._cache_key(actual_idx, self._thumb_zoom, self._thumb_quality)

    def _is_thumbnail_key(self, key: tuple[str, int, float, float]) -> bool:
        return key[2] == round(self._thumb_zoom, 3) and key[3] == round(self._thumb_quality, 2)

    def _update_thumbnail_icon(self, actual_idx: int, image: QImage) -> None:
        item = self._thumbnail_items_by_page.get(actual_idx)
        if item is None:
            return
        pixmap = QPixmap.fromImage(image)
        icon_pixmap = pixmap.scaled(
            self.thumbnail_list.iconSize(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        item.setIcon(QIcon(icon_pixmap))

    def _schedule_visible_thumbnail_renders(self) -> None:
        if self.current_doc is None:
            return
        self._thumbnail_render_timer.start(40)

    def _queue_visible_thumbnail_renders(self) -> None:
        if self.current_doc is None or not self.thumbnail_list.isVisible():
            return
        count = self.thumbnail_list.count()
        if count <= 0:
            return

        viewport = self.thumbnail_list.viewport()
        top_row = self.thumbnail_list.indexAt(QPoint(6, 6)).row()
        bottom_row = self.thumbnail_list.indexAt(QPoint(6, max(6, viewport.height() - 6))).row()
        if top_row < 0:
            top_row = max(0, self.current_page_index - 6)
        if bottom_row < 0:
            bottom_row = min(count - 1, top_row + 14)

        start = max(0, top_row - 6)
        end = min(count - 1, bottom_row + 6)
        for row in range(start, end + 1):
            item = self.thumbnail_list.item(row)
            if item is None:
                continue
            actual_idx = item.data(Qt.UserRole)
            if not isinstance(actual_idx, int):
                continue
            key = self._thumbnail_key(actual_idx)
            image = self._cache_get_image(key)
            if image is not None:
                self._update_thumbnail_icon(actual_idx, image)
            else:
                self._request_render(key, priority=2)

    def _ensure_converted_text_loaded(self) -> None:
        if self.current_doc is None:
            self.page_text_view.clear()
            self._converted_text_cache = ""
            self._converted_text_doc_token = ""
            return
        token = self._active_doc_token()
        if token == self._converted_text_doc_token and self._converted_text_cache:
            return

        parts: list[str] = []
        order = self.page_order if self.page_order else list(range(self.current_doc.page_count))
        for display_idx, actual_idx in enumerate(order, start=1):
            page = self.current_doc.load_page(actual_idx)
            text = page.get_text("text").strip()
            if text:
                parts.append(f"Page {display_idx}\n{text}")
            else:
                parts.append(f"Page {display_idx}\n[No text detected on this page]")
        content = "\n\n".join(parts).strip()
        if not content:
            content = "[No text detected in this PDF]"
        self._converted_text_cache = content
        self._converted_text_doc_token = token
        self.page_text_view.setPlainText(self._converted_text_cache)

    def _apply_text_search_highlights(self) -> None:
        if not self._convert_text_active():
            self.page_text_view.setExtraSelections([])
            return
        if not self._text_search_spans:
            self.page_text_view.setExtraSelections([])
            return

        selections: list[QTextEdit.ExtraSelection] = []
        for idx, (start, end) in enumerate(self._text_search_spans):
            cursor = self.page_text_view.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            if idx == self._text_search_cursor:
                selection.format.setBackground(QColor(255, 190, 56, 205))
            else:
                selection.format.setBackground(QColor(255, 231, 114, 115))
            selections.append(selection)
        self.page_text_view.setExtraSelections(selections)

    def _focus_text_search_cursor(self) -> None:
        if not self._text_search_spans:
            return
        if self._text_search_cursor < 0 or self._text_search_cursor >= len(self._text_search_spans):
            self._text_search_cursor = 0
        start, end = self._text_search_spans[self._text_search_cursor]
        cursor = self.page_text_view.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        self.page_text_view.setTextCursor(cursor)
        self.page_text_view.ensureCursorVisible()
        self._apply_text_search_highlights()

    def _execute_text_search(self, query: str) -> None:
        if not self._convert_text_active():
            return
        self._ensure_converted_text_loaded()
        text = self.page_text_view.toPlainText()
        lowered = text.lower()
        needle = query.lower()
        spans: list[tuple[int, int]] = []
        start = 0
        while True:
            idx = lowered.find(needle, start)
            if idx < 0:
                break
            spans.append((idx, idx + len(needle)))
            start = idx + max(1, len(needle))
        self._text_search_spans = spans
        if not spans:
            self._text_search_cursor = -1
            self._update_search_result_label()
            self._apply_text_search_highlights()
            self.statusBar().showMessage("No matches found.")
            return
        self._text_search_cursor = 0
        self._update_search_result_label()
        self._focus_text_search_cursor()

    def _search_highlights_for_page(self, page_index: int) -> list[tuple[float, float, float, float]]:
        return self.search_hits.get(page_index, [])

    def _active_search_hit(self) -> tuple[int, int] | None:
        if self._convert_text_active():
            return None
        if self.search_cursor < 0 or self.search_cursor >= len(self.search_sequence):
            return None
        _, page_index, local_index = self.search_sequence[self.search_cursor]
        return (page_index, local_index)

    def _apply_search_highlights(
        self,
        pixmap: QPixmap,
        *,
        page_index: int,
        zoom: float,
        quality: float,
    ) -> QPixmap:
        rects = self.search_hits.get(page_index)
        if not rects:
            return pixmap

        active_rect_index = -1
        if 0 <= self.search_cursor < len(self.search_sequence):
            _, active_page, active_local = self.search_sequence[self.search_cursor]
            if active_page == page_index:
                active_rect_index = active_local

        overlay = pixmap.copy()
        painter = QPainter(overlay)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # QPainter uses device-independent coordinates when pixmap has DPR set.
        scale = zoom
        for idx, (x0, y0, x1, y1) in enumerate(rects):
            x = int(x0 * scale)
            y = int(y0 * scale)
            w = max(2, int((x1 - x0) * scale))
            h = max(2, int((y1 - y0) * scale))
            if idx == active_rect_index:
                painter.fillRect(x, y, w, h, QColor(255, 191, 46, 188))
                painter.setPen(QColor(222, 123, 29, 225))
                painter.drawRect(x, y, w, h)
            else:
                painter.fillRect(x, y, w, h, QColor(255, 232, 120, 112))
        painter.end()
        return overlay

    def _show_search_bar(self) -> None:
        self.search_row.setVisible(True)
        self.search_input.setFocus(Qt.ShortcutFocusReason)
        self.search_input.selectAll()

    def _hide_search_bar(self) -> None:
        self.search_row.setVisible(False)
        self._search_timer.stop()
        self.search_input.clear()
        self._clear_search()
        self._refresh_view()

    def _clear_search(self) -> None:
        self.search_hits.clear()
        self.search_sequence.clear()
        self.search_cursor = -1
        self._text_search_spans = []
        self._text_search_cursor = -1
        self.page_text_view.setExtraSelections([])
        self._update_search_result_label()

    def _update_search_result_label(self) -> None:
        if self._convert_text_active():
            if not self._text_search_spans:
                self.search_result_label.setText("0 / 0")
                return
            self.search_result_label.setText(f"{self._text_search_cursor + 1} / {len(self._text_search_spans)}")
            return
        if not self.search_sequence:
            self.search_result_label.setText("0 / 0")
            return
        self.search_result_label.setText(f"{self.search_cursor + 1} / {len(self.search_sequence)}")

    def _rebuild_search_sequence_from_hits(self) -> None:
        if self.current_doc is None or not self.search_hits:
            self.search_sequence = []
            self.search_cursor = -1
            self._update_search_result_label()
            return

        order = self.page_order if self.page_order else list(range(self.current_doc.page_count))
        sequence: list[tuple[int, int, int]] = []
        for display_idx, actual_idx in enumerate(order):
            rects = self.search_hits.get(actual_idx)
            if not rects:
                continue
            for local_idx, _ in enumerate(rects):
                sequence.append((display_idx, actual_idx, local_idx))

        self.search_sequence = sequence
        if not self.search_sequence:
            self.search_cursor = -1
        else:
            self.search_cursor = max(0, min(self.search_cursor, len(self.search_sequence) - 1))
        self._update_search_result_label()

    def _execute_search(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            self._cancel_async_search()
            self._clear_search()
            self._refresh_view()
            return
        if self.current_doc is None or self.current_pdf is None:
            self._cancel_async_search()
            self._clear_search()
            return
        if self._convert_text_active():
            self._cancel_async_search()
            self.search_hits.clear()
            self.search_sequence.clear()
            self.search_cursor = -1
            self._execute_text_search(query)
            return

        self._start_async_search(query)

    def _cancel_async_search(self) -> None:
        self._active_search_job_id = 0
        self._active_search_query = ""
        self._active_search_doc_token = ""
        future = getattr(self, "_active_search_future", None)
        if future is not None:
            try:
                future.cancel()
            except Exception:
                pass
        self._active_search_future = None

    def _start_async_search(self, query: str) -> None:
        if self.current_doc is None or self.current_pdf is None:
            return
        order = self.page_order if self.page_order else list(range(self.current_doc.page_count))

        self._search_job_counter += 1
        job_id = self._search_job_counter
        doc_token = self._active_doc_token()

        self._active_search_job_id = job_id
        self._active_search_query = query
        self._active_search_doc_token = doc_token

        if hasattr(self, "search_result_label"):
            self.search_result_label.setText("… / …")
        self.statusBar().showMessage("Searching…")

        future = get_process_pool().submit(search_pdf_text, str(self.current_pdf), order, query)
        self._active_search_future = future

        def _done(fut) -> None:
            try:
                result: SearchResult = fut.result()
                self._async_bridge.searchCompleted.emit((job_id, doc_token, query, result))
            except Exception as exc:
                self._async_bridge.searchFailed.emit((job_id, doc_token, query, str(exc)))

        future.add_done_callback(_done)

    def _on_async_search_completed(self, payload: object) -> None:
        try:
            job_id, doc_token, query, result = payload
        except Exception:
            return
        if job_id != self._active_search_job_id:
            return
        if doc_token != self._active_doc_token():
            return
        if query != self.search_input.text().strip():
            return
        if not isinstance(result, SearchResult):
            return

        self.search_hits = dict(result.hits)
        self.search_sequence = list(result.sequence)

        if not self.search_sequence:
            self.search_cursor = -1
            self.statusBar().showMessage("No matches found.")
            self._update_search_result_label()
            self._refresh_view()
            return

        start_cursor = 0
        for idx, (display_idx, _, _) in enumerate(self.search_sequence):
            if display_idx >= self.current_page_index:
                start_cursor = idx
                break
        self.search_cursor = start_cursor
        self._update_search_result_label()
        self._focus_search_cursor()

    def _on_async_search_failed(self, payload: object) -> None:
        try:
            job_id, doc_token, query, message = payload
        except Exception:
            return
        if job_id != self._active_search_job_id:
            return
        if doc_token != self._active_doc_token():
            return
        if query != self.search_input.text().strip():
            return

        self.search_hits.clear()
        self.search_sequence.clear()
        self.search_cursor = -1
        self.statusBar().showMessage(f"Search error: {message}")
        self._update_search_result_label()
        self._refresh_view()


    def _search_next(self) -> None:
        if self._convert_text_active():
            if not self._text_search_spans:
                if self.search_input.text().strip():
                    self._execute_search()
                return
            self._text_search_cursor = (self._text_search_cursor + 1) % len(self._text_search_spans)
            self._update_search_result_label()
            self._focus_text_search_cursor()
            return
        if not self.search_sequence:
            if self.search_input.text().strip():
                self._execute_search()
            return
        self.search_cursor = (self.search_cursor + 1) % len(self.search_sequence)
        self._update_search_result_label()
        self._focus_search_cursor()

    def _search_prev(self) -> None:
        if self._convert_text_active():
            if not self._text_search_spans:
                if self.search_input.text().strip():
                    self._execute_search()
                return
            self._text_search_cursor = (self._text_search_cursor - 1) % len(self._text_search_spans)
            self._update_search_result_label()
            self._focus_text_search_cursor()
            return
        if not self.search_sequence:
            if self.search_input.text().strip():
                self._execute_search()
            return
        self.search_cursor = (self.search_cursor - 1) % len(self.search_sequence)
        self._update_search_result_label()
        self._focus_search_cursor()

    def _focus_search_cursor(self) -> None:
        if self._convert_text_active():
            self._focus_text_search_cursor()
            return
        if not self.search_sequence:
            return
        display_idx, actual_idx, local_idx = self.search_sequence[self.search_cursor]
        rects = self.search_hits.get(actual_idx) or []
        if local_idx < 0 or local_idx >= len(rects):
            return
        x0, y0, x1, _ = rects[local_idx]

        if self.view_mode == "continuous":
            self.current_page_index = display_idx
            page_top = self.continuous_view.page_top(display_idx)
            scroll_y = page_top + int(y0 * self.zoom_factor) - 64
            self.page_scroll.verticalScrollBar().setValue(max(0, scroll_y))
            if self.thumbnail_list.currentRow() != display_idx:
                self.thumbnail_list.blockSignals(True)
                self.thumbnail_list.setCurrentRow(display_idx)
                self.thumbnail_list.blockSignals(False)
            total = len(self.page_order) if self.page_order else (self.current_doc.page_count if self.current_doc else 0)
            self.page_label.setText(f"Page {display_idx + 1} / {total}")
            self.continuous_view.update()
            return

        self._set_page(display_idx, record_history=False)
        vbar = self.page_scroll.verticalScrollBar()
        hbar = self.page_scroll.horizontalScrollBar()
        vbar.setValue(max(0, min(vbar.maximum(), int(y0 * self.zoom_factor) - 64)))
        hbar.setValue(max(0, min(hbar.maximum(), int(x0 * self.zoom_factor) - 36)))

    def _capture_reading_anchor(self) -> tuple[int, float, float] | None:
        if self.current_doc is None:
            return None
        vbar = self.page_scroll.verticalScrollBar()
        hbar = self.page_scroll.horizontalScrollBar()
        if self.page_scroll.widget() is self.continuous_view and self.continuous_view.page_count():
            row = self.continuous_view.page_at_offset(vbar.value() + 8)
            scale = max(0.001, self.continuous_view._zoom)
            offset = (vbar.value() - self.continuous_view.page_top(row)) / scale
            return row, offset, hbar.value() / scale
        if self.page_scroll.widget() is self.page_stack:
            scale = max(0.001, getattr(self, '_single_display_zoom', self.zoom_factor))
            return self.current_page_index, vbar.value() / scale, hbar.value() / scale
        return None

    def _restore_reading_anchor(self, anchor: tuple[int, float, float] | None) -> None:
        if anchor is None or self.current_doc is None:
            return
        page, y, x = anchor
        self.current_page_index = max(0, min(len(self.page_order) - 1, page))
        top = self.continuous_view.page_top(self.current_page_index) if self.view_mode == 'continuous' else 0
        vbar = self.page_scroll.verticalScrollBar()
        hbar = self.page_scroll.horizontalScrollBar()
        vbar.blockSignals(True)
        hbar.blockSignals(True)
        vbar.setValue(max(0, top + round(y * self.zoom_factor)))
        hbar.setValue(max(0, round(x * self.zoom_factor)))
        vbar.blockSignals(False)
        hbar.blockSignals(False)
        self.page_label.setText(f'Page {self.current_page_index + 1} / {len(self.page_order)}')
        self._sync_page_jump_controls()

    def _refresh_view(self) -> None:
        if self.current_doc is None:
            return
        signature = (self._active_doc_token(), round(self.zoom_factor, 3))
        if getattr(self, "_render_signature", None) != signature:
            self.renderer.invalidate()
            self._render_signature = signature
        anchor = self._capture_reading_anchor()
        if self.view_mode == "continuous":
            self.page_stack.setCurrentWidget(self.page_image)
            self._set_scroll_content_widget(self.continuous_view)
            self._render_continuous_document()
            if anchor is not None:
                self._restore_reading_anchor(anchor)
            return
        self._set_scroll_content_widget(self.page_stack)
        self._set_page(self.current_page_index)
        self._restore_reading_anchor(anchor)

    def _render_continuous_document(self) -> None:
        if self.current_doc is None:
            return

        page_count = len(self.page_order) if self.page_order else self.current_doc.page_count
        if page_count == 0:
            return

        page_indices: list[int] = []
        page_sizes: list[QSize] = []
        for idx in range(page_count):
            actual_idx = self.page_order[idx] if self.page_order else idx
            rect = self.current_doc.load_page(actual_idx).rect
            page_indices.append(actual_idx)
            page_sizes.append(
                QSize(
                    max(80, int(rect.width * self.zoom_factor)),
                    max(80, int(rect.height * self.zoom_factor)),
                )
            )

        self.continuous_view.configure(page_indices, page_sizes, self.zoom_factor)
        self.page_label.setText(f"Page {self.current_page_index + 1} / {page_count}")
        self._update_zoom_label()
        self.page_scroll.horizontalScrollBar().setValue(0)
        self._get_or_request_page_image(page_indices[self.current_page_index], self.zoom_factor)
        self._prefetch_neighbor_pages(self.current_page_index, span=2)

    def _on_view_scroll(self, value: int) -> None:
        if self.view_mode != "continuous" or self.current_doc is None:
            return
        if self.continuous_view.page_count() == 0:
            return
        mid = value + max(1, self.page_scroll.viewport().height() // 2)
        page_index = self.continuous_view.page_at_offset(mid)
        if page_index == self.current_page_index:
            return
        self.current_page_index = page_index
        total = len(self.page_order) if self.page_order else self.current_doc.page_count
        self.page_label.setText(f"Page {self.current_page_index + 1} / {total}")
        self._sync_page_jump_controls()
        self._sync_outline_selection()
        self._prefetch_neighbor_pages(self.current_page_index, span=2)
        self._schedule_state_save()
        if self.thumbnail_list.currentRow() != self.current_page_index:
            self.thumbnail_list.blockSignals(True)
            self.thumbnail_list.setCurrentRow(self.current_page_index)
            self.thumbnail_list.blockSignals(False)
        self._schedule_visible_thumbnail_renders()

    def _set_page(self, index: int, record_history: bool = True) -> None:
        if not self.current_doc:
            return
        total = len(self.page_order) if self.page_order else self.current_doc.page_count
        if index < 0 or index >= total:
            return
        if record_history:
            self._history_push(index)
        self.current_page_index = index
        if self.view_mode == "continuous":
            self.page_label.setText(f"Page {index + 1} / {total}")
            self._sync_page_jump_controls()
            self._sync_outline_selection()
            target_y = self.continuous_view.page_top(index)
            self.page_scroll.verticalScrollBar().setValue(max(0, target_y - 8))
            if self.thumbnail_list.currentRow() != index:
                self.thumbnail_list.blockSignals(True)
                self.thumbnail_list.setCurrentRow(index)
                self.thumbnail_list.blockSignals(False)
            self._schedule_visible_thumbnail_renders()
            self._schedule_state_save()
            return

        actual_idx = self.page_order[index] if self.page_order else index
        page = self.current_doc.load_page(actual_idx)
        target_width = max(80, int(page.rect.width * self.zoom_factor))
        target_height = max(80, int(page.rect.height * self.zoom_factor))
        self._single_display_zoom = self.zoom_factor
        self._set_display_size(target_width, target_height)

        if self._convert_text_active():
            self.page_stack.setCurrentWidget(self.page_text_view)
            self._ensure_converted_text_loaded()
            if self._text_search_spans:
                self._focus_text_search_cursor()
            else:
                cursor = self.page_text_view.textCursor()
                cursor.setPosition(0)
                self.page_text_view.setTextCursor(cursor)
            self.page_image.set_selection_mode(False)
            self.page_image.set_page_words([], self.zoom_factor)
        else:
            self.page_stack.setCurrentWidget(self.page_image)
            self.page_image.set_selection_mode(self._text_select_active())
            if self._text_select_active():
                words = page.get_text("words")
                self.page_image.set_page_words(words, self.zoom_factor)
            else:
                self.page_image.set_page_words([], self.zoom_factor)
            image = self._get_or_request_page_image(actual_idx, self.zoom_factor)
            if image is None:
                self.page_image.clear()
                self.page_image.setText("Rendering page...")
            else:
                quality = round(self._effective_render_quality(actual_idx, self.zoom_factor), 2)
                self._present_single_page_image(actual_idx, image, quality)

        self.page_label.setText(f"Page {index + 1} / {total}")
        self._sync_page_jump_controls()
        self._sync_outline_selection()
        self._update_zoom_label()
        self.page_scroll.horizontalScrollBar().setValue(0)
        self.page_scroll.verticalScrollBar().setValue(0)
        if not self._convert_text_active():
            self._prefetch_neighbor_pages(index, span=2)
        self._schedule_state_save()

        if self.thumbnail_list.currentRow() != index:
            self.thumbnail_list.blockSignals(True)
            self.thumbnail_list.setCurrentRow(index)
            self.thumbnail_list.blockSignals(False)
        self._schedule_visible_thumbnail_renders()

    def _render_page_pixmap(self, index: int, zoom: float) -> QPixmap | None:
        if not self.current_doc:
            return None
        quality = self._effective_render_quality(index, zoom)
        key = self._cache_key(index, zoom, quality)
        image = self._cache_get_image(key)
        if image is None:
            image = self._render_page_image_sync(index, zoom, quality)
            if image is None:
                return None
            self._cache_put_image(key, image)
        return self._image_to_pixmap(image, key[3])

    def _present_single_page_image(self, actual_idx: int, image: QImage, quality: float) -> None:
        pixmap = self._image_to_pixmap(image, quality)
        if self.search_hits:
            pixmap = self._apply_search_highlights(
                pixmap,
                page_index=actual_idx,
                zoom=self.zoom_factor,
                quality=quality,
            )
        self.page_image.setPixmap(pixmap)
        self.page_image.setText("")

    def _on_render_ready(self, key: tuple[str, int, float, float], pixels) -> None:
        self._inflight_renders.discard(key)
        if key[0] != self._active_doc_token():
            return
        image = QImage(pixels.samples, pixels.width, pixels.height, pixels.stride, QImage.Format_RGB888).copy()
        if image.isNull():
            return
        self._cache_put_image(key, image)
        if self._is_thumbnail_key(key):
            self._update_thumbnail_icon(key[1], image)
            return

        if self.view_mode == "continuous":
            self.continuous_view.update_page(key[1])
            return
        if self._convert_text_active() or self.current_doc is None:
            return

        actual_idx = self.page_order[self.current_page_index] if self.page_order else self.current_page_index
        expected = self._cache_key(actual_idx, self.zoom_factor, self._effective_render_quality(actual_idx, self.zoom_factor))
        if expected != key:
            return
        self._present_single_page_image(actual_idx, image, key[3])

    def _on_render_failed(self, key: tuple[str, int, float, float], message: str) -> None:
        self._inflight_renders.discard(key)
        if key[0] != self._active_doc_token():
            return
        if self.view_mode == "single" and not self._convert_text_active():
            actual_idx = self.page_order[self.current_page_index] if self.page_order else self.current_page_index
            expected = self._cache_key(actual_idx, self.zoom_factor, self._effective_render_quality(actual_idx, self.zoom_factor))
            if expected == key:
                self.page_image.clear()
                self.page_image.setText(f"Render failed: {message}")

    def _change_zoom(self, factor: float) -> None:
        if not self.current_doc:
            return
        self.fit_mode = "manual"
        self.zoom_factor = max(0.2, min(6.0, self.zoom_factor * factor))
        self._refresh_view()
        self._schedule_state_save()

    def _fit_page(self) -> None:
        if not self.current_doc:
            return
        self.fit_mode = "page"
        viewport_width = max(120, self.page_scroll.viewport().width() - 34)
        viewport_height = max(120, self.page_scroll.viewport().height() - 28)
        actual_idx = self.page_order[self.current_page_index] if self.page_order else self.current_page_index
        page = self.current_doc.load_page(actual_idx)
        rect = page.rect
        self.zoom_factor = max(0.2, min(6.0, min(viewport_width / rect.width, viewport_height / rect.height)))
        self._refresh_view()
        self._schedule_state_save()

    def _fit_width(self) -> None:
        if not self.current_doc:
            return
        self.fit_mode = "width"
        viewport_width = max(120, self.page_scroll.viewport().width() - 34)
        actual_idx = self.page_order[self.current_page_index] if self.page_order else self.current_page_index
        page = self.current_doc.load_page(actual_idx)
        rect = page.rect
        self.zoom_factor = max(0.2, min(6.0, viewport_width / rect.width))
        self._refresh_view()
        self._schedule_state_save()

    def _actual_size(self) -> None:
        if not self.current_doc:
            return
        self.fit_mode = "manual"
        self.zoom_factor = 1.0
        self._refresh_view()
        self._schedule_state_save()

    def _effective_render_quality(self, page_index: int | None = None, zoom: float | None = None) -> float:
        dpr = max(1.0, float(self.devicePixelRatioF()))
        if self.view_mode == "continuous":
            base = max(1.2, min(2.1, dpr * 1.35))
            min_quality = 1.0
            max_pixels = 14_000_000.0
        else:
            # Keep single-page mode crisp while respecting memory limits.
            base = max(2.0, min(3.2, dpr * 2.1))
            min_quality = 1.2
            max_pixels = 24_000_000.0

        if self.current_doc is None or page_index is None:
            return base

        target_zoom = float(self.zoom_factor if zoom is None else zoom)
        try:
            rect = self.current_doc.load_page(page_index).rect
        except Exception:
            return base

        return bounded_scale(rect.width, rect.height, target_zoom * base) / max(0.001, target_zoom)

    def _toggle_left_panel(self) -> None:
        sizes = self.main_splitter.sizes()
        left_width = sizes[0]
        if left_width > 12:
            self._last_left_size = left_width
            sizes[1] = max(100, sizes[1] + left_width)
            sizes[0] = 0
            self.main_splitter.setSizes(sizes)
        else:
            restored = max(180, min(260, self._last_left_size))
            sizes[0] = restored
            sizes[1] = max(100, sizes[1] - restored)
            self.main_splitter.setSizes(sizes)
        self._sync_panel_toggle_labels()
        self._on_layout_changed()

    def _toggle_right_panel(self) -> None:
        sizes = self.main_splitter.sizes()
        right_width = sizes[2]
        if right_width > 12:
            self._last_right_size = right_width
            sizes[1] = max(100, sizes[1] + right_width)
            sizes[2] = 0
            self.main_splitter.setSizes(sizes)
        else:
            restored = max(300, self._last_right_size)
            sizes[2] = restored
            sizes[1] = max(100, sizes[1] - restored)
            self.main_splitter.setSizes(sizes)
        self._sync_panel_toggle_labels()
        self._on_layout_changed()

    def _toggle_thumbnail_panel(self) -> None:
        self.navigation_combo.setCurrentIndex(0)
        if self.main_splitter.sizes()[0] <= 12:
            self._toggle_left_panel()
        self._schedule_visible_thumbnail_renders()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            return
        self.showFullScreen()

    def _set_text_select_mode(self, enabled: bool) -> None:
        # Backward-compatible wrapper for older call sites.
        self._set_text_tool_mode("select" if enabled else "view")

    def _register_splitter_handles(self, splitter: QSplitter, name: str) -> None:
        for idx in range(1, splitter.count()):
            handle = splitter.handle(idx)
            handle.setProperty("splitter_name", name)
            handle.setProperty("splitter_handle_index", idx)
            handle.installEventFilter(self)

    def _sync_panel_toggle_labels(self) -> None:
        sizes = self.main_splitter.sizes()
        self.left_toggle_btn.setText("Navigation")
        self.right_toggle_btn.setText("Tools")

    def _sync_thumbnail_toggle_label(self) -> None:
        sizes = self.body_split.sizes()
        self.thumb_toggle_btn.setText("Show Thumbs" if sizes[0] <= 12 else "Hide Thumbs")

    def _on_layout_changed(self, *_args) -> None:
        self._sync_panel_toggle_labels()
        self._sync_thumbnail_toggle_label()
        self._layout_state_timer.start(700)
        self._layout_timer.start(180)

    @staticmethod
    def _coerce_sizes(raw: object, count: int) -> list[int] | None:
        if not isinstance(raw, list) or len(raw) != count:
            return None
        values: list[int] = []
        for item in raw:
            try:
                value = int(item)
            except Exception:
                return None
            values.append(max(0, value))
        if sum(values) <= 0:
            return None
        return values

    def _save_layout_state(self) -> None:
        if not hasattr(self, "main_splitter"):
            return
        payload = {
            "main_sizes": [int(v) for v in self.main_splitter.sizes()],
            "body_sizes": [int(v) for v in self.body_split.sizes()],
            "last_left": int(self._last_left_size),
            "last_right": int(self._last_right_size),
            "last_thumb": int(self._last_thumb_size),
        }
        self.state_store.set_ui("layout_state", payload)

    def _restore_layout_state(self) -> None:
        raw_layout = self.state_store.get_ui("layout_state", {})
        layout_payload = raw_layout if isinstance(raw_layout, dict) else {}

        raw_main = layout_payload.get("main_sizes")
        main_sizes = self._coerce_sizes(raw_main, 3)
        if main_sizes is None:
            main_sizes = list(self._default_main_sizes)
        self.main_splitter.setSizes(main_sizes)

        raw_body = layout_payload.get("body_sizes")
        body_sizes = self._coerce_sizes(raw_body, 2)
        if body_sizes is None:
            body_sizes = list(self._default_body_sizes)
        self.body_split.setSizes(body_sizes)

        try:
            self._last_left_size = int(layout_payload.get("last_left", self._default_main_sizes[0]))
        except Exception:
            self._last_left_size = self._default_main_sizes[0]
        try:
            self._last_right_size = int(layout_payload.get("last_right", self._default_main_sizes[2]))
        except Exception:
            self._last_right_size = 340
        try:
            self._last_thumb_size = int(layout_payload.get("last_thumb", self._default_body_sizes[0]))
        except Exception:
            self._last_thumb_size = self._default_body_sizes[0]

        self._sync_panel_toggle_labels()
        self._sync_thumbnail_toggle_label()

    def _restore_layout_defaults(self) -> None:
        self.main_splitter.setSizes(list(self._default_main_sizes))
        self.body_split.setSizes(list(self._default_body_sizes))
        self._last_left_size = self._default_main_sizes[0]
        self._last_right_size = 340
        self._last_thumb_size = self._default_body_sizes[0]
        self._sync_panel_toggle_labels()
        self._sync_thumbnail_toggle_label()
        self._save_layout_state()
        self._layout_timer.start(10)
        self.statusBar().showMessage("Layout restored to defaults.")

    def _save_window_geometry(self) -> None:
        encoded = bytes(self.saveGeometry().toBase64()).decode("ascii", errors="ignore")
        self.state_store.set_ui("window_geometry_b64", encoded)

    def _center_window_on_screen(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.x() + max(0, (area.width() - self.width()) // 2)
        y = area.y() + max(0, (area.height() - self.height()) // 2)
        self.move(x, y)

    def _ensure_window_visible(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        geo = self.frameGeometry()
        x = geo.x()
        y = geo.y()
        if geo.right() < area.left() + 80:
            x = area.left() + 30
        elif geo.left() > area.right() - 80:
            x = max(area.left(), area.right() - geo.width() - 30)
        if geo.bottom() < area.top() + 60:
            y = area.top() + 30
        elif geo.top() > area.bottom() - 60:
            y = max(area.top(), area.bottom() - geo.height() - 30)
        self.move(x, y)

    def _restore_window_geometry(self) -> None:
        raw = self.state_store.get_ui("window_geometry_b64", "")
        restored = False
        if isinstance(raw, str) and raw.strip():
            data = QByteArray.fromBase64(raw.encode("ascii", errors="ignore"))
            if not data.isEmpty():
                restored = bool(self.restoreGeometry(data))
        if restored:
            self._ensure_window_visible()
            return
        self._center_window_on_screen()

    def _apply_fit_after_layout_change(self) -> None:
        if self.current_doc is None:
            return
        if self.fit_mode == "width":
            self._fit_width()
            return
        if self.fit_mode == "page":
            self._fit_page()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if hasattr(self, 'main_splitter'):
            sizes = self.main_splitter.sizes()
            if self.width() < 1100 and sizes[0] > 0:
                sizes[1] += sizes[0]
                sizes[0] = 0
            if self.width() < 1200 and sizes[2] > 0:
                sizes[1] += sizes[2]
                sizes[2] = 0
            self.main_splitter.setSizes(sizes)
        self._on_layout_changed()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._layout_state_timer.stop()
        self._state_save_timer.stop()
        self._save_window_geometry()
        self._save_layout_state()
        self._save_tab_session()
        self._save_current_document_state()
        if self.current_doc is not None:
            self.current_doc.close()
            self.current_doc = None
        super().closeEvent(event)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.KeyPress and self._handle_reader_keypress(event):
            return True

        if watched is getattr(self, "document_tabs", None) and event.type() == QEvent.MouseButtonRelease:
            if event.button() == Qt.MiddleButton:
                point = event.position().toPoint() if hasattr(event, "position") else event.pos()
                tab_index = self.document_tabs.tabAt(point)
                if tab_index >= 0:
                    self._close_document_tab(tab_index)
                    return True

        if event.type() == QEvent.MouseButtonDblClick:
            splitter_name = watched.property("splitter_name") if hasattr(watched, "property") else None
            if splitter_name == "main":
                handle_index = int(watched.property("splitter_handle_index"))
                if handle_index == 1:
                    self._toggle_left_panel()
                elif handle_index == 2:
                    self._toggle_right_panel()
                return True
            if splitter_name == "body":
                self._toggle_thumbnail_panel()
                return True

        thumbnail_viewport = self.thumbnail_list.viewport() if hasattr(self, "thumbnail_list") else None
        if thumbnail_viewport is not None and watched is thumbnail_viewport and event.type() in {
            QEvent.Wheel,
            QEvent.Resize,
            QEvent.Show,
        }:
            self._schedule_visible_thumbnail_renders()

        page_viewport = self.page_scroll.viewport() if hasattr(self, "page_scroll") else None
        if page_viewport is not None and event.type() == QEvent.Wheel and watched is page_viewport:
            if self._handle_single_mode_wheel_edge_transition(event):
                return True

        if event.type() == QEvent.Wheel and (event.modifiers() & Qt.ControlModifier):
            delta = event.angleDelta().y()
            if delta:
                self._change_zoom(1.1 if delta > 0 else (1 / 1.1))
                return True
        return super().eventFilter(watched, event)

    def _parse_zoom_text(self, text: str) -> float | None:
        cleaned = text.strip().replace("%", "")
        if not cleaned:
            return None
        try:
            value = float(cleaned)
        except ValueError:
            return None
        if value <= 0:
            return None
        return value / 100.0

    def _on_zoom_combo_changed(self, *_args) -> None:
        zoom = self._parse_zoom_text(self.zoom_combo.currentText())
        if zoom is None:
            self._update_zoom_label()
            return
        self.fit_mode = "manual"
        self.zoom_factor = max(0.2, min(6.0, zoom))
        if self.current_doc is not None:
            self._set_page(self.current_page_index)
            self._schedule_state_save()
        else:
            self._update_zoom_label()

    def _update_zoom_label(self) -> None:
        if not hasattr(self, "zoom_combo"):
            return
        label = f"{int(round(self.zoom_factor * 100))}%"
        self.zoom_combo.blockSignals(True)
        self.zoom_combo.setEditText(label)
        self.zoom_combo.blockSignals(False)

    def _on_merge_files_dropped(self, paths: list[Path]) -> None:
        for path in paths:
            if path.suffix.lower() == ".pdf":
                self._append_merge_file(path)

    def _sync_merge_sources_from_widget(self) -> None:
        ordered: list[Path] = []
        for row in range(self.merge_list.count()):
            item = self.merge_list.item(row)
            if item is None:
                continue
            ordered.append(Path(item.text()))
        self.merge_sources = ordered

    def _add_merge_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add PDFs to Merge Queue", "", "PDF Files (*.pdf)")
        for path in paths:
            self._append_merge_file(Path(path))

    def _append_merge_file(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved in self.merge_sources:
            return
        self.merge_sources.append(resolved)
        self.merge_list.addItem(str(resolved))

    def _remove_merge_files(self) -> None:
        selected_items = self.merge_list.selectedItems()
        if not selected_items:
            return
        selected_paths = {Path(item.text()) for item in selected_items}
        self.merge_sources = [path for path in self.merge_sources if path not in selected_paths]
        for item in selected_items:
            self.merge_list.takeItem(self.merge_list.row(item))

    def _clear_merge_files(self) -> None:
        self.merge_sources.clear()
        self.merge_list.clear()

    def _merge_queue(self) -> None:
        try:
            if len(self.merge_sources) < 2:
                raise PdfToolkitError("Add at least two PDFs in merge queue.")
            output = self.toolkit.default_output_path(self.merge_sources[0], "merged")
            result = self.toolkit.merge(self.merge_sources, output)
            self._show_result([result], "Merge complete.")
        except Exception as exc:
            self._show_error(exc)

    def _extract_pages_from_ui(self) -> None:
        try:
            source = self._require_current_pdf()
            output = self.toolkit.default_output_path(source, "extract")
            result = self.toolkit.extract_pages(source, self.extract_selection_input.text(), output)
            self._show_result([result], "Pages extracted.")
        except Exception as exc:
            self._show_error(exc)

    def _delete_pages(self) -> None:
        try:
            source = self._require_current_pdf()
            output = self.toolkit.default_output_path(source, "deleted")
            result = self.toolkit.delete_pages(source, self.delete_selection_input.text(), output)
            self._show_result([result], "Pages deleted.")
        except Exception as exc:
            self._show_error(exc)

    def _reorder_pages(self) -> None:
        try:
            source = self._require_current_pdf()
            output = self.toolkit.default_output_path(source, "reordered")
            result = self.toolkit.reorder_pages(source, self.reorder_input.text(), output)
            self._show_result([result], "Pages reordered.")
        except Exception as exc:
            self._show_error(exc)

    def _apply_thumbnail_order(self) -> None:
        try:
            source = self._require_current_pdf()
            if not self.page_order:
                raise PdfToolkitError("No thumbnail order found.")
            identity = list(range(len(self.page_order)))
            if self.page_order == identity:
                raise PdfToolkitError("Thumbnail order is unchanged.")
            order_text = ",".join(str(idx + 1) for idx in self.page_order)
            output = self.toolkit.default_output_path(source, "thumb_reordered")
            result = self.toolkit.reorder_pages(source, order_text, output)
            self._show_result([result], "Applied thumbnail drag order.")
        except Exception as exc:
            self._show_error(exc)

    def _rotate_pages(self) -> None:
        try:
            source = self._require_current_pdf()
            output = self.toolkit.default_output_path(source, "rotated")
            degrees = int(self.rotate_degrees.currentText())
            result = self.toolkit.rotate_pages(
                source,
                self.rotate_selection_input.text(),
                degrees,
                output,
            )
            self._show_result([result], "Rotation complete.")
        except Exception as exc:
            self._show_error(exc)

    def _split_ranges(self) -> None:
        try:
            source = self._require_current_pdf()
            output_dir = output_root() / f"{source.stem}_split_ranges"
            results = self.toolkit.split_by_ranges(source, self.split_ranges_input.text(), output_dir)
            self._show_result(results, f"Created {len(results)} split files.")
        except Exception as exc:
            self._show_error(exc)

    def _split_every(self) -> None:
        try:
            source = self._require_current_pdf()
            output_dir = output_root() / f"{source.stem}_split_every_{self.split_every_spin.value()}"
            results = self.toolkit.split_every(source, self.split_every_spin.value(), output_dir)
            self._show_result(results, f"Created {len(results)} split files.")
        except Exception as exc:
            self._show_error(exc)

    def _convert_document(self) -> None:
        try:
            source = self._require_current_pdf()
            target = self.convert_target.currentText()
            output_dir = output_root() / f"{source.stem}_convert_{target}"
            result = self.toolkit.convert(source, target, output_dir)
            self._show_result(result.outputs, f"Converted to {target}.")
        except Exception as exc:
            self._show_error(exc)

    def _add_to_pdf_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add Files To Convert Into PDF",
            "",
            "Supported Files (*.txt *.md *.rtf *.html *.htm *.docx *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.pdf)",
        )
        for path in paths:
            self._append_to_pdf_source(Path(path))

    def _append_to_pdf_source(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved in self.to_pdf_sources:
            return
        self.to_pdf_sources.append(resolved)
        self.to_pdf_list.addItem(str(resolved))

    def _on_to_pdf_files_dropped(self, paths: list[Path]) -> None:
        for path in paths:
            self._append_to_pdf_source(path)

    def _sync_to_pdf_sources_from_widget(self) -> None:
        ordered: list[Path] = []
        for row in range(self.to_pdf_list.count()):
            item = self.to_pdf_list.item(row)
            if item is None:
                continue
            ordered.append(Path(item.text()))
        self.to_pdf_sources = ordered

    def _remove_to_pdf_files(self) -> None:
        selected_items = self.to_pdf_list.selectedItems()
        if not selected_items:
            return
        selected_paths = {Path(item.text()) for item in selected_items}
        self.to_pdf_sources = [path for path in self.to_pdf_sources if path not in selected_paths]
        for item in selected_items:
            self.to_pdf_list.takeItem(self.to_pdf_list.row(item))

    def _clear_to_pdf_files(self) -> None:
        self.to_pdf_sources.clear()
        self.to_pdf_list.clear()

    def _convert_to_pdf(self) -> None:
        try:
            if not self.to_pdf_sources:
                raise PdfToolkitError("Add at least one source file to convert to PDF.")
            first = self.to_pdf_sources[0]
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output = output_root() / f"{first.stem}_to_pdf_{stamp}.pdf"
            result = self.toolkit.convert_to_pdf(self.to_pdf_sources, output)
            self._show_result([result], "Converted to PDF.")
        except Exception as exc:
            self._show_error(exc)

    def _reflow_text(self) -> None:
        try:
            source = self._require_current_pdf()
            text = self.toolkit.extract_reflow_text(source)
            self.reflow_output.setPlainText(text)
            self.statusBar().showMessage("Reflow text extracted.")
        except Exception as exc:
            self._show_error(exc)

    def _save_reflow_text(self) -> None:
        try:
            source = self._require_current_pdf()
            content = self.reflow_output.toPlainText().strip()
            if not content:
                raise PdfToolkitError("Reflow output is empty.")
            path = self.toolkit.default_output_path(source, "reflow", "txt")
            path.write_text(content, encoding="utf-8")
            self._show_result([path], "Reflow text saved.")
        except Exception as exc:
            self._show_error(exc)

    def _protect_pdf(self) -> None:
        try:
            source = self._require_current_pdf()
            output = self.toolkit.default_output_path(source, "protected")
            opts = ProtectOptions(
                allow_print=self.allow_print_box.isChecked(),
                allow_copy=self.allow_copy_box.isChecked(),
                allow_modify=self.allow_modify_box.isChecked(),
                allow_annotate=self.allow_annotate_box.isChecked(),
            )
            result = self.toolkit.protect(
                source,
                self.protect_user_password.text(),
                self.protect_owner_password.text() or None,
                opts,
                output,
            )
            self._show_result([result], "PDF protected.")
        except Exception as exc:
            self._show_error(exc)

    def _unlock_pdf(self) -> None:
        try:
            source = self._require_current_pdf()
            output = self.toolkit.default_output_path(source, "unlocked")
            result = self.toolkit.unlock(source, self.unlock_password.text(), output)
            self._show_result([result], "PDF unlocked.")
        except Exception as exc:
            self._show_error(exc)

    def _watermark_pdf(self) -> None:
        try:
            source = self._require_current_pdf()
            output = self.toolkit.default_output_path(source, "watermark")
            result = self.toolkit.watermark_text(
                source,
                self.watermark_text.text(),
                output,
                selection=self.watermark_pages.text(),
                opacity=float(self.watermark_opacity.value()),
            )
            self._show_result([result], "Watermark applied.")
        except Exception as exc:
            self._show_error(exc)

    def _compress_pdf(self) -> None:
        try:
            source = self._require_current_pdf()
            output = self.toolkit.default_output_path(source, "optimized")
            result = self.toolkit.compress(source, output)
            self._show_result([result], "Optimization complete.")
        except Exception as exc:
            self._show_error(exc)

    def _annotate_matches(self) -> None:
        try:
            source = self._require_current_pdf()
            style_text = self.annotate_style.currentText().strip().lower()
            output = self.toolkit.default_output_path(source, f"{style_text}_annotated")
            result = self.toolkit.annotate_text_matches(
                source,
                self.annotate_query_input.text(),
                self.annotate_pages_input.text(),
                style_text,
                output,
            )
            self._show_result([result], f"{style_text.title()} annotations added.")
        except Exception as exc:
            self._show_error(exc)

    def _redact_matches(self) -> None:
        try:
            source = self._require_current_pdf()
            output = self.toolkit.default_output_path(source, "redacted")
            result = self.toolkit.redact_text_matches(
                source,
                self.redact_query_input.text(),
                self.redact_pages_input.text(),
                output,
            )
            self._show_result([result], "Redaction complete.")
        except Exception as exc:
            self._show_error(exc)

    def _pick_stamp_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Stamp Image",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)",
        )
        if path:
            self.stamp_image_path.setText(path)

    @staticmethod
    def _anchor_label_to_value(label: str) -> str:
        return label.strip().lower().replace(" ", "-")

    def _apply_stamp_image(self) -> None:
        try:
            source = self._require_current_pdf()
            raw_path = self.stamp_image_path.text().strip()
            if not raw_path:
                raise PdfToolkitError("Choose a stamp image first.")
            image_path = Path(raw_path)
            if not image_path.is_file():
                raise PdfToolkitError(f"Stamp image not found: {image_path}")
            output = self.toolkit.default_output_path(source, "stamped")
            result = self.toolkit.stamp_image(
                source,
                image_path,
                self.stamp_pages_input.text(),
                output,
                anchor=self._anchor_label_to_value(self.stamp_anchor.currentText()),
                width_ratio=float(self.stamp_scale.value()),
            )
            self._show_result([result], "Stamp applied.")
        except Exception as exc:
            self._show_error(exc)

    def _show_result(self, outputs: list[Path], message: str) -> None:
        self.statusBar().showMessage(message)
        if not outputs:
            return
        first = outputs[0]
        details = [message, "", f"Output folder: {first.parent}", "", "Created files:"]
        details.extend(str(path.name) for path in outputs[:10])
        if len(outputs) > 10:
            details.append(f"... and {len(outputs) - 10} more")
        QMessageBox.information(self, "Operation Complete", "\n".join(details))

        if len(outputs) == 1 and outputs[0].suffix.lower() == ".pdf":
            self._set_active_document_tab(outputs[0], record_doc_history=True)

    def _show_error(self, error: Exception) -> None:
        text = str(error) if str(error) else error.__class__.__name__
        self.statusBar().showMessage(f"Error: {text}")
        QMessageBox.critical(self, "Operation Failed", text)

    def _require_current_pdf(self) -> Path:
        if not self.current_pdf:
            raise PdfToolkitError("Open a PDF first.")
        return self.current_pdf
