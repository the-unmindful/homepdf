param(
  [Parameter(Mandatory = $true)]
  [string]$LatestPortableDir,
  [Parameter(Mandatory = $true)]
  [string]$StablePortableDir,
  [Parameter(Mandatory = $true)]
  [string]$LatestZipPath,
  [Parameter(Mandatory = $true)]
  [string]$StableZipPath,
  [Parameter(Mandatory = $true)]
  [string]$ArchiveRoot
)

$ErrorActionPreference = 'Stop'

$latestPortableDir = (Resolve-Path -LiteralPath $LatestPortableDir).Path
$latestZipPath = (Resolve-Path -LiteralPath $LatestZipPath).Path
$stablePortableDir = [System.IO.Path]::GetFullPath($StablePortableDir)
$stableZipPath = [System.IO.Path]::GetFullPath($StableZipPath)
$archiveRoot = [System.IO.Path]::GetFullPath($ArchiveRoot)

New-Item -ItemType Directory -Force $ArchiveRoot | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

if (Test-Path -LiteralPath $stablePortableDir) {
  $archivePortableDir = Join-Path $ArchiveRoot "PDFUltimate-portable-prev-$stamp"
  Move-Item -LiteralPath $stablePortableDir -Destination $archivePortableDir
}

if (Test-Path -LiteralPath $stableZipPath) {
  $archiveZipPath = Join-Path $ArchiveRoot "PDFUltimate-portable-prev-$stamp.zip"
  Move-Item -LiteralPath $stableZipPath -Destination $archiveZipPath
}

Copy-Item -LiteralPath $latestPortableDir -Destination $stablePortableDir -Recurse
Copy-Item -LiteralPath $latestZipPath -Destination $stableZipPath

Write-Host "Stable default portable path updated: $stablePortableDir"
Write-Host "Stable default portable zip updated: $stableZipPath"
