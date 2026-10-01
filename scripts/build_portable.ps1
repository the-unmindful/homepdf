param(
  [switch]$SkipInstall
)

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

if (-not $SkipInstall) {
  & $python -m pip install -r desktop\requirements.txt pyinstaller
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$workPath = Join-Path $root "build\pyi-$stamp"
$distPath = Join-Path $root "dist\pyi-$stamp"
$iconPath = Join-Path $root 'desktop\pdf_ultimate\resources\app.ico'
$pyiArgs = @(
  '--noconfirm',
  '--windowed',
  '--workpath', $workPath,
  '--distpath', $distPath,
  '--name', 'PDFUltimate',
  '--collect-all', 'fitz',
  '--collect-all', 'pypdf',
  '--collect-all', 'docx'
)
if (Test-Path $iconPath) {
  $pyiArgs += @('--icon', $iconPath)
}
$pyiArgs += 'desktop\main.py'

& $python -m PyInstaller @pyiArgs
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$releaseRoot = Join-Path $root 'release'
$stablePortableDir = Join-Path $releaseRoot 'PDFUltimate-portable'
$stableZipPath = Join-Path $releaseRoot 'PDFUltimate-portable.zip'
$versionedPortableDir = Join-Path $releaseRoot "PDFUltimate-portable-$stamp"
$versionedZipPath = Join-Path $releaseRoot "PDFUltimate-portable-$stamp.zip"
$latestMarker = Join-Path $releaseRoot 'LATEST_PORTABLE_PATH.txt'
$latestZipMarker = Join-Path $releaseRoot 'LATEST_PORTABLE_ZIP.txt'
$stableMarker = Join-Path $releaseRoot 'DEFAULT_PORTABLE_PATH.txt'
$stableZipMarker = Join-Path $releaseRoot 'DEFAULT_PORTABLE_ZIP.txt'
$pendingSyncScript = Join-Path $releaseRoot 'APPLY_LATEST_PORTABLE_TO_DEFAULT.ps1'
$syncScript = Join-Path $PSScriptRoot 'sync_portable_default.ps1'
$archiveRoot = Join-Path $root 'temp\moved_artifacts\portable-default-history'

New-Item -ItemType Directory -Force $releaseRoot | Out-Null
$distPortable = Join-Path $distPath 'PDFUltimate'
if (-not (Test-Path $distPortable)) {
  throw "Build output not found: $distPortable"
}

$portableScriptsDir = Join-Path $distPortable 'scripts'
$portableOcrToolDir = Join-Path $distPortable 'ocr_tool'
New-Item -ItemType Directory -Force $portableScriptsDir | Out-Null
New-Item -ItemType Directory -Force $portableOcrToolDir | Out-Null
New-Item -ItemType Directory -Force (Join-Path $portableOcrToolDir 'schemas') | Out-Null
New-Item -ItemType Directory -Force (Join-Path $portableOcrToolDir 'input') | Out-Null
New-Item -ItemType Directory -Force (Join-Path $portableOcrToolDir 'output') | Out-Null
New-Item -ItemType Directory -Force (Join-Path $portableOcrToolDir 'cache') | Out-Null

Copy-Item 'scripts\ocr.bat' (Join-Path $portableScriptsDir 'ocr.bat') -Force
Copy-Item 'scripts\run_ocr.ps1' (Join-Path $portableScriptsDir 'run_ocr.ps1') -Force
Copy-Item 'ocr_tool\OCR.py' (Join-Path $portableOcrToolDir 'OCR.py') -Force
Copy-Item 'ocr_tool\README.md' (Join-Path $portableOcrToolDir 'README.md') -Force
Copy-Item 'ocr_tool\requirements.txt' (Join-Path $portableOcrToolDir 'requirements.txt') -Force
Copy-Item 'ocr_tool\schemas\*' (Join-Path $portableOcrToolDir 'schemas') -Recurse -Force

if (Test-Path $versionedPortableDir) {
  throw "Versioned portable folder already exists: $versionedPortableDir"
}
if (Test-Path $versionedZipPath) {
  throw "Versioned portable zip already exists: $versionedZipPath"
}

Copy-Item $distPortable $versionedPortableDir -Recurse
Compress-Archive -Path (Join-Path $versionedPortableDir '*') -DestinationPath $versionedZipPath -Force

Set-Content -Path $latestMarker -Value $versionedPortableDir -Encoding UTF8
Set-Content -Path $latestZipMarker -Value $versionedZipPath -Encoding UTF8

$syncSucceeded = $false
try {
  & powershell -NoProfile -ExecutionPolicy Bypass -File $syncScript `
    -LatestPortableDir $versionedPortableDir `
    -StablePortableDir $stablePortableDir `
    -LatestZipPath $versionedZipPath `
    -StableZipPath $stableZipPath `
    -ArchiveRoot $archiveRoot
  $syncSucceeded = $true
  Set-Content -Path $stableMarker -Value $stablePortableDir -Encoding UTF8
  Set-Content -Path $stableZipMarker -Value $stableZipPath -Encoding UTF8
  if (Test-Path $pendingSyncScript) {
    Move-Item -LiteralPath $pendingSyncScript -Destination (Join-Path $archiveRoot ("APPLY_LATEST_PORTABLE_TO_DEFAULT-$stamp.ps1")) -Force
  }
}
catch {
  $pendingScriptContent = @"
param()
`$ErrorActionPreference = 'Stop'
& powershell -NoProfile -ExecutionPolicy Bypass -File "$syncScript" -LatestPortableDir "$versionedPortableDir" -StablePortableDir "$stablePortableDir" -LatestZipPath "$versionedZipPath" -StableZipPath "$stableZipPath" -ArchiveRoot "$archiveRoot"
"@
  Set-Content -Path $pendingSyncScript -Value $pendingScriptContent -Encoding ASCII
  Write-Host "Stable default portable path is currently locked."
  Write-Host "Close HOME PDF and run: $pendingSyncScript"
}

Write-Host "Versioned portable package created: $versionedZipPath"
Write-Host "Versioned portable folder: $versionedPortableDir"
if ($syncSucceeded) {
  Write-Host "Stable default portable path refreshed: $stablePortableDir"
  Write-Host "Stable default portable zip refreshed: $stableZipPath"
}
