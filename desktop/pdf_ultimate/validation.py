"""Explicit portable smoke test; never runs during ordinary app startup."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time


def run_smoke(source: Path, destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    os.environ["HOME_PDF_APP_ROOT"] = str(destination / "state")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication
    from .core.process_pool import shutdown_process_pool
    from .ui.main_window import PdfUltimateMainWindow
    from .ui.theme import STYLE_SHEET

    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE_SHEET)
    for name in ("segoeui.ttf", "segoeuib.ttf"):
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    window = PdfUltimateMainWindow()
    window.resize(1366, 768)
    errors = []
    window._show_error = lambda error: errors.append(str(error))
    window.show()
    if not window._open_pdf(source, record_doc_history=False):
        raise RuntimeError(str(errors))
    report = {"frozen": bool(getattr(sys, "frozen", False)), "errors": errors, "scale": window.devicePixelRatioF()}
    start = time.monotonic()
    state = {"phase": "render", "ticks": 0, "renders": 0}
    window.renderer.rendered.connect(lambda *_: state.update(renders=state["renders"] + 1))
    window.renderer.failed.connect(lambda key, error: errors.append(error))
    window.search_jobs.failed.connect(errors.append)
    window.tool_jobs.failed.connect(errors.append)
    window.search_jobs.finished.connect(lambda result: report.update(search_hits=len(result["value"]["sequence"])))
    window.tool_jobs.finished.connect(lambda result: report.update(export_files=len(result["outputs"])))
    window.tool_jobs.cancelled.connect(lambda: report.update(cancelled=True))
    def cancel_after_progress(_):
        if state["phase"] == "cancel" and _.get("done", 0) >= 2:
            window.tool_jobs.cancel()
    window.tool_jobs.progress.connect(cancel_after_progress)
    timer = QTimer()
    def step():
        state["ticks"] += 1
        try:
            if time.monotonic() - start > 45:
                raise TimeoutError(f"Smoke test timed out in {state['phase']}")
            if errors:
                raise RuntimeError(str(errors))
            phase = state["phase"]
            if phase == "render" and window.preview_cache:
                report["first_render_ms"] = round((time.monotonic() - start) * 1000, 1)
                window.grab().save(str(destination / "reader.png"))
                report["viewport_width"] = window.page_scroll.viewport().width()
                report["raw_renders"] = state["renders"]
                window._toggle_right_panel()
                window.tools_combo.setCurrentIndex(2)  # OCR
                window.grab().save(str(destination / "ocr.png"))
                window._toggle_right_panel()
                for _ in range(8): window._change_zoom(1.04)
                report["active_after_zoom"] = len(window.renderer._active)
                report["pending_after_zoom"] = window.renderer.pending_count
                assert report["active_after_zoom"] <= 2 and report["pending_after_zoom"] <= 24
                state["phase"] = "search"
                window.search_input.blockSignals(True)
                window.search_input.setText("needle")
                window.search_input.blockSignals(False)
                window._start_async_search("needle")
            elif phase == "search" and not window.search_jobs.busy:
                assert report.get("search_hits", 0) > 0
                state["phase"] = "export"
                state["export_start"] = time.monotonic()
                state["export_ticks"] = state["ticks"]
                window._submit_tool("convert", source, "png", destination / "export", message="Export complete.")
            elif phase == "export" and not window.tool_jobs.busy:
                assert report.get("export_files", 0) > 0
                report["export_ms"] = round((time.monotonic() - state["export_start"]) * 1000, 1)
                report["export_heartbeats"] = state["ticks"] - state["export_ticks"]
                state["phase"] = "cancel"
                window._submit_tool("convert", source, "png", destination / "cancelled-export")
            elif phase == "cancel" and not window.tool_jobs.busy:
                assert report.get("cancelled")
                assert not (destination / "cancelled-export").exists()
                assert not list((destination / "state" / "temp").glob("job-*"))
                report["passed"] = True
                finish(0)
        except Exception as exc:
            report["failure"] = str(exc)
            report["passed"] = False
            finish(1)
    def finish(code):
        timer.stop()
        report["elapsed_ms"] = round((time.monotonic() - start) * 1000, 1)
        (destination / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        window.close()
        shutdown_process_pool(wait=True)
        app.exit(code)
    timer.timeout.connect(step)
    timer.start(20)
    return app.exec()
