# HomePDF

HomePDF (shown as **HOME PDF** in the app) is a Windows desktop PDF reader and toolkit built with PySide6 and PyMuPDF. PDF reading and editing run locally on your computer.

## Download and run

**Windows 10/11, 64-bit:** [Download the latest portable release](https://github.com/the-unmindful/homepdf/releases/latest).

1. Download `PDFUltimate-portable-20260307-215953.zip` from the release assets.
2. Extract the entire ZIP into a writable folder.
3. Run `PDFUltimate.exe`. Keep `_internal`, `scripts`, and `ocr_tool` beside the executable.
4. Open a PDF in the app, or drag a PDF onto the executable.

The reader requires no Python installation. The published package is the latest existing local build, **March 7, 2026**, build `20260307-215953`, with source package version `0.1.0`.

Windows may show an unsigned-app prompt. Settings and outputs normally live in `Documents\HOME PDF`. To keep them beside the executable, create an empty `portable.flag` file in the extracted folder; data will then use `HOME PDF_DATA`.

## Features

- PDF tabs, zoom, fit width/page, continuous viewing, thumbnails, outlines, search, and text reflow
- Merge, split, extract, delete, reorder, and rotate pages
- Watermarks, image stamps, compression, password protection, and unlock
- PDF export to DOCX, TXT, Markdown, HTML, JSON, RTF, PNG, and JPG
- Create PDFs from text, Markdown, RTF, HTML, DOCX, images, and PDFs
- Optional GLM OCR runner for text, tables, formulas, and structured extraction

## Run from source

Install **Python 3.11, 64-bit** on Windows, then run in PowerShell:

```powershell
git clone https://github.com/the-unmindful/homepdf.git
cd homepdf
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\desktop\requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_dev.ps1
```

Source runs keep application data in the checkout's `runtime_data` folder. Only desktop dependencies are required for the reader and PDF toolkit.

## Build a portable package

With the local virtual environment created above:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_portable.ps1
```

The script installs PyInstaller and desktop dependencies, then writes a dated ZIP and folder under `release`. It also refreshes `release\PDFUltimate-portable` and its ZIP. Use `-SkipInstall` only when those build dependencies are already installed. No installer is included in this release.

## Optional OCR

OCR requires a separate project-local Python environment and an NVIDIA GPU with CUDA support. Python, CUDA PyTorch, and model weights are **not bundled** with the portable reader. Initial installation and model download require internet access; subsequent runs can use the local cache.

For a source checkout:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_ocr_dependencies.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_ocr.ps1 --help
```

In the app's OCR tab, point **OCR Command** to the checkout's `scripts\ocr.bat`. For a portable folder, create `.venv` there, install `ocr_tool\requirements.txt` plus the CUDA PyTorch versions specified in [the installation script](scripts/install_ocr_dependencies.ps1), and select that folder's `scripts\ocr.bat`.

See [the OCR guide](ocr_tool/README.md) for modes, page selection, and output formats.

## Choose HomePDF as your PDF reader

From a source checkout, register the extracted executable for the current Windows user:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\register_portable_pdf_handler.ps1 -ExePath 'C:\Apps\HomePDF\PDFUltimate.exe'
```

Then open **Windows Settings > Apps > Default apps** and choose **HOME PDF** or `PDFUltimate.exe` for `.pdf`. To remove the registration, run `scripts\unregister_portable_pdf_handler.ps1`.

## Repository contents

- `desktop/`: reader, toolkit, UI, dependency pins, and app icon
- `scripts/`: source launcher, portable build, OCR launch/setup, and PDF registration
- `ocr_tool/`: optional OCR runner and example extraction schema
- `docs/`: published build metadata and checksum

Virtual environments, build outputs, personal documents, session data, and model caches are excluded from Git. The ready-to-run binary is distributed through GitHub Releases.

## License

Project source is covered by the repository's [MIT license](LICENSE). Third-party dependencies retain their own licenses; see [dependency notices](THIRD_PARTY.md). The repository license does not replace licenses of libraries bundled in the portable download.
