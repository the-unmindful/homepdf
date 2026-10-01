<#
.SYNOPSIS
Creates a HomePDF Start menu shortcut for the current Windows user.
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\create_start_menu_shortcut.ps1
#>
param(
    [string]$ExePath,
    [switch]$DesktopShortcut
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $ExePath) {
    $candidates = @(
        (Join-Path $root 'PDFUltimate.exe'),
        (Join-Path $root 'release\PDFUltimate-portable\PDFUltimate.exe')
    )
    $ExePath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $ExePath -or -not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw 'HomePDF executable not found. Build a portable package or supply -ExePath.'
}
$exe = (Resolve-Path -LiteralPath $ExePath).Path
$appFolder = Split-Path -Parent $exe
$startMenu = [Environment]::GetFolderPath('Programs')
if (-not $startMenu) { throw 'The current-user Start menu folder could not be resolved.' }
$shell = New-Object -ComObject WScript.Shell
$shortcutPaths = @((Join-Path $startMenu 'HomePDF.lnk'))
if ($DesktopShortcut) {
    $shortcutPaths += Join-Path ([Environment]::GetFolderPath('Desktop')) 'HomePDF.lnk'
}
foreach ($shortcutPath in $shortcutPaths) {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $exe
    $shortcut.WorkingDirectory = $appFolder
    $shortcut.IconLocation = "$exe,0"
    $shortcut.Description = 'HomePDF PDF reader and toolkit'
    $shortcut.Save()
    Write-Host "Created: $shortcutPath"
}
Write-Host 'HomePDF is now available in Start. Keep its portable folder in this location.'
