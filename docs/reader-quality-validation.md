# Reader quality validation

The bounded plan started on `codex/performance-ux` from `c2a0d5a` and is now merged into `main`. The initial portable candidate was built from `ff02cf0`; the ribbon/mode/theme and Windows registration follow-up is recorded below. At the user's request, GitHub release `v0.1.2` was published from `1e93146`. All existing reading/tool workflows remain local. No framework replacement was introduced.

## Highest-value results

| Measurement | Before | After |
| --- | ---: | ---: |
| Text-page process transport + Qt image creation | 63.12 ms | 15.07 ms (4.2x faster) |
| Image-heavy page, same measurement | 347.18 ms | 57.80 ms (6x faster) |
| Reader module import, median of three fresh processes | 382.14 ms | 249.68 ms (35% lower) |
| Unpacked portable package | 210.20 MiB | 168.22 MiB (20% smaller) |
| Portable ZIP | 87.46 MiB | 71.73 MiB |
| UI heartbeats during 200-page PNG export | 0 | 871; largest interval 16 ms |
| 200-page PNG export elapsed time | 8.464 s | 8.843 s |
| Fresh 1366 px window document viewport | cramped | 1070 logical px |

Export throughput is roughly unchanged, with a small overhead in this measurement. The gain is that the reader stays responsive and the operation is cancellable. Rendering timings are ten warmed process requests at zoom 1 and quality 2.1, including transport and QImage creation. Import timings measure module loading, not full launch-to-first-paint. These are same-machine synthetic benchmarks, not universal speed claims. Full measurements are in `reader-quality-results.json`.

The renderer has two active jobs, at most 24 pending requests, a hard 12 million pixel cap and a 96 MiB image cache. It removes obsolete generations and offscreen pending work, prioritizes visible pages, transports owned RGB, and repaints affected pages. Fit and zoom preserve reading position. Locked/unreadable opens clear stale state and recover on the next valid document.

Tools, search and text extraction use owned processes. Worker passwords travel through stdin. Outputs stay private until success, preserve existing files, support cross-drive publication, and roll back failed publication. Page edits, merge and split preserve tested metadata, detailed/internal/external bookmarks, annotations, links and form fields. Conversion labels describe text-only limits; unsupported RTF import is rejected.

Navigation combines Pages/Outline/Merge, Tools starts collapsed, secondary controls live in View, and tall forms scroll. Results offer Open result/Show folder without modal success dialogs. OCR tuning is collapsed, logs are bounded, and owned Windows descendants are stopped on Cancel or close. OCR progress stays in the app.

## Verification

- 45 unittest regressions pass, including real Windows QProcess, Unicode paths, collision-safe export, cancellation cleanup, cross-drive publication, detailed bookmarks and actual Windows parent/child termination on reader close.
- Frozen executable passes real render/search/export/cancel smoke tests at 100%, 125% and 150% Qt scaling; representative screenshots were inspected. First page render after document opening: 156-172 ms on the synthetic fixture.
- 18 frozen worker jobs pass: all eight export formats, six supported document/image import formats, password round trip, form/outline-preserving merge and search. The worker checks confirm no PySide6 imports.
- A PyInstaller runtime hook dispatches workers before the bundled Qt hook, preventing QtCore from loading in background workers. Ordinary GUI startup retains the standard Qt hooks.
- ZIP CRC, executable, helper and packaged icon were checked. NumPy/Pillow/Tkinter/Matplotlib/IPython are excluded from the reader build; required Qt/image/XML support remains. The unused pypdf runtime dependency is removed.
- One independent reviewer reported seven Important findings and no Critical findings. All seven were reproduced, fixed and retested. The reviewer verified the corrections and found no unresolved material issue in that follow-up.

## Review corrections

| Finding | Regression/fix |
| --- | --- |
| Synchronous failed launch left Tools disabled | Set UI/callback state before process launch; failed-start regression |
| Merge/split discarded structure or retargeted links | Document-aware PyMuPDF copy/select; metadata/forms/outline/omitted-link fixtures |
| Reorder reset rich bookmarks and removed URI entries | Preserve detailed TOC dictionaries; remap internal page destinations |
| Minimum raster scale overrode the cap | Remove upward floor; extreme geometry test before allocation |
| OCR descendants survived closing | Cancel owned tree and await bounded cleanup; real Windows descendant test |
| A0 Fit Page stayed above actual fit scale | Allow fit scales below manual minimum; large-page regression |
| Batch publication could leave partial output | Unique staged filenames and rollback; repeated-range/failure fixtures |

