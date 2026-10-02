import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from pdf_ultimate import windows_integration


class StartMenuRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.exe = self.folder / 'PDFUltimate.exe'
        self.exe.touch()
        self.root_patch = patch("pdf_ultimate.windows_integration.app_root", return_value=self.folder)
        self.root_patch.start(); self.addCleanup(self.root_patch.stop)
        (self.folder / 'scripts').mkdir()
        (self.folder / 'scripts' / 'create_start_menu_shortcut.ps1').touch()

    def test_source_runs_do_not_register_python_as_homepdf(self):
        with patch.object(sys, 'frozen', False, create=True), patch('subprocess.run') as run:
            self.assertIsNone(windows_integration.ensure_start_menu_shortcut())
            run.assert_not_called()

    def test_portable_run_registers_the_actual_executable(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'win32'), patch.object(sys, 'executable', str(self.exe)), patch('subprocess.run') as run:
            self.assertTrue(windows_integration.ensure_start_menu_shortcut())
            command = run.call_args.args[0]
            self.assertEqual(command[command.index('-ExePath') + 1], str(self.exe.resolve()))
            self.assertEqual(command[command.index('-File') + 1], str(self.folder / 'scripts' / 'create_start_menu_shortcut.ps1'))

    def test_successful_registration_is_not_repeated(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'win32'), patch.object(sys, 'executable', str(self.exe)), patch('pdf_ultimate.windows_integration.app_root', return_value=self.folder), patch.dict('os.environ', {'APPDATA': str(self.folder)}), patch('subprocess.run') as run:
            shortcut = self.folder / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'HomePDF.lnk'
            shortcut.parent.mkdir(parents=True); shortcut.touch()
            self.assertTrue(windows_integration.ensure_start_menu_shortcut())
            self.assertTrue(windows_integration.ensure_start_menu_shortcut())
            self.assertEqual(run.call_count, 1)

    def test_failed_registration_does_not_prevent_reading_pdfs(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'win32'), patch.object(sys, 'executable', str(self.exe)), patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, ['powershell'], stderr='Access denied')):
            with self.assertLogs('pdf_ultimate.windows_integration', level='WARNING'):
                self.assertFalse(windows_integration.ensure_start_menu_shortcut())

    def test_missing_helper_reports_failure_without_launching_powershell(self):
        (self.folder / 'scripts' / 'create_start_menu_shortcut.ps1').unlink()
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'win32'), patch.object(sys, 'executable', str(self.exe)), patch('subprocess.run') as run:
            with self.assertLogs('pdf_ultimate.windows_integration', level='WARNING'):
                self.assertFalse(windows_integration.ensure_start_menu_shortcut())
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
