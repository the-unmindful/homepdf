param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$OcrArgs
)

$ErrorActionPreference = 'Stop'
$rootCandidates = @(
  (Split-Path -Parent $PSScriptRoot),
  (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
  (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique

$python = $null
$pythonRoot = $null
$toolRoot = $null

foreach ($candidateRoot in $rootCandidates) {
  if (-not $python) {
    foreach ($pythonCandidate in @(
      (Join-Path $candidateRoot 'venv\Scripts\python.exe'),
      (Join-Path $candidateRoot '.venv\Scripts\python.exe')
    )) {
      if (Test-Path $pythonCandidate) {
        $python = $pythonCandidate
        $pythonRoot = $candidateRoot
        break
      }
    }
  }

  if (-not $toolRoot) {
    $toolCandidate = Join-Path $candidateRoot 'ocr_tool'
    if (Test-Path (Join-Path $toolCandidate 'OCR.py')) {
      $toolRoot = $toolCandidate
    }
  }
}

if (-not $python) {
  throw 'No project-local virtual environment found. Expected .venv or venv under this workspace.'
}

if (-not $toolRoot) {
  throw 'OCR tool folder not found. Expected ocr_tool\\OCR.py near this launcher.'
}

if ($pythonRoot) {
  $preferredToolRoot = Join-Path $pythonRoot 'ocr_tool'
  if (Test-Path (Join-Path $preferredToolRoot 'OCR.py')) {
    $toolRoot = $preferredToolRoot
  }
}

$cacheRoot = Join-Path $toolRoot 'cache'

$env:HF_HOME = Join-Path $cacheRoot 'huggingface'
$env:HUGGINGFACE_HUB_CACHE = Join-Path $env:HF_HOME 'hub'
$env:TRANSFORMERS_CACHE = Join-Path $env:HF_HOME 'transformers'
$env:TORCH_HOME = Join-Path $cacheRoot 'torch'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONIOENCODING = 'utf-8'

New-Item -ItemType Directory -Force $env:HF_HOME, $env:HUGGINGFACE_HUB_CACHE, $env:TRANSFORMERS_CACHE, $env:TORCH_HOME | Out-Null

& $python -u (Join-Path $toolRoot 'OCR.py') @OcrArgs
exit $LASTEXITCODE
