<#
Generates desktop\pdf_ultimate\resources\app.ico: a deep green rounded square with a white "H".
Uses WPF (PresentationCore) to draw each size, so no extra tools are needed.

  powershell -ExecutionPolicy Bypass -File .\scripts\make_icon.ps1
#>
param([string]$OutFile = (Join-Path $PSScriptRoot "..\desktop\pdf_ultimate\resources\app.ico"))

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName PresentationCore, WindowsBase

$sizes = 16, 20, 24, 32, 40, 48, 64, 256

function Render-Icon([int]$size) {
    $s = [double]$size
    $dv = New-Object System.Windows.Media.DrawingVisual
    $dc = $dv.RenderOpen()

    # Background: rounded square with a top-to-bottom deep green gradient.
    $inset = [Math]::Max(0.5, $s * 0.03)
    $radius = $s * 0.22
    $rect = New-Object System.Windows.Rect($inset, $inset, ($s - 2 * $inset), ($s - 2 * $inset))
    $grad = New-Object System.Windows.Media.LinearGradientBrush(
        [System.Windows.Media.Color]::FromRgb(0x16, 0x65, 0x34),
        [System.Windows.Media.Color]::FromRgb(0x06, 0x4E, 0x3B),
        90.0)
    $edge = New-Object System.Windows.Media.Pen(
        (New-Object System.Windows.Media.SolidColorBrush([System.Windows.Media.Color]::FromRgb(0x04, 0x3F, 0x2E))),
        [Math]::Max(1.0, $s / 32.0))
    $dc.DrawRoundedRectangle($grad, $edge, $rect, $radius, $radius)

    # Glyph: bold white "H", centred on its ink bounds (not the font's line box).
    $face = New-Object System.Windows.Media.Typeface(
        (New-Object System.Windows.Media.FontFamily("Segoe UI")),
        [System.Windows.FontStyles]::Normal,
        [System.Windows.FontWeights]::Black,
        [System.Windows.FontStretches]::Normal)
    $ft = New-Object System.Windows.Media.FormattedText(
        "H", [Globalization.CultureInfo]::InvariantCulture,
        [System.Windows.FlowDirection]::LeftToRight, $face, ($s * 0.92),
        [System.Windows.Media.Brushes]::White, 1.0)
    $geo = $ft.BuildGeometry((New-Object System.Windows.Point(0, 0)))
    $b = $geo.Bounds
    $target = $s * 0.66                       # glyph height as a share of the icon
    $k = $target / $b.Height
    $tg = New-Object System.Windows.Media.TransformGroup
    $tg.Children.Add((New-Object System.Windows.Media.TranslateTransform(-($b.X + $b.Width / 2), -($b.Y + $b.Height / 2))))
    $tg.Children.Add((New-Object System.Windows.Media.ScaleTransform($k, $k)))
    $tg.Children.Add((New-Object System.Windows.Media.TranslateTransform(($s / 2), ($s / 2))))
    $geo.Transform = $tg

    # Soft shadow under the glyph for depth (skipped at tiny sizes where it just blurs).
    if ($size -ge 32) {
        $dc.PushTransform((New-Object System.Windows.Media.TranslateTransform(0, ($s * 0.025))))
        $dc.DrawGeometry((New-Object System.Windows.Media.SolidColorBrush([System.Windows.Media.Color]::FromArgb(90, 0, 0x2C, 0x18))), $null, $geo)
        $dc.Pop()
    }
    $dc.DrawGeometry([System.Windows.Media.Brushes]::White, $null, $geo)
    $dc.Close()

    $rtb = New-Object System.Windows.Media.Imaging.RenderTargetBitmap($size, $size, 96, 96, [System.Windows.Media.PixelFormats]::Pbgra32)
    $rtb.Render($dv)
    # ICO frames want straight (non-premultiplied) alpha.
    return New-Object System.Windows.Media.Imaging.FormatConvertedBitmap($rtb, [System.Windows.Media.PixelFormats]::Bgra32, $null, 0)
}

function To-Png($bmp) {
    $enc = New-Object System.Windows.Media.Imaging.PngBitmapEncoder
    $enc.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($bmp))
    $ms = New-Object System.IO.MemoryStream
    $enc.Save($ms)
    # Leading comma stops PowerShell unrolling the byte[] into object[].
    return ,$ms.ToArray()
}

# Classic 32-bit DIB frame (BITMAPINFOHEADER + bottom-up BGRA + empty AND mask).
function To-Dib($bmp, [int]$size) {
    $stride = $size * 4
    $px = New-Object byte[] ($stride * $size)
    $bmp.CopyPixels($px, $stride, 0)
    $maskStride = [int]([Math]::Ceiling($size / 32.0) * 4)
    $ms = New-Object System.IO.MemoryStream
    $w = New-Object System.IO.BinaryWriter($ms)
    $w.Write([int]40); $w.Write([int]$size); $w.Write([int]($size * 2))
    $w.Write([int16]1); $w.Write([int16]32); $w.Write([int]0)
    $w.Write([int]($px.Length + $maskStride * $size))
    $w.Write([int]0); $w.Write([int]0); $w.Write([int]0); $w.Write([int]0)
    for ($y = $size - 1; $y -ge 0; $y--) { $w.Write($px, $y * $stride, $stride) }
    $w.Write((New-Object byte[] ($maskStride * $size)))
    $w.Flush()
    return ,$ms.ToArray()
}

$frames = foreach ($sz in $sizes) {
    $bmp = Render-Icon $sz
    # 256 px as PNG keeps the file small; smaller sizes as DIB for widest compatibility.
    [byte[]]$data = if ($sz -ge 256) { To-Png $bmp } else { To-Dib $bmp $sz }
    [pscustomobject]@{ Size = $sz; Data = $data }
}

$dir = Split-Path -Parent $OutFile
if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }

$out = New-Object System.IO.MemoryStream
$w = New-Object System.IO.BinaryWriter($out)
$w.Write([int16]0); $w.Write([int16]1); $w.Write([int16]$frames.Count)
$offset = 6 + 16 * $frames.Count
foreach ($f in $frames) {
    $dim = if ($f.Size -ge 256) { 0 } else { $f.Size }
    $w.Write([byte]$dim); $w.Write([byte]$dim); $w.Write([byte]0); $w.Write([byte]0)
    $w.Write([int16]1); $w.Write([int16]32)
    $w.Write([int]$f.Data.Length); $w.Write([int]$offset)
    $offset += $f.Data.Length
}
foreach ($f in $frames) { $w.Write([byte[]]$f.Data, 0, $f.Data.Length) }
$w.Flush()
[System.IO.File]::WriteAllBytes([System.IO.Path]::GetFullPath($OutFile), $out.ToArray())

# Preview PNG next to the script output dir (not shipped) for a quick visual check.
if ($env:HOME_PDF_ICON_PREVIEW) {
    [System.IO.File]::WriteAllBytes($env:HOME_PDF_ICON_PREVIEW, [byte[]](To-Png (Render-Icon 256)))
}
Write-Host "Wrote $([System.IO.Path]::GetFullPath($OutFile)) ($($frames.Count) sizes: $($sizes -join ', '))"
