param()

$ErrorActionPreference = 'Stop'

$classesRoot = 'HKCU:\Software\Classes'
$progId = 'HomePdf.Document'
$openWithProgidsKey = Join-Path $classesRoot '.pdf\OpenWithProgids'
$progIdKey = Join-Path $classesRoot $progId
$appKey = Join-Path $classesRoot 'Applications\PDFUltimate.exe'

if (Test-Path -LiteralPath $openWithProgidsKey) {
  try {
    Remove-ItemProperty -Path $openWithProgidsKey -Name $progId -ErrorAction Stop
  }
  catch {
    # Ignore if the value is already absent.
  }
}

if (Test-Path -LiteralPath $progIdKey) {
  Remove-Item -LiteralPath $progIdKey -Recurse -Force
}

if (Test-Path -LiteralPath $appKey) {
  Remove-Item -LiteralPath $appKey -Recurse -Force
}

try {
  $shell32 = Add-Type -MemberDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ShellRefresh {
  [DllImport("shell32.dll")]
  public static extern void SHChangeNotify(uint wEventId, uint uFlags, IntPtr dwItem1, IntPtr dwItem2);
}
'@ -Name ShellRefresh -Namespace HomePdfUndo -PassThru
  $shell32::SHChangeNotify(0x08000000, 0x0000, [IntPtr]::Zero, [IntPtr]::Zero)
}
catch {
  # Unregistration still succeeds if shell refresh fails.
}

Write-Host 'Removed HOME PDF portable handler registration from HKCU.'
