# HomePDF reader quality design

Implement the performance and UX review authorized on 2 October 2026. Keep HomePDF a local Windows PDF reader/toolkit with its existing PySide6/PyMuPDF stack. Improve existing behavior rather than expanding formats, adding cloud services, or replacing the framework.

## Acceptance criteria

- Fresh 1366 x 768 layout gives the PDF at least 750 logical pixels, with readable essential controls. Tools start collapsed; outline and thumbnails share one navigation surface. Tall forms scroll, secondary controls use a View menu, and the window remains usable at 1000 px and Windows display scales.
- Mode transitions display the correct widget. Locked/failed documents clear stale page state. Zoom and fit changes preserve a page-relative reading anchor. Fit Page fits both dimensions in either view mode.
- Rendering uses owned RGB bytes, at most two active jobs and 24 pending requests, visible pages before speculative work. Obsolete generations are dropped before decoding. Raster allocations cannot exceed 12 million pixels, including very large pages; image cache cannot exceed 96 MiB.
- Existing tools run in a cancellable subprocess distinct from rendering. Show progress and compact outcome actions. Cancellation/failure leave no partial published output. Text extraction and search run outside the UI event loop. Search replaces obsolete queries rather than accumulating scans.
- Output paths never silently overwrite existing files. Page operations preserve metadata, bookmarks with remapped destinations, annotations, links and forms where applicable. Add representative preservation tests and document limitations that remain.
- Startup avoids Qt imports in render/tool workers and performs shortcut registration asynchronously and only when its executable location changes. Audit optional packaged dependencies and validate a rebuilt portable executable.
- OCR remains external. Essential controls are first, tuning lives in Advanced, Run is disabled while running, cancellation controls the launched process tree, and normal runs use the in-app log.
- Conversion labels accurately state text-only limitations. Reject unsupported RTF import rather than producing PDFs containing RTF control syntax. Retain existing supported exports.
- Add Ctrl+Tab/Shift+Ctrl+Tab and focus/accessibility labels, simplify success feedback, and maintain offline reading. No new mandatory runtime dependencies.

## Architecture

Keep the existing main window while extracting bounded rendering, output safety and process job control into small core modules. Use the existing render pool for short renders only. A Qt QProcess launches the same executable (or source entrypoint) in a CLI worker mode for tools and search; JSON over stdin/stdout carries typed arguments, progress, results and failures. Workers write outputs in a temporary run folder; publish successful complete files with collision-safe names. Cancel terminates the isolated process and removes staging files.

Use minimal regression tests with real PDFs and real Qt widgets. Process-boundary tests exercise subprocesses rather than mocking toolkit behavior. Record the RED/GREEN results and task commits in the plan ledger. Before final delivery, run the full suite, a fresh code review, source/portable smoke checks and the review fixtures. Preserve the original checkout and user data.

## Constraints

Python 3.11; PySide6 6.9.2; PyMuPDF 1.26.3; pypdf 5.9.0; python-docx 1.2.0. Windows 10/11 x64. Never run concurrent PyMuPDF work in threads. No network calls during ordinary reading. Do not publish a release, merge main, change repository licensing, or install OCR models as part of this plan.
