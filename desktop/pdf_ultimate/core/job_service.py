from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
from PySide6.QtCore import QObject, QProcess, Signal
from .job_worker import encode
from .output_safety import publish_file
from .paths import temp_root


class JobService(QObject):
    """An owned, cancellable process. Publication happens only after success."""
    finished = Signal(object)
    progress = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None, workspace_root=None):
        super().__init__(parent)
        self._root = Path(workspace_root) if workspace_root else temp_root()
        self._process = None
        self._workspace = None
        self._buffer = b""
        self._result = None
        self._error = ""
        self._cancel = False
        self._stderr = b""

    @property
    def busy(self):
        return self._process is not None

    def start(self, operation, args, kwargs=None):
        if self.busy:
            raise RuntimeError("An operation is already running. Cancel it or wait for it to finish.")
        self._root.mkdir(parents=True, exist_ok=True)
        self._workspace = Path(tempfile.mkdtemp(prefix="job-", dir=self._root))
        self._buffer = b""; self._stderr = b""; self._result = None; self._error = ""; self._cancel = False
        request = json.dumps({"operation": operation, "args": encode(args), "kwargs": encode(kwargs or {}), "workspace": str(self._workspace)}, ensure_ascii=True).encode("utf-8")
        process = QProcess(self)
        self._process = process
        process.readyReadStandardOutput.connect(self._read)
        process.readyReadStandardError.connect(self._read_error)
        process.finished.connect(self._finish)
        process.errorOccurred.connect(self._process_error)
        def write_request():
            if self._cancel:
                process.kill()
                return
            process.write(request)
            process.closeWriteChannel()
        process.started.connect(write_request)
        args = ["--tool-worker"] if getattr(sys, "frozen", False) else [str(Path(__file__).resolve().parents[2] / "main.py"), "--tool-worker"]
        process.start(sys.executable, args)

    def _read(self):
        if self._process is None:
            return
        self._buffer += bytes(self._process.readAllStandardOutput())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                data = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if data.get("type") == "result": self._result = data
            elif data.get("type") == "error": self._error = data.get("message", "Operation failed")
            elif data.get("type") == "progress" and not self._cancel: self.progress.emit(data)

    def _read_error(self):
        self._stderr = (self._stderr + bytes(self._process.readAllStandardError()))[-8192:]

    def _process_error(self, error):
        if error == QProcess.FailedToStart:
            self._error = self._process.errorString()
            self._finish(-1, QProcess.CrashExit)

    def cancel(self):
        if self._process is not None:
            self._cancel = True
            self._process.kill()

    def _finish(self, code, status):
        if self._process is None:
            return
        self._read()
        process, workspace = self._process, self._workspace
        result, cancelled = self._result, self._cancel
        error = self._error or self._stderr.decode("utf-8", errors="replace").strip() or "The operation process stopped unexpectedly."
        published = []
        try:
            if not cancelled and code == 0 and result is not None:
                for item in result.get("files", []):
                    staged = Path(item["staged"]).resolve()
                    if not staged.is_relative_to(workspace.resolve()):
                        raise ValueError("Invalid staged output")
                    published.append(publish_file(staged, Path(item["destination"])))
                result["outputs"] = published
            elif not cancelled:
                result = None
        except Exception as exc:
            for path in published:
                path.unlink(missing_ok=True)
            result = None
            error = str(exc)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
            self._workspace = None
            self._process = None
            process.deleteLater()
        # Finalize state before signals: receivers may immediately start another job.
        if cancelled:
            self.cancelled.emit()
        elif result is not None:
            self.finished.emit(result)
        else:
            self.failed.emit(error)
