# GLM OCR Sub-Tool

This folder contains the optional local GLM-OCR runner for HomePDF.
It is separate from the reader and is not required to open or edit PDFs.

It uses the project-local virtual environment at `.venv` and keeps its model
and torch caches inside this workspace under `ocr_tool/cache/`.
OCR inference is GPU-only. The tool will fail immediately if CUDA is not
available in `.venv`.

## Layout

```text
ocr_tool/
  OCR.py
  requirements.txt
  input/
  output/
  cache/
  schemas/
```

## Install

```powershell
.\scripts\install_ocr_dependencies.ps1
```

## Run

Default behavior:

```powershell
.\scripts\run_ocr.ps1
```

Custom input folder:

```powershell
.\scripts\run_ocr.ps1 --input_dir "E:\Scans"
```

Images to JSON:

```powershell
.\scripts\run_ocr.ps1 --mode images --output_format json
```

All PDFs in an input folder:

```powershell
.\scripts\run_ocr.ps1 --mode all_pdfs --input_dir "E:\BatchPDFs"
```

Table OCR to HTML:

```powershell
.\scripts\run_ocr.ps1 --task table --output_format html
```

Single page from a PDF (page 2 only):

```powershell
.\scripts\run_ocr.ps1 --mode pdf --pdf_mode pages --pages 2
```

Selected ranges:

```powershell
.\scripts\run_ocr.ps1 --mode pdf --pages 1-3,8,10-
```

Structured extraction:

```powershell
.\scripts\run_ocr.ps1 --task extract --output_format json --schema ".\ocr_tool\schemas\example_extract_schema.txt"
```

## Notes

- `scripts\install_ocr_dependencies.ps1` installs the CUDA PyTorch build into
  the local `.venv`.
- `pdf2image` is supported and will use Poppler from your `PATH`.
- A PyMuPDF fallback is also present, so PDF rendering still works if
  `pdf2image` fails.
- The first model run can download a large Hugging Face model into
  `ocr_tool/cache/huggingface/`.
- Outputs are written to `ocr_tool/output/` by default.
- `--pages` works for PDF page mode only.
- `--pages` cannot be used with `--pdf_mode direct`.
- `--pages` cannot be combined with explicit `--max_pages`.
