"""Register the portable Windows executable in the current user's Start menu."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from .core.paths import app_root

_logger = logging.getLogger(__name__)


def ensure_start_menu_shortcut() -> bool | None:
    """Return registration success, or None for source/non-Windows runs."""
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return None
    executable = Path(sys.executable).resolve()
    marker = app_root() / 'start-menu-registration.json'
    shortcut = Path(os.environ.get('APPDATA', '')) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'HomePDF.lnk'
    try:
        if shortcut.is_file() and json.loads(marker.read_text(encoding='utf-8')).get('executable') == str(executable):
            return True
    except (OSError, ValueError):
        pass
    helper = executable.parent / 'scripts' / 'create_start_menu_shortcut.ps1'
    if not helper.is_file():
        _logger.warning('HomePDF Start menu helper is missing: %s', helper)
        return False
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    try:
        subprocess.run(
            [str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive',
             '-ExecutionPolicy', 'Bypass', '-File', str(helper), '-ExePath', str(executable)],
            check=True, capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except (OSError, subprocess.SubprocessError) as error:
        _logger.warning('HomePDF could not add its Start menu shortcut: %s', error)
        return False
    try:
        marker.write_text(json.dumps({'executable': str(executable)}), encoding='utf-8')
    except OSError:
        pass
    return True
