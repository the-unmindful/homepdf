from __future__ import annotations

import os
from pathlib import Path
import sys

_DEV_APP_ROOT_ENV = "HOME_PDF_APP_ROOT"

if not getattr(sys, "frozen", False) and _DEV_APP_ROOT_ENV not in os.environ:
    os.environ[_DEV_APP_ROOT_ENV] = str(Path(__file__).resolve().parent.parent / "runtime_data")

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    if "--tool-worker" in sys.argv:
        from pdf_ultimate.core.job_worker import run_worker
        raise SystemExit(run_worker())
    from pdf_ultimate.app import run
    raise SystemExit(run())
