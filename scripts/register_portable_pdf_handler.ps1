param(
  [string]$ExePath
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
if (-not $ExePath) {
  $ExePath = Join-Path $root 'release\PDFUltimate-portable\PDFUltimate.exe'
}

$resolvedExe = (Resolve-Path -LiteralPath $ExePath).Path
if (-not (Test-Path -LiteralPath $resolvedExe -PathType Leaf)) {
  throw "Portable executable not found: $ExePath"
}

$progId = 'HomePdf.Document'
$classesRoot = 'HKCU:\Software\Classes'
$openWithProgidsKey = Join-Path $classesRoot '.pdf\OpenWithProgids'
$progIdKey = Join-Path $classesRoot $progId
$progIdIconKey = Join-Path $progIdKey 'DefaultIcon'
$progIdCommandKey = Join-Path $progIdKey 'shell\open\command'
$appKey = Join-Path $classesRoot 'Applications\PDFUltimate.exe'
$appCommandKey = Join-Path $appKey 'shell\open\command'
$appSupportedTypesKey = Join-Path $appKey 'SupportedTypes'
$commandValue = ('"{0}" "%1"' -f $resolvedExe)
$iconValue = ('{0},0' -f $resolvedExe)

New-Item -Path $openWithProgidsKey -Force | Out-Null
New-ItemProperty -Path $openWithProgidsKey -Name $progId -Value '' -PropertyType String -Force | Out-Null

New-Item -Path $progIdKey -Force -Value 'PDF Document' | Out-Null

New-Item -Path $progIdIconKey -Force -Value $iconValue | Out-Null

New-Item -Path $progIdCommandKey -Force -Value $commandValue | Out-Null

New-Item -Path $appCommandKey -Force -Value $commandValue | Out-Null

New-Item -Path $appSupportedTypesKey -Force | Out-Null
New-ItemProperty -Path $appSupportedTypesKey -Name '.pdf' -Value '' -PropertyType String -Force | Out-Null

try {
  $shell32 = Add-Type -MemberDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ShellRefresh {
  [DllImport("shell32.dll")]
  public static extern void SHChangeNotify(uint wEventId, uint uFlags, IntPtr dwItem1, IntPtr dwItem2);
}
'@ -Name ShellRefresh -Namespace HomePdf -PassThru
  $shell32::SHChangeNotify(0x08000000, 0x0000, [IntPtr]::Zero, [IntPtr]::Zero)
}
catch {
  # Registration still succeeds if shell refresh fails.
}

Write-Host "Registered HOME PDF portable handler for .pdf in HKCU."
Write-Host "Executable: $resolvedExe"
Write-Host "Next step: open Windows Settings > Apps > Default apps and choose HOME PDF or PDFUltimate.exe for .pdf if Windows has not switched it automatically."
