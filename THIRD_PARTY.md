# Third-party dependencies

HomePDF uses independently licensed libraries. The repository's MIT license applies to HomePDF source and does not relicense its dependencies.

Desktop requirements are recorded in `desktop/requirements.txt`: PySide6 6.9.2 (Qt for Python), PyMuPDF 1.26.3, pypdf 5.9.0, and python-docx 1.2.0. The portable package also contains transitive dependencies and a Python 3.11 runtime.

The original portable archive retains bundled notices, including:

- `_internal/pymupdf-1.26.3.dist-info/COPYING`
- `_internal/pypdf-5.9.0.dist-info/licenses/LICENSE`
- `_internal/python_docx-1.2.0.dist-info/licenses/LICENSE`
- `_internal/numpy-2.3.5.dist-info/LICENSE.txt`

Consult each dependency's distribution and upstream license terms when redistributing or modifying a build. Optional OCR dependencies and GLM-OCR model weights are installed separately and have their own terms.
