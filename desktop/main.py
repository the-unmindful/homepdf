from __future__ import annotations

import os
from pathlib import Path
import sys

_DEV_APP_ROOT_ENV = "HOME_PDF_APP_ROOT"

if not getattr(sys, "frozen", False) and _DEV_APP_ROOT_ENV not in os.environ:
    os.environ[_DEV_APP_ROOT_ENV] = str(Path(__file__).resolve().parent.parent / "runtime_data")

from pdf_ultimate.app import run


if __name__ == "__main__":
    raise SystemExit(run())
