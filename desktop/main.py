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
    if len(sys.argv) == 4 and sys.argv[1] == "--smoke-test":
        from pdf_ultimate.validation import run_smoke
        raise SystemExit(run_smoke(Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()))
    from pdf_ultimate.app import run
    raise SystemExit(run())
