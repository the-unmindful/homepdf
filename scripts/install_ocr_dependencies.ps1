$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$pythonCandidates = @(
  'venv\Scripts\python.exe',
  '.venv\Scripts\python.exe'
)

$python = $pythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) {
  throw 'No local virtual environment found. Create one with: python -m venv .venv'
}

$env:PIP_NO_CACHE_DIR = '1'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$cudaIndexUrl = 'https://download.pytorch.org/whl/cu128'
$torchVersion = '2.10.0+cu128'
$torchvisionVersion = '0.25.0+cu128'

& $python -m pip install -r ocr_tool\requirements.txt
& $python -m pip install --force-reinstall "torch==$torchVersion" "torchvision==$torchvisionVersion" --index-url $cudaIndexUrl
