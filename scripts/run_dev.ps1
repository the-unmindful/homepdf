$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$pythonCandidates = @(
  'venv\Scripts\python.exe',
  '.venv\Scripts\python.exe'
)

$python = $pythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) {
  throw 'No local virtual environment found. Create one with: python -m venv venv'
}

$env:HOME_PDF_APP_ROOT = Join-Path $root 'runtime_data'
& $python desktop\main.py
