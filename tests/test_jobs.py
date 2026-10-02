import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desktop"))
import fitz
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from pdf_ultimate.core.job_service import JobService

class JobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.root = Path(self.folder.name)
        self.source = self.root / "r\u00e9sum\u00e9_\u65e5\u672c.pdf"
        with fitz.open() as doc:
            for i in range(30): doc.new_page().insert_text((60,60), "needle page " + str(i))
            doc.save(self.source)
        self.service = JobService(workspace_root=self.root / "jobs")
        self.results = []; self.errors = []; self.progress = []; self.cancelled = []
        self.service.finished.connect(self.results.append); self.service.failed.connect(self.errors.append)
        self.service.progress.connect(self.progress.append); self.service.cancelled.connect(lambda: self.cancelled.append(True))
    def tearDown(self):
        self.service.cancel()
        self.wait_idle(); self.service.deleteLater(); self.app.processEvents(); self.folder.cleanup()
    def wait_idle(self):
        deadline = time.monotonic() + 20
        while self.service.busy and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(.005)
        self.assertFalse(self.service.busy)
    def test_export_is_responsive_and_collision_safe(self):
        output = self.root / "out"; output.mkdir(); prior = output / (self.source.stem + "_page_1.png")
        prior.write_bytes(b"sentinel")
        ticks = []; timer = QTimer(); timer.timeout.connect(lambda: ticks.append(1)); timer.start(10)
        self.service.start("convert", [self.source, "png", output]); self.wait_idle(); timer.stop()
        self.assertFalse(self.errors); self.assertEqual(len(self.results[0]["outputs"]), 30)
        self.assertEqual(prior.read_bytes(), b"sentinel"); self.assertGreater(len(ticks), 5)
        self.assertTrue(self.progress); self.assertEqual(list((self.root / "jobs").iterdir()), [])
    def test_cancel_cleans_private_outputs(self):
        self.service.start("convert", [self.source, "png", self.root / "cancelled"])
        self.app.processEvents(); self.service.cancel(); self.wait_idle()
        self.assertTrue(self.cancelled); self.assertEqual(self.results, [])
        self.assertFalse((self.root / "cancelled").exists())
        self.assertEqual(list((self.root / "jobs").iterdir()), [])
    def test_worker_error_is_reported_without_publishing(self):
        self.service.start("rotate_pages", [self.root / "missing.pdf", "all", 90, self.root / "bad.pdf"])
        self.wait_idle(); self.assertTrue(self.errors); self.assertFalse((self.root / "bad.pdf").exists())
    def test_search_and_text_run_in_worker(self):
        self.service.start("search", [self.source, list(range(30)), "needle"]); self.wait_idle()
        self.assertEqual(len(self.results[-1]["value"]["sequence"]), 30)
        self.assertFalse(self.results[-1]["qt_imported"])
        self.service.start("reader_text", [self.source, list(range(30))]); self.wait_idle()
        self.assertIn("Page 30", self.results[-1]["value"])

if __name__ == "__main__": unittest.main()
