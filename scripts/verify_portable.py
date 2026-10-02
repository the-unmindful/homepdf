"""Validate frozen workers and formats without opening a normal app instance."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import uuid
import zipfile

import fitz


def main(executable: Path, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    source = root / "r\u00e9sum\u00e9_\u65e5\u672c.pdf"
    with fitz.open() as doc:
        for i in range(3):
            doc.new_page().insert_text((60, 60), f"needle page {i + 1}")
        doc.set_metadata({"title": "Preserved title"})
        doc.set_toc([[1, "First", 1], [1, "Last", 3]])
        widget = fitz.Widget()
        widget.field_name = "name"
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.rect = fitz.Rect(60, 100, 160, 130)
        widget.field_value = "Alice"
        doc[0].add_widget(widget)
        doc.save(source)
    def encode(value):
        if isinstance(value, Path): return {"__path__": str(value.resolve())}
        if isinstance(value, list): return [encode(item) for item in value]
        return value
    reports = []
    def job(operation, args):
        workspace = root / ("job-" + uuid.uuid4().hex)
        workspace.mkdir()
        request = {"operation": operation, "args": encode(args), "workspace": str(workspace.resolve())}
        process = subprocess.run([str(executable.resolve()), "--tool-worker"], input=json.dumps(request).encode(), capture_output=True, timeout=30)
        messages = [json.loads(line) for line in process.stdout.splitlines() if line.startswith(b'{')]
        assert process.returncode == 0, (operation, messages, process.stderr)
        result = next(item for item in messages if item.get("type") == "result")
        assert not result["qt_imported"], operation
        files = [Path(item["staged"]) for item in result["files"]]
        assert all(path.is_file() and path.stat().st_size for path in files)
        reports.append({"operation": operation, "outputs": len(files), "qt_imported": False})
        return files, result
    formats = {}
    for target in ("docx", "txt", "md", "html", "json", "rtf", "png", "jpg"):
        files, _ = job("convert", [source, target, root / ("export-" + target)])
        assert len(files) == (3 if target in {"png", "jpg"} else 1)
        formats[target] = files
        if target == "docx":
            with zipfile.ZipFile(files[0]) as package: assert package.testzip() is None
        elif target in {"png", "jpg"}:
            with fitz.open(files[0]) as image: assert image.page_count == 1
        elif target == "json":
            assert len(json.loads(files[0].read_text(encoding="utf-8"))["pages"]) == 3
        else:
            assert "needle" in files[0].read_text(encoding="utf-8")
    for extension in ("docx", "txt", "md", "html", "png", "jpg"):
        files, _ = job("convert_to_pdf", [[formats[extension][0]], root / f"from-{extension}.pdf"])
        with fitz.open(files[0]) as doc: assert doc.page_count > 0
    options = {"__options__": {"allow_print": True, "allow_copy": True, "allow_modify": False, "allow_annotate": True}}
    protected, _ = job("protect", [source, "secret", "owner", options, root / "protected.pdf"])
    unlocked, _ = job("unlock", [protected[0], "secret", root / "unlocked.pdf"])
    with fitz.open(unlocked[0]) as doc:
        assert doc.get_toc() == [[1, "First", 1], [1, "Last", 3]]
        assert next(doc[0].widgets()).field_value == "Alice"
        assert doc.metadata["title"] == "Preserved title"
    merged, _ = job("merge", [[source, source], root / "merged.pdf"])
    with fitz.open(merged[0]) as doc:
        assert doc.page_count == 6 and len(doc.get_toc()) == 4
        assert next(doc[3].widgets()).field_value == "Alice"
    _, search = job("search", [source, [0, 1, 2], "needle"])
    assert len(search["value"]["sequence"]) == 3
    report = {"passed": True, "worker_jobs": reports}
    (root / "formats-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Verified {len(reports)} frozen worker jobs: exports, imports, password round trip, forms, merge and search.")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
