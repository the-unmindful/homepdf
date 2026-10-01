# Portable Windows download

Download [HomePDF-portable.zip](https://github.com/the-unmindful/homepdf/releases/latest/download/HomePDF-portable.zip) from the [latest GitHub Release](https://github.com/the-unmindful/homepdf/releases/latest).

Extract the entire archive and run `PDFUltimate.exe`. Keep the `_internal`, `scripts`, and `ocr_tool` folders alongside it. The reader needs Windows 10/11 x64 and no separate Python installation.

Starting with `v0.1.1`, opening the portable app also creates its HomePDF Start menu shortcut automatically. **Add HomePDF to Start.cmd** provides a double-click alternative.

Release assets include `SHA256SUMS.txt` for download verification. Version `v0.1.0` contains the October 2, 2026 build, `20261002-010304`, with the green H Windows icon and `scripts/create_start_menu_shortcut.ps1`.

ZIP files are stored as release assets rather than in Git, so future builds do not increase the size of source clones. Push a new version tag to publish future Windows builds using the release workflow.
