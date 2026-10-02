# HomePDF Reader Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The user authorized creating and executing the full plan. Steps use checkbox syntax for tracking.

**Goal:** Make existing reading and toolkit workflows fast, reliable and compact without expanding the app's scope.

**Architecture:** Bounded process rendering with owned raw pixels, a separate cancellable QProcess job channel, collision-safe staged outputs and a reader-first Qt layout. Keep the existing stack and extract only the core responsibilities needed by this plan.

**Tech Stack:** Python 3.11, PySide6 6.9.2, PyMuPDF 1.26.3, pypdf 5.9.0, python-docx 1.2.0, unittest.

**Spec:** docs/superpowers/specs/2026-10-02-reader-quality-design.md

## Global Constraints

- Windows 10/11 x64; no new mandatory dependencies or ordinary-reading network calls.
- PyMuPDF processing stays in processes, never competing threads.
- Preserve existing files and user state; no silent overwrites or partial published outputs.
- Two active renders, 24 pending requests, 12 million pixels per render, 96 MiB image cache.
- Work only on codex/performance-ux; do not merge or publish a release.

## Review Focus

- Failed/cancelled jobs and destination collisions must not damage originals or earlier results (Tasks 1 and 5).
- Large-page, rapid-zoom and tab-switch generations must not bypass memory limits or display stale images (Tasks 2 and 4).
- Encrypted or unreadable documents must leave a coherent viewer and recover on the next successful open (Task 2).
- Page removal/reordering/duplication must keep navigation destinations meaningful, and protect/unlock must retain forms (Task 1).
- Frozen worker entrypoints, Windows process cancellation and Unicode paths must work without GUI initialization (Tasks 5, 6 and 8).

### Task 1: Safe outputs and document fidelity
**Files:** core/pdf_tools.py, core/output_safety.py, tests/test_pdf_integrity.py.
**Interfaces:** `unique_path(path: Path) -> Path`; toolkit public signatures unchanged.
- [x] Write tests for rotate/delete/reorder metadata/bookmarks, annotations/links/forms, duplicate page selections, and non-overwriting repeated outputs.
- [x] Run `python -m unittest discover -s tests -p test_pdf_integrity.py -v`; expect preservation/collision failures.
- [x] Add safe publication helpers and document-preserving page operations; keep source untouched and remap destinations.
- [x] Run targeted tests and full suite; expect all passing. Commit the task.

### Task 2: Correct reader transitions and anchors
**Files:** ui/main_window.py, ui/continuous_view.py, tests/test_reader.py.
**Interfaces:** `_capture_reading_anchor() -> tuple[int, float, float]`; `_restore_reading_anchor(anchor) -> None`.
- [x] Write real offscreen Qt tests for selection/text widget changes, locked/failed opens, zoom anchor preservation and Fit Page.
- [x] Run targeted tests; expect current transition/anchor failures.
- [x] Centralize view attachment/clearing and preserve page-relative coordinates through zoom/fit/layout changes.
- [x] Run targeted tests and suite; commit.

### Task 3: Compact reading layout
**Files:** ui/main_window.py, ui/theme.py, tests/test_reader.py.
**Interfaces:** existing panel toggle actions; one navigation tab control for outline/thumbnails.
- [x] Add tests for fresh 1366 px viewport >=750 px and essential controls >= their minimum hints; tall tools scroll.
- [x] Run tests; expect cramped-layout failures.
- [x] Collapse tools initially, consolidate navigation, simplify essential toolbar and move secondary actions into View. Apply restrained button states and visible focus.
- [x] Verify 1366/1000 px screenshots and scale variants, run suite; commit.

### Task 4: Bounded raw-pixel rendering
**Files:** core/worker_tasks.py, core/render_service.py, ui/main_window.py, ui/continuous_view.py, tests/test_rendering.py.
**Interfaces:** `RenderedPage(width, height, stride, samples)`; `render_page_pixels(path, page, zoom, quality)`; renderer `queue_render(..., priority=0)`, `invalidate()`, `retain(keys)`.
- [x] Write tests for pixel cap, owned RGB display data, visible priority, bounded pending work and stale generations.
- [x] Run tests; expect missing/broken behavior.
- [x] Implement scheduler and raw payload; limit cache, prioritize visible pages and local repaint. Retain existing images while final zoom renders.
- [x] Run rendering tests and full suite; rerun transport benchmark; commit.

### Task 5: Cancellable background tools, text and search
**Files:** core/job_service.py, core/job_worker.py, desktop/main.py, core/pdf_tools.py, ui/main_window.py, tests/test_jobs.py.
**Interfaces:** `JobService.start(operation, args, kwargs=None)`; progress/result/error/cancel signals; `run_worker() -> int` JSON CLI.
- [x] Write subprocess tests for real export, progress, failure/cancel cleanup, Unicode/collision outputs and search; Qt test verifies heartbeat while a tool runs.
- [x] Run tests; expect missing async behavior.
- [x] Stage outputs, run existing handlers through one process channel, separate search channel, replace obsolete queries, asynchronously extract converted/reflow text.
- [x] Run job tests and full suite; compare 200-page export heartbeat; commit.

### Task 6: Startup, outcomes and honest conversion
**Files:** app.py, windows_integration.py, ui/main_window.py, core/pdf_tools.py, tests/test_windows_integration.py, tests/test_jobs.py, README.md.
**Interfaces:** safe bootstrap before GUI import; persisted successful registration location.
- [x] Add worker import/startup, registration-repeat, RTF rejection and tab-key tests; run for expected failures.
- [x] Lazy-load optional conversion imports, register shortcut asynchronously/conditionally, add compact Open/Show folder outcome actions, keyboard/tab/accessibility behavior, accurate text-only labels.
- [x] Run suite; commit.

### Task 7: Essential OCR controls and cancellation
**Files:** ui/main_window.py, tests/test_reader.py.
**Interfaces:** `_cancel_ocr()`; existing runner and argument protocol retained.
- [x] Add tests for primary/Advanced visibility, disabled Run while active and cancel state.
- [x] Run for expected failures.
- [x] Scroll OCR form, group tuning under Advanced, default to in-app logs, disable active Run, terminate owned process tree on cancel/close and cap log growth.
- [x] Run suite; commit.

### Task 8: Portable validation and final review
**Files:** scripts/build_portable.ps1, .github/workflows, README.md, docs/reader-quality-validation.md, tests/.
**Interfaces:** existing build script flags/output paths retained.
- [x] Audit import graph and optional hooks; exclude only verified optional packages and keep required image/plugin support.
- [x] Run complete suite, build portable app using existing environment, exercise frozen workers/render/search and cancellation with synthetic fixtures; inspect representative screenshots.
- [x] Record before/after latency, heartbeat, package size and verification limitations.
- [x] Request fresh whole-branch review; reproduce/fix important findings and rerun suite. Commit and report branch, results and remaining device-level checks.
