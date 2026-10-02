# Reader quality validation

The bounded plan is complete on `codex/performance-ux`, starting from `c2a0d5a`. The local portable candidate was built from `ff02cf0`; no release was published. All existing reading/tool workflows remain local. No feature expansion or framework replacement was introduced.

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
