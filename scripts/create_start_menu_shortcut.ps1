<#
.SYNOPSIS
Registers an extracted HomePDF executable in Start and Open with for this user.
.DESCRIPTION
Keeps the executable in its portable folder. Repairs HomePDF-owned registrations;
does not edit Windows' protected PDF default choice or other applications.
Use -VerifyOnly for a read-only check and the effective Windows PDF command.
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\create_start_menu_shortcut.ps1 -ExePath 'C:\Apps\HomePDF\PDFUltimate.exe'
#>
param(
    [string]$ExePath,
    [switch]$DesktopShortcut,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'

function Get-RegistrationValue([string]$SubKey, [string]$Name = '') {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($SubKey)
    if ($null -eq $key) { return $null }
    try { return $key.GetValue($Name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) }
    finally { $key.Dispose() }
}

function Get-RegistrationValueNames([string]$SubKey) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($SubKey)
    if ($null -eq $key) { return @() }
    try { return $key.GetValueNames() }
    finally { $key.Dispose() }
}

function Set-RegistrationValue([string]$SubKey, [string]$Name, [string]$Value) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($SubKey)
    try { $key.SetValue($Name, $Value, [Microsoft.Win32.RegistryValueKind]::String) }
    finally { $key.Dispose() }
}

function Remove-RegistrationValue([string]$SubKey, [string]$Name) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($SubKey, $true)
    if ($null -eq $key) { return }
    try { $key.DeleteValue($Name, $false) }
    finally { $key.Dispose() }
}

function Remove-RegistrationKey([string]$SubKey) {
    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($SubKey, $false)
}

function Test-LegacyHomePdfOwner {
    $command = Get-RegistrationValue 'Software\Classes\pdf_auto_file\shell\open\command'
    # pdf_auto_file is a generic Windows name. Never clean it up if another
    # application now owns it, even when that application's path is missing.
    return $command -match '^\s*(?:"[^"\r\n]*\\(?:PDFUltimate|HomePDF)\.exe"|[^\s"]*\\(?:PDFUltimate|HomePDF)\.exe)(?:\s|$)'
}

function Remove-HomePdfRawOpenWith {
    $pdfKeys = @(
        'Software\Classes\.pdf',
        'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf'
    )
    foreach ($pdfKey in $pdfKeys) {
        Remove-RegistrationValue "$pdfKey\OpenWithProgids" 'Applications\PDFUltimate.exe'
        Remove-RegistrationKey "$pdfKey\OpenWithList\PDFUltimate.exe"
        $listKey = "$pdfKey\OpenWithList"
        $removedNames = @()
        foreach ($name in @(Get-RegistrationValueNames $listKey)) {
            if ($name -eq 'MRUList') { continue }
            $value = Get-RegistrationValue $listKey $name
            if ($value -ieq 'PDFUltimate.exe' -or $name -ieq 'PDFUltimate.exe') {
                Remove-RegistrationValue $listKey $name
                $removedNames += $name
            }
        }
        $mru = Get-RegistrationValue $listKey 'MRUList'
        if ($removedNames.Count -and $null -ne $mru) {
            $updatedMru = -join @($mru.ToCharArray() | Where-Object { $removedNames -notcontains [string]$_ })
            if ($updatedMru -cne $mru) { Set-RegistrationValue $listKey 'MRUList' $updatedMru }
        }
    }
}

function Register-HomePdfFileHandler([string]$Executable) {
    $progId = 'HomePdf.Document'
    $classes = 'Software\Classes'
    $command = '"{0}" "%1"' -f $Executable
    $icon = '"{0}",0' -f $Executable
    $progIdKey = "$classes\$progId"
    Set-RegistrationValue $progIdKey '' 'HomePDF PDF Document'
    Set-RegistrationValue "$progIdKey\DefaultIcon" '' $icon
    Set-RegistrationValue "$progIdKey\shell\open\command" '' $command
    Set-RegistrationValue "$progIdKey\Application" 'ApplicationName' 'HomePDF'
    Set-RegistrationValue "$progIdKey\Application" 'ApplicationDescription' 'HomePDF PDF reader and toolkit'
    Set-RegistrationValue "$progIdKey\Application" 'ApplicationIcon' $icon
    Remove-RegistrationValue $progIdKey 'NoOpenWith'
    Set-RegistrationValue "$classes\.pdf\OpenWithProgids" $progId ''

    # Keep executable-based invocations working without showing a second entry.
    $application = "$classes\Applications\PDFUltimate.exe"
    Set-RegistrationValue $application 'FriendlyAppName' 'HomePDF'
    Set-RegistrationValue $application 'NoOpenWith' ''
    Set-RegistrationValue "$application\DefaultIcon" '' $icon
    Set-RegistrationValue "$application\shell\open\command" '' $command
    Remove-RegistrationValue "$application\SupportedTypes" '.pdf'
    Remove-HomePdfRawOpenWith

    $capabilities = 'Software\HomePDF\Capabilities'
    Set-RegistrationValue $capabilities 'ApplicationName' 'HomePDF'
    Set-RegistrationValue $capabilities 'ApplicationDescription' 'HomePDF PDF reader and toolkit'
    Set-RegistrationValue $capabilities 'ApplicationIcon' $icon
    Set-RegistrationValue "$capabilities\FileAssociations" '.pdf' $progId
    Set-RegistrationValue 'Software\RegisteredApplications' 'HomePDF' $capabilities

    if (Test-LegacyHomePdfOwner) {
        # Retain the old class as a compatibility alias, but remove its PDF menu
        # bindings. Existing protected choices of that alias still open correctly.
        Set-RegistrationValue "$classes\pdf_auto_file\shell\open\command" '' $command
        Set-RegistrationValue "$classes\pdf_auto_file\DefaultIcon" '' $icon
        Set-RegistrationValue "$classes\pdf_auto_file" 'NoOpenWith' ''
        if ((Get-RegistrationValue "$classes\.pdf") -ieq 'pdf_auto_file') {
            Set-RegistrationValue "$classes\.pdf" '' $progId
        }
        Remove-RegistrationValue "$classes\.pdf\OpenWithProgids" 'pdf_auto_file'
        Remove-RegistrationValue 'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\OpenWithProgids' 'pdf_auto_file'
    }
}

