import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import Future
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desktop"))
import fitz
from PySide6.QtWidgets import QApplication
from pdf_ultimate.core import worker_tasks
from pdf_ultimate.core.render_service import PdfRenderService

class Executor:
    def __init__(self): self.calls = []
    def submit(self, fn, *args):
        future = Future()
        future.set_running_or_notify_cancel()
        self.calls.append((future, args))
        return future

class RenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])
    def test_large_page_has_hard_pixel_cap_and_owned_rgb(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "large.pdf"
            doc = fitz.open(); doc.new_page(width=2384, height=3370); doc.save(path); doc.close()
            result = worker_tasks.render_page_pixels(str(path), 0, 6, 3.2)
            self.assertLessEqual(result.width * result.height, 12_000_000)
            self.assertEqual(len(result.samples), result.stride * result.height)
    def service(self):
        executor = Executor()
        with patch("pdf_ultimate.core.render_service.get_process_pool", return_value=executor):
            service = PdfRenderService()
        return service, executor
    def queue(self, service, i, priority=2):
        return service.queue_render(key=("doc", i, 1., 1.), pdf_path=Path("a.pdf"), page_index=i, zoom=1, quality=1, priority=priority)
    def test_queue_is_bounded_and_visible_pages_precede_prefetch(self):
        service, executor = self.service()
        for i in range(80): self.queue(service, i)
        self.queue(service, 999, 0)
        self.assertEqual(len(executor.calls), 2)
        self.assertLessEqual(service.pending_count, 24)
        executor.calls[0][0].set_result(None); self.app.processEvents()
        self.assertEqual(executor.calls[2][1][1], 999)
    def test_obsolete_generation_never_emits_image(self):
        service, executor = self.service(); delivered = []
        service.rendered.connect(lambda *args: delivered.append(args))
        self.queue(service, 0); service.invalidate()
        executor.calls[0][0].set_result(None); self.app.processEvents()
        self.assertEqual(delivered, [])

if __name__ == "__main__": unittest.main()
