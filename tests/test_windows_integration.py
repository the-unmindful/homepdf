import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from pdf_ultimate import windows_integration


POWERSHELL = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
CLASSES = r'Software\Classes'
PDF_HISTORY = r'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf'

# Load only the helper's function definitions. Replace registry primitives before
# invoking the registration logic; this harness never touches the real registry.
REGISTRY_HARNESS = r'''
param([string]$Helper, [string]$Fixture, [string]$Executable, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Helper, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false) | ForEach-Object {
    . ([scriptblock]::Create($_.Extent.Text))
}
$script:registry = @{}
$data = Get-Content -LiteralPath $Fixture -Raw | ConvertFrom-Json
foreach ($entry in $data.PSObject.Properties) {
    $values = @{}
    foreach ($value in $entry.Value) { $values[$value.name] = $value.value }
    $script:registry[$entry.Name] = $values
}
function Get-RegistrationValue([string]$SubKey, [string]$Name = '') {
    if ($script:registry.ContainsKey($SubKey)) { return $script:registry[$SubKey][$Name] }
    return $null
}
function Get-RegistrationValueNames([string]$SubKey) {
    if ($script:registry.ContainsKey($SubKey)) { return @($script:registry[$SubKey].Keys) }
    return @()
}
function Set-RegistrationValue([string]$SubKey, [string]$Name, [string]$Value) {
    if (-not $script:registry.ContainsKey($SubKey)) { $script:registry[$SubKey] = @{} }
    $script:registry[$SubKey][$Name] = $Value
}
function Remove-RegistrationValue([string]$SubKey, [string]$Name) {
    if ($script:registry.ContainsKey($SubKey)) { $script:registry[$SubKey].Remove($Name) }
}
function Remove-RegistrationKey([string]$SubKey) { $script:registry.Remove($SubKey) }
if (-not $CheckOnly -and (Get-Command Register-HomePdfFileHandler -ErrorAction SilentlyContinue)) {
    Register-HomePdfFileHandler $Executable
}
$issues = @('Registration verification unavailable')
if (Get-Command Get-HomePdfRegistryIssues -ErrorAction SilentlyContinue) {
    $issues = @(Get-HomePdfRegistryIssues $Executable)
}
@{registry = $script:registry; issues = $issues} | ConvertTo-Json -Depth 8 -Compress
'''


class StartMenuRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.exe = self.folder / 'PDFUltimate.exe'
        self.exe.touch()
        self.root_patch = patch("pdf_ultimate.windows_integration.app_root", return_value=self.folder)
        self.root_patch.start(); self.addCleanup(self.root_patch.stop)
        self.environment_patch = patch.dict('os.environ', {'APPDATA': str(self.folder)})
        self.environment_patch.start(); self.addCleanup(self.environment_patch.stop)
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
            self.assertEqual(run.call_count, 2)
            self.assertNotIn('-VerifyOnly', run.call_args_list[0].args[0])
            self.assertIn('-VerifyOnly', run.call_args_list[1].args[0])

    def test_unversioned_marker_upgrades_existing_shortcut_registration(self):
        marker = self.folder / 'start-menu-registration.json'
        marker.write_text(json.dumps({'executable': str(self.exe)}), encoding='utf-8')
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'win32'), patch.object(sys, 'executable', str(self.exe)), patch.dict('os.environ', {'APPDATA': str(self.folder)}), patch('subprocess.run') as run:
            shortcut = self.folder / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'HomePDF.lnk'
            shortcut.parent.mkdir(parents=True); shortcut.touch()
            self.assertTrue(windows_integration.ensure_start_menu_shortcut())
            self.assertEqual(run.call_count, 1)
            self.assertNotIn('-VerifyOnly', run.call_args.args[0])
            self.assertEqual(json.loads(marker.read_text(encoding='utf-8')), {
                'executable': str(self.exe), 'registration_version': 2,
            })

    def test_current_marker_still_checks_shortcut_and_pdf_registration(self):
        marker = self.folder / 'start-menu-registration.json'
        marker.write_text(json.dumps({'executable': str(self.exe), 'registration_version': 2}), encoding='utf-8')
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'win32'), patch.object(sys, 'executable', str(self.exe)), patch('subprocess.run') as run:
            self.assertTrue(windows_integration.ensure_start_menu_shortcut())
            self.assertEqual(run.call_count, 1)
            self.assertIn('-VerifyOnly', run.call_args.args[0])

    def test_stale_verified_registration_is_repaired_with_actual_executable(self):
        marker = self.folder / 'start-menu-registration.json'
        marker.write_text(json.dumps({'executable': str(self.exe), 'registration_version': 2}), encoding='utf-8')
        verify_failure = subprocess.CalledProcessError(1, ['powershell'], stderr='PDF handler points to a missing executable')
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'win32'), patch.object(sys, 'executable', str(self.exe)), patch('subprocess.run', side_effect=[verify_failure, subprocess.CompletedProcess([], 0)]) as run:
            self.assertTrue(windows_integration.ensure_start_menu_shortcut())
            self.assertEqual(run.call_count, 2)
            self.assertIn('-VerifyOnly', run.call_args_list[0].args[0])
            repair = run.call_args_list[1].args[0]
            self.assertNotIn('-VerifyOnly', repair)
            self.assertEqual(repair[repair.index('-ExePath') + 1], str(self.exe))

    def test_moved_portable_executable_replaces_old_marker(self):
        marker = self.folder / 'start-menu-registration.json'
        marker.write_text(json.dumps({'executable': str(self.folder / 'old' / 'PDFUltimate.exe'), 'registration_version': 2}), encoding='utf-8')
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'win32'), patch.object(sys, 'executable', str(self.exe)), patch('subprocess.run') as run:
            self.assertTrue(windows_integration.ensure_start_menu_shortcut())
            self.assertNotIn('-VerifyOnly', run.call_args.args[0])
            self.assertEqual(json.loads(marker.read_text(encoding='utf-8'))['executable'], str(self.exe))

    def test_failed_repair_does_not_record_a_new_registration_version(self):
        marker = self.folder / 'start-menu-registration.json'
        original = json.dumps({'executable': str(self.exe)})
        marker.write_text(original, encoding='utf-8')
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'win32'), patch.object(sys, 'executable', str(self.exe)), patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, ['powershell'], stderr='Access denied')):
            with self.assertLogs('pdf_ultimate.windows_integration', level='WARNING'):
                self.assertFalse(windows_integration.ensure_start_menu_shortcut())
        self.assertEqual(marker.read_text(encoding='utf-8'), original)

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