The incorrect collision sentinel, missing per-page progress and offscreen queue retention were also corrected. Runtime checks additionally caught relative-root staging and cross-drive fsync errors, now fixed.

## Reproduce

From the checkout, use Python 3.11 with `desktop/requirements.txt` and PyInstaller 6.19.0:

```powershell
python -m unittest discover -s tests -v
./scripts/build_portable.ps1 -SkipInstall
python scripts/verify_portable.py release/PDFUltimate-portable/PDFUltimate.exe runtime_data/format-check
```

The explicit smoke command is `PDFUltimate.exe --smoke-test <synthetic-pdf-containing-needle> <report-directory>`. It writes a JSON report and screenshots, uses offscreen Qt, and avoids ordinary Start-menu/single-instance startup. CI now runs reader regressions, frozen smoke and frozen format verification before publishing a tagged release.

Local candidate: `release/PDFUltimate-portable/PDFUltimate.exe`; archive: `release/PDFUltimate-portable.zip`. Keep the complete portable folder together. Intermediate validation artifacts are under ignored `runtime_data/validation`.

## Practical limits

Physical multi-monitor DPI transitions, screen-reader behavior and actual GLM-OCR model/GPU inference still need device-level checks. Offscreen tests verify layout and process behavior, not those hardware experiences. OCR remains an external runtime and may leave partial OCR output after cancellation. The work does not establish a competitive claim that HomePDF is the best reader; it makes the existing app substantially faster, more reliable and easier to use.

## Ribbon, modes, themes and Windows launch follow-up

- Reader and Text remain visible before Tools. Fresh documents default to Continuous; document-specific saved layout still restores. Single/Continuous both support View and Select Text. Extracted Text displays whole-document text, disables the irrelevant layout selector and preserves the layout for returning to pages.
- Search works through all six layout/text choices, including mode changes during extraction and Unicode cursor positions. Selection supports rotated pages and Continuous without extracting every page; releasing a drag removes its rectangle while keeping selected words and clipboard copying.
- Icon controls expose navigation, fit width/page, actual size, search, theme and tools. Navigation uses a page list and Tools a wrench; chevrons, checked state and descriptive hover text expose pane contents and direction. Visible dropdown chevrons apply to all selectors. Zoom presets synchronize the active choice with fit/actual-size actions.
- The ribbon uses one row when it fits, otherwise two aligned rows. Both panes at 1366 px leave more than 650 px of document viewport. Manual opening and dragging at 760/1000 px reserve readable controls, collapsing the opposite pane where needed. The redundant long page label yields space in a narrow reader; the page spinner remains available.
- System/Light/Dark applies to existing widgets and icons and persists. Dark chrome retains white PDF pages. Identical palette/stylesheet application is skipped. No new runtime dependencies.
- 81 source regressions pass. Packaged smoke checks pass at 100% and 150% scaling: rendering, bounded queue, 30 search matches, 30-file export, cancellation cleanup, six mode attachments and extracted-text search. Synthetic first render measured 156/172 ms; these are fixture timings, not universal startup claims. ZIP CRC, executable, helper and packaged icon pass verification.
- Windows Shell enumeration reproduced two recommended HomePDF entries and three total: legacy portable and missing Local\\Programs registrations. Repair leaves one recommended and one total handler. AssocQueryString, Get-StartApps and actual PDF launch all point to `release/PDFUltimate-portable/PDFUltimate.exe` in this worktree. The obsolete running reader closed normally and the same Downloads PDF reopened through Windows' PDF default. Protected UserChoice/UserChoiceLatest values/hashes remained unchanged. No app installation was performed.

Local validation executable SHA-256: `E3DA336E642B5859C8DC8899CB514A0C7977A71E47AB6C3F045A4DABD402D402`. That local ZIP is 75,229,903 bytes. The source plan is [ribbon/modes/themes](superpowers/plans/2026-10-02-ribbon-modes-themes.md); reports and Windows registration backups are under ignored `runtime_data`.

## Published release

[HomePDF v0.1.2](https://github.com/the-unmindful/homepdf/releases/tag/v0.1.2) was built from `1e93146e8cb0a4235138697cbc601d8a4ba4a041` by the Windows release workflow. All build, regression, frozen reader, format, archive/icon and startup/Start catalog checks passed. The published ZIP and checksum were downloaded and independently checked against GitHub's asset digest, ZIP CRC and source icon. Published build metadata and checksum are recorded in [latest-build.json](latest-build.json) and [SHA256SUMS.txt](SHA256SUMS.txt).
