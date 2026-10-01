from __future__ import annotations

import sys
import multiprocessing
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from pdf_ultimate.ui.main_window import PdfUltimateMainWindow
from pdf_ultimate.ui.theme import STYLE_SHEET
from pdf_ultimate.core.process_pool import shutdown_process_pool

_SINGLE_INSTANCE_SERVER = "pdf-ultimate-single-instance-v2"
_ACTIVATE_TOKEN = "__ACTIVATE__"
_WINDOWS_APP_ID = "Local.HomePdf"


def _startup_pdf_paths(argv: list[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in argv[1:]:
        candidate = raw.strip().strip('"').strip("'")
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.suffix.lower() != ".pdf":
            continue
        if not path.exists():
            continue
        paths.append(path.resolve())
    return paths


def _set_windows_appusermodel_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_WINDOWS_APP_ID)
    except Exception:
        return


def _resolve_runtime_icon() -> QIcon:
    candidates: list[Path] = []
    here = Path(__file__).resolve()
    candidates.append(here.parent / "resources" / "app.ico")
    exe_path = Path(sys.executable).resolve()
    candidates.append(exe_path.parent / "_internal" / "pdf_ultimate" / "resources" / "app.ico")
    candidates.append(exe_path)

    for candidate in candidates:
        if not candidate.exists():
            continue
        icon = QIcon(str(candidate))
        if not icon.isNull():
            return icon
    return QIcon()


def _send_to_running_instance(paths: list[Path]) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(_SINGLE_INSTANCE_SERVER)
    if not socket.waitForConnected(220):
        return False

    if paths:
        payload = "\n".join(str(path) for path in paths)
    else:
        payload = _ACTIVATE_TOKEN
    socket.write(payload.encode("utf-8", errors="ignore"))
    socket.flush()
    socket.waitForBytesWritten(220)
    socket.disconnectFromServer()
    return True


class _InstanceBridge(QObject):
    documentsReceived = Signal(list)
    activateRequested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._enabled = False
        QLocalServer.removeServer(_SINGLE_INSTANCE_SERVER)
        if not self._server.listen(_SINGLE_INSTANCE_SERVER):
            QLocalServer.removeServer(_SINGLE_INSTANCE_SERVER)
            if not self._server.listen(_SINGLE_INSTANCE_SERVER):
                return
        self._enabled = True
        self._server.newConnection.connect(self._on_new_connection)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            socket.readyRead.connect(lambda s=socket: self._handle_socket_data(s))
            socket.disconnected.connect(socket.deleteLater)

    def _handle_socket_data(self, socket: QLocalSocket) -> None:
        payload = bytes(socket.readAll()).decode("utf-8", errors="ignore").strip()
        if not payload:
            self.activateRequested.emit()
            return
        if payload == _ACTIVATE_TOKEN:
            self.activateRequested.emit()
            return
        paths: list[Path] = []
        for line in payload.splitlines():
            candidate = line.strip().strip('"').strip("'")
            if not candidate:
                continue
            path = Path(candidate).expanduser()
            if path.suffix.lower() != ".pdf":
                continue
            if path.exists():
                paths.append(path.resolve())
        if paths:
            self.documentsReceived.emit(paths)
        else:
            self.activateRequested.emit()


def run() -> int:
    multiprocessing.freeze_support()
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError:
        pass
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    _set_windows_appusermodel_id()
    startup_paths = _startup_pdf_paths(sys.argv)
    if _send_to_running_instance(startup_paths):
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName("HOME PDF")
    app.setOrganizationName("Local")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE_SHEET)
    icon = _resolve_runtime_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    window = PdfUltimateMainWindow()
    if not icon.isNull():
        window.setWindowIcon(icon)
    bridge = _InstanceBridge(window)
    bridge.documentsReceived.connect(window.open_documents)
    bridge.activateRequested.connect(window.activateWindow)
    bridge.activateRequested.connect(window.raise_)
    window.show()
    if startup_paths:
        QTimer.singleShot(0, lambda: window.open_documents(startup_paths))
    else:
        QTimer.singleShot(0, window.restore_previous_session)
    app.aboutToQuit.connect(lambda: shutdown_process_pool(wait=False))
    return app.exec()
