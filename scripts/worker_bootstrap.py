"""PyInstaller runtime hook: dispatch Windows workers before Qt runtime hooks.

The bundled Qt hook otherwise imports QtCore to create qt.conf, even when the
entrypoint itself is import-light. GUI launches still run the standard hooks.
"""
import multiprocessing
import sys

if "--multiprocessing-fork" in sys.argv:
    multiprocessing.freeze_support()

if "--tool-worker" in sys.argv:
    from pdf_ultimate.core.job_worker import run_worker
    raise SystemExit(run_worker())
