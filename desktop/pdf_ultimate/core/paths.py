from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path


APP_NAME = "HOME PDF"
ORG_NAME = "Local"
APP_ROOT_ENV_VAR = "HOME_PDF_APP_ROOT"

# If any of these marker files exist next to the executable, the app runs in "portable mode"
# and stores settings/outputs/temp next to the exe (inside a dedicated folder).
PORTABLE_MARKERS = ("portable.flag", ".portable", "PORTABLE_MODE")


@lru_cache(maxsize=1)
def _exe_dir() -> Path | None:
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
    except Exception:
        return None
    return None


@lru_cache(maxsize=1)
def app_root() -> Path:
    """
    Root folder for settings / outputs / temp.

    Override: HOME_PDF_APP_ROOT
    Default:  ~/Documents/HOME PDF
    Portable: <exe-folder>/HOME PDF_DATA  (enabled by creating a marker file next to the exe)
    """
    override = os.environ.get(APP_ROOT_ENV_VAR, "").strip()
    if override:
        root = Path(override).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    exe_dir = _exe_dir()
    if exe_dir is not None:
        for marker in PORTABLE_MARKERS:
            if (exe_dir / marker).exists():
                root = exe_dir / f"{APP_NAME}_DATA"
                root.mkdir(parents=True, exist_ok=True)
                return root

    # Default (non-portable) location
    root = Path.home() / "Documents" / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def output_root() -> Path:
    path = app_root() / "outputs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def temp_root() -> Path:
    path = app_root() / "temp"
    path.mkdir(parents=True, exist_ok=True)
    return path