function Get-HomePdfRegistryIssues([string]$Executable) {
    $command = '"{0}" "%1"' -f $Executable
    $icon = '"{0}",0' -f $Executable
    $expected = @(
        @('Software\Classes\HomePdf.Document\shell\open\command', '', $command),
        @('Software\Classes\HomePdf.Document\DefaultIcon', '', $icon),
        @('Software\Classes\HomePdf.Document\Application', 'ApplicationName', 'HomePDF'),
        @('Software\Classes\.pdf\OpenWithProgids', 'HomePdf.Document', ''),
        @('Software\Classes\Applications\PDFUltimate.exe\shell\open\command', '', $command),
        @('Software\Classes\Applications\PDFUltimate.exe\DefaultIcon', '', $icon),
        @('Software\Classes\Applications\PDFUltimate.exe', 'NoOpenWith', ''),
        @('Software\HomePDF\Capabilities', 'ApplicationName', 'HomePDF'),
        @('Software\HomePDF\Capabilities', 'ApplicationDescription', 'HomePDF PDF reader and toolkit'),
        @('Software\HomePDF\Capabilities', 'ApplicationIcon', $icon),
        @('Software\HomePDF\Capabilities\FileAssociations', '.pdf', 'HomePdf.Document'),
        @('Software\RegisteredApplications', 'HomePDF', 'Software\HomePDF\Capabilities')
    )
    if (Test-LegacyHomePdfOwner) {
        $expected += ,@('Software\Classes\pdf_auto_file\shell\open\command', '', $command)
        $expected += ,@('Software\Classes\pdf_auto_file\DefaultIcon', '', $icon)
        foreach ($key in @('Software\Classes\.pdf\OpenWithProgids', 'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\OpenWithProgids')) {
            if ('pdf_auto_file' -in @(Get-RegistrationValueNames $key)) { "Legacy HomePDF binding remains: $key" }
        }
        if ((Get-RegistrationValue 'Software\Classes\.pdf') -ieq 'pdf_auto_file') { 'Legacy HomePDF PDF class binding remains.' }
    }
    foreach ($entry in $expected) {
        $actual = Get-RegistrationValue $entry[0] $entry[1]
        if ($null -eq $actual -or $actual -ine $entry[2]) { "Registration differs: $($entry[0]) [$($entry[1])]" }
    }
    if ('NoOpenWith' -in @(Get-RegistrationValueNames 'Software\Classes\HomePdf.Document')) { 'Canonical HomePDF PDF handler is hidden.' }
    if ('.pdf' -in @(Get-RegistrationValueNames 'Software\Classes\Applications\PDFUltimate.exe\SupportedTypes')) { 'Duplicate executable PDF handler remains.' }
    foreach ($key in @('Software\Classes\.pdf', 'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf')) {
        if ('Applications\PDFUltimate.exe' -in @(Get-RegistrationValueNames "$key\OpenWithProgids")) { "Duplicate executable PDF binding remains: $key" }
        foreach ($name in @(Get-RegistrationValueNames "$key\OpenWithList")) {
            if ($name -ine 'MRUList' -and (Get-RegistrationValue "$key\OpenWithList" $name) -ieq 'PDFUltimate.exe') { "Duplicate executable Open with entry remains: $key" }
        }
    }
}

