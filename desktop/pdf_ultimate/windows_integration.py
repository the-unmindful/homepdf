"""Register the portable executable in Start and as a current-user PDF handler."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from .core.paths import app_root

_logger = logging.getLogger(__name__)
REGISTRATION_VERSION = 2


def ensure_start_menu_shortcut() -> bool | None:
    """Return registration success, or None for source/non-Windows runs."""
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return None
    executable = Path(sys.executable).resolve()
    marker = app_root() / 'start-menu-registration.json'
    registration_current = False
    try:
        saved = json.loads(marker.read_text(encoding='utf-8'))
        registration_current = (
            isinstance(saved, dict)
            and saved.get('executable') == str(executable)
            and saved.get('registration_version') == REGISTRATION_VERSION
        )
    except (OSError, ValueError):
        pass
    helper = executable.parent / 'scripts' / 'create_start_menu_shortcut.ps1'
    if not helper.is_file():
        _logger.warning('HomePDF Start menu helper is missing: %s', helper)
        return False
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    command = [str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive',
               '-ExecutionPolicy', 'Bypass', '-File', str(helper), '-ExePath', str(executable)]
    run_options = dict(
        check=True, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    if registration_current:
        try:
            # A marker records a successful run, not the continued correctness of
            # the .lnk target, icon, or PDF registry commands after another build.
            subprocess.run([*command, '-VerifyOnly'], **run_options)
            return True
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        # The helper verifies its saved shortcut and registration before exiting.
        subprocess.run(command, **run_options)
    except (OSError, subprocess.SubprocessError) as error:
        _logger.warning('HomePDF could not register its shortcut and PDF handler: %s', error)
        return False
    try:
        marker.write_text(json.dumps({
            'executable': str(executable), 'registration_version': REGISTRATION_VERSION,
        }), encoding='utf-8')
    except OSError:
        pass
    return True
