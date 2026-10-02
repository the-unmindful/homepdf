"""Qt-free, one-job process protocol. Passwords arrive through stdin only."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import inspect
import json
from pathlib import Path
import sys

OPERATIONS = {"merge", "extract_pages", "delete_pages", "reorder_pages", "rotate_pages",
              "split_by_ranges", "split_every", "convert", "convert_to_pdf", "protect",
              "unlock", "watermark_text", "compress", "annotate_text_matches",
              "redact_text_matches", "stamp_image", "extract_reflow_text"}


def encode(value):
    if isinstance(value, Path):
        return {"__path__": str(value)}
    if is_dataclass(value):
        return {"__options__": asdict(value)}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    return value


def decode(value):
    if isinstance(value, dict):
        if "__path__" in value:
            return Path(value["__path__"])
        if "__options__" in value:
            from .pdf_tools import ProtectOptions
            return ProtectOptions(**value["__options__"])
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value


def emit(kind, **data):
    print(json.dumps({"type": kind, **data}, ensure_ascii=True), flush=True)


def run_worker() -> int:
    # PyInstaller's windowed executable has no sys.stdout until we attach the
    # QProcess pipes using the Windows standard handles.
    if sys.stdin is None or sys.stdout is None:
        import os
        import msvcrt
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.GetStdHandle.restype = ctypes.c_void_p
        if sys.stdin is None:
            fd = msvcrt.open_osfhandle(kernel.GetStdHandle(-10), os.O_RDONLY)
            sys.stdin = os.fdopen(fd, "r", encoding="utf-8")
        if sys.stdout is None:
            fd = msvcrt.open_osfhandle(kernel.GetStdHandle(-11), os.O_WRONLY)
            sys.stdout = os.fdopen(fd, "w", encoding="utf-8", buffering=1)
        if sys.stderr is None:
            sys.stderr = sys.stdout
    try:
        request = json.loads(sys.stdin.read())
        operation = request["operation"]
        args = decode(request.get("args", []))
        kwargs = decode(request.get("kwargs", {}))
        emit("progress", message="Working…")
        files = []
        if operation == "search":
            from .worker_tasks import search_pdf_text
            value = asdict(search_pdf_text(str(args[0]), args[1], args[2]))
        elif operation == "reader_text":
            import fitz
            parts = []
            size = 0
            with fitz.open(args[0]) as doc:
                for display, actual in enumerate(args[1], 1):
                    text = doc[actual].get_text().strip() or "[No text detected on this page]"
                    part = f"Page {display}\n{text}"
                    parts.append(part)
                    size += len(part)
                    if size > 250_000:
                        parts.append("[Preview limited to 250,000 characters. Use text export for the complete document.]")
                        break
            value = "\n\n".join(parts)
        else:
            if operation not in OPERATIONS:
                raise ValueError("Unknown operation")
            from .pdf_tools import PdfToolkit
            toolkit = PdfToolkit(Path(request["workspace"]))
            toolkit.progress_callback = lambda done, total: emit("progress", message=f"Page {done} of {total}", done=done, total=total)
            method = getattr(toolkit, operation)
            bound = inspect.signature(method).bind(*args, **kwargs)
            parameter = next((name for name in ("output_path", "output_dir") if name in bound.arguments), None)
            destination = Path(bound.arguments[parameter]).resolve() if parameter else None
            stage = Path(request["workspace"]).resolve() / "outputs"
            stage.mkdir()
            if parameter:
                bound.arguments[parameter] = stage / destination.name if parameter == "output_path" else stage
            result = method(*bound.args, **bound.kwargs)
            outputs = result.outputs if hasattr(result, "outputs") else result if isinstance(result, list) else [result] if isinstance(result, Path) else []
            for path in outputs:
                target = destination if parameter == "output_path" else destination / path.relative_to(stage)
                files.append({"staged": str(path), "destination": str(target)})
            value = result if isinstance(result, str) else None
        emit("result", files=files, value=value, qt_imported=any(name.startswith("PySide6") for name in sys.modules))
        return 0
    except Exception as exc:
        emit("error", message=str(exc))
        return 1