function Initialize-HomePdfShellApi {
    if ('HomePdf.PortableShell' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
namespace HomePdf {
    public static class PortableShell {
        [DllImport("shell32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        public static extern void SHChangeNotify(uint eventId, uint flags, IntPtr item1, IntPtr item2);
        [DllImport("shell32.dll", CharSet = CharSet.Unicode, EntryPoint = "SHChangeNotify", ExactSpelling = true)]
        public static extern void NotifyPath(uint eventId, uint flags, string item1, IntPtr item2);
        [DllImport("shlwapi.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        private static extern int AssocQueryStringW(uint flags, uint kind, string association, string extra, StringBuilder value, ref uint length);
        public static string PdfAssociation(uint kind) {
            uint length = 0;
            AssocQueryStringW(0, kind, ".pdf", null, null, ref length);
            if (length == 0) return null;
            var value = new StringBuilder((int)length);
            return AssocQueryStringW(0, kind, ".pdf", null, value, ref length) == 0 ? value.ToString() : null;
        }
    }
}
'@
}

function Get-HomePdfShortcutIssues($Shell, [string]$ShortcutPath, [string]$Executable) {
    if (-not (Test-Path -LiteralPath $ShortcutPath -PathType Leaf)) { "Shortcut is missing: $ShortcutPath"; return }
    $saved = $Shell.CreateShortcut($ShortcutPath)
    if ($saved.TargetPath -ine $Executable) { "Shortcut target differs: $ShortcutPath" }
    if ($saved.WorkingDirectory -ine (Split-Path -Parent $Executable)) { "Shortcut working folder differs: $ShortcutPath" }
    if ($saved.IconLocation -ine "$Executable,0") { "Shortcut icon differs: $ShortcutPath" }
    if ($saved.Arguments) { "Shortcut has stale arguments: $ShortcutPath" }
}

$root = Split-Path -Parent $PSScriptRoot
if (-not $ExePath) {
    $candidates = @((Join-Path $root 'PDFUltimate.exe'), (Join-Path $root 'release\PDFUltimate-portable\PDFUltimate.exe'))
    $ExePath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $ExePath -or -not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw 'HomePDF executable not found. Build a portable package or supply -ExePath.'
}
$exe = (Resolve-Path -LiteralPath $ExePath).Path
$appFolder = Split-Path -Parent $exe
$startMenu = [Environment]::GetFolderPath('Programs')
if (-not $startMenu) { throw 'The current-user Start menu folder could not be resolved.' }
$shortcutPaths = @((Join-Path $startMenu 'HomePDF.lnk'))
if ($DesktopShortcut) { $shortcutPaths += Join-Path ([Environment]::GetFolderPath('Desktop')) 'HomePDF.lnk' }
$shell = New-Object -ComObject WScript.Shell
Initialize-HomePdfShellApi

if (-not $VerifyOnly) {
    Register-HomePdfFileHandler $exe
    New-Item -ItemType Directory -Path $startMenu -Force | Out-Null
    foreach ($shortcutPath in $shortcutPaths) {
        # Updating an existing .lnk can preserve stale Shell identity and icon
        # metadata. Recreate it so a moved portable build starts with fresh data.
        if (Test-Path -LiteralPath $shortcutPath -PathType Leaf) {
            Remove-Item -LiteralPath $shortcutPath -Force
            [HomePdf.PortableShell]::NotifyPath(0x00000004, 0x0005, $shortcutPath, [IntPtr]::Zero)
        }
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $exe
        $shortcut.WorkingDirectory = $appFolder
        $shortcut.Arguments = ''
        $shortcut.IconLocation = "$exe,0"
        $shortcut.Description = 'HomePDF PDF reader and toolkit'
        $shortcut.Save()
        [HomePdf.PortableShell]::NotifyPath(0x00000002, 0x0005, $shortcutPath, [IntPtr]::Zero)
        Write-Host "Created: $shortcutPath"
    }
    # SHCNE_ASSOCCHANGED with SHCNF_IDLIST invalidates Shell association/icon caches.
    [HomePdf.PortableShell]::SHChangeNotify(0x08000000, 0x0000, [IntPtr]::Zero, [IntPtr]::Zero)
}

$issues = @(Get-HomePdfRegistryIssues $exe)
foreach ($shortcutPath in $shortcutPaths) { $issues += @(Get-HomePdfShortcutIssues $shell $shortcutPath $exe) }
if ($issues.Count) { throw ($issues -join [Environment]::NewLine) }
Write-Host "Verified HomePDF executable: $exe"
# Query the effective association through the supported Shell API. Registration
# remains valid when the user has selected a different default PDF application.
Write-Host "Effective PDF executable: $([HomePdf.PortableShell]::PdfAssociation(2))"
Write-Host "Effective PDF command: $([HomePdf.PortableShell]::PdfAssociation(1))"
if (-not $VerifyOnly) { Write-Host 'HomePDF is registered in Start and Open with. Keep its portable folder in this location.' }