@unittest.skipUnless(POWERSHELL.is_file(), 'Windows PowerShell is required for the mocked registry harness')
class PdfHandlerRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.harness = self.folder / 'registry-harness.ps1'
        self.harness.write_text(REGISTRY_HARNESS, encoding='utf-8')
        self.helper = Path(__file__).resolve().parents[1] / 'scripts' / 'create_start_menu_shortcut.ps1'
        self.exe = r'E:\Extracted HomePDF\PDFUltimate.exe'
        self.command = '"E:\\Extracted HomePDF\\PDFUltimate.exe" "%1"'

    def invoke(self, registry, *, check_only=False):
        fixture = self.folder / 'registry.json'
        fixture.write_text(json.dumps({key: [{'name': name, 'value': value} for name, value in values.items()] for key, values in registry.items()}), encoding='utf-8')
        arguments = [str(POWERSHELL), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(self.harness), '-Helper', str(self.helper), '-Fixture', str(fixture), '-Executable', self.exe]
        if check_only:
            arguments.append('-CheckOnly')
        result = subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=20)
        return json.loads(result.stdout)

    def test_registers_one_canonical_pdf_identity_and_retargets_legacy_homepdf(self):
        initial = {
            CLASSES + r'\.pdf': {'': 'pdf_auto_file', 'Content Type': 'application/pdf'},
            CLASSES + r'\.pdf\OpenWithProgids': {'pdf_auto_file': '', 'Other.Pdf': ''},
            PDF_HISTORY + r'\OpenWithProgids': {'pdf_auto_file': '', 'Other.Pdf': ''},
            PDF_HISTORY + r'\OpenWithList': {'a': 'PDFUltimate.exe', 'b': 'Acrobat.exe', 'c': 'notepad.exe', 'MRUList': 'bac'},
            CLASSES + r'\.pdf\OpenWithList\PDFUltimate.exe': {},
            CLASSES + r'\.pdf\OpenWithList\Other.exe': {},
            CLASSES + r'\pdf_auto_file\shell\open\command': {'': r'"E:\Old HomePDF\PDFUltimate.exe" "%1"'},
            CLASSES + r'\Applications\PDFUltimate.exe\SupportedTypes': {'.pdf': '', '.txt': ''},
            PDF_HISTORY + r'\UserChoice': {'ProgId': 'Other.Pdf', 'Hash': 'old-protected-hash'},
            PDF_HISTORY + r'\UserChoiceLatest': {'ProgId': 'HomePdf.Document', 'Hash': 'current-protected-hash'},
        }
        result = self.invoke(initial)
        registry = result['registry']
        self.assertEqual(registry[CLASSES + r'\HomePdf.Document\shell\open\command'][''], self.command)
        self.assertEqual(registry[CLASSES + r'\HomePdf.Document\DefaultIcon'][''], '"E:\\Extracted HomePDF\\PDFUltimate.exe",0')
        self.assertIn('HomePdf.Document', registry[CLASSES + r'\.pdf\OpenWithProgids'])
        self.assertEqual(registry[CLASSES + r'\.pdf'][''], 'HomePdf.Document')
        self.assertEqual(registry[CLASSES + r'\pdf_auto_file\shell\open\command'][''], self.command)
        self.assertEqual(registry[CLASSES + r'\Applications\PDFUltimate.exe\shell\open\command'][''], self.command)
        self.assertEqual(registry[CLASSES + r'\Applications\PDFUltimate.exe']['NoOpenWith'], '')
        self.assertEqual(registry[CLASSES + r'\Applications\PDFUltimate.exe\SupportedTypes'], {'.txt': ''})
        self.assertEqual(registry[PDF_HISTORY + r'\OpenWithList'], {'b': 'Acrobat.exe', 'c': 'notepad.exe', 'MRUList': 'bc'})
        self.assertNotIn(CLASSES + r'\.pdf\OpenWithList\PDFUltimate.exe', registry)
        self.assertIn(CLASSES + r'\.pdf\OpenWithList\Other.exe', registry)
        self.assertEqual(registry[CLASSES + r'\.pdf\OpenWithProgids']['Other.Pdf'], '')
        self.assertEqual(registry[PDF_HISTORY + r'\OpenWithProgids'], {'Other.Pdf': ''})
        for choice in ('UserChoice', 'UserChoiceLatest'):
            self.assertEqual(registry[PDF_HISTORY + '\\' + choice], initial[PDF_HISTORY + '\\' + choice])
        self.assertEqual(registry[r'Software\RegisteredApplications']['HomePDF'], r'Software\HomePDF\Capabilities')
        self.assertEqual(registry[r'Software\HomePDF\Capabilities\FileAssociations']['.pdf'], 'HomePdf.Document')
        self.assertEqual(result['issues'], [])

    def test_preserves_generic_pdf_class_owned_by_another_application(self):
        initial = {
            CLASSES + r'\.pdf': {'': 'pdf_auto_file'},
            CLASSES + r'\.pdf\OpenWithProgids': {'pdf_auto_file': ''},
            PDF_HISTORY + r'\OpenWithProgids': {'pdf_auto_file': ''},
            CLASSES + r'\pdf_auto_file\shell\open\command': {'': r'"C:\Other Reader\reader.exe" "%1"'},
        }
        result = self.invoke(initial)
        for key in initial:
            for name, value in initial[key].items():
                self.assertEqual(result['registry'][key][name], value)
        self.assertEqual(result['issues'], [])

    def test_read_only_check_detects_stale_pdf_command_without_repairing_it(self):
        registered = self.invoke({})['registry']
        stale = CLASSES + r'\HomePdf.Document\shell\open\command'
        registered[stale][''] = r'"C:\Missing\PDFUltimate.exe" "%1"'
        checked = self.invoke(registered, check_only=True)
        self.assertEqual(checked['registry'], registered)
        self.assertTrue(any('command' in issue.lower() for issue in checked['issues']))



if __name__ == '__main__':
    unittest.main()
