from __future__ import annotations

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from typing import Optional

# A single shared ProcessPoolExecutor for CPU-heavy / PyMuPDF work.
# Using processes (not threads) avoids PyMuPDF thread-safety issues.
_EXECUTOR: ProcessPoolExecutor | None = None
_MAX_WORKERS: int | None = None


def get_process_pool(max_workers: Optional[int] = None) -> ProcessPoolExecutor:
    """
    Return a shared ProcessPoolExecutor instance.

    On Windows, multiprocessing uses spawn, so call multiprocessing.freeze_support()
    in the application entrypoint (see pdf_ultimate.app.run()).
    """
    global _EXECUTOR, _MAX_WORKERS
    if _EXECUTOR is not None:
        return _EXECUTOR

    if max_workers is None:
        cpu = os.cpu_count() or 2
        # Be conservative: PDF rendering is memory heavy. Keep some headroom.
        max_workers = max(1, min(3, cpu - 1))

    _MAX_WORKERS = int(max_workers)
    ctx = multiprocessing.get_context("spawn") if os.name == "nt" else multiprocessing.get_context()
    _EXECUTOR = ProcessPoolExecutor(max_workers=_MAX_WORKERS, mp_context=ctx)
    return _EXECUTOR


def shutdown_process_pool(wait: bool = False) -> None:
    """Shutdown the shared process pool (best effort)."""
    global _EXECUTOR, _MAX_WORKERS
    if _EXECUTOR is None:
        return
    try:
        # cancel_futures is supported on Python 3.9+
        _EXECUTOR.shutdown(wait=wait, cancel_futures=True)  # type: ignore[call-arg]
    except TypeError:
        _EXECUTOR.shutdown(wait=wait)
    finally:
        _EXECUTOR = None
        _MAX_WORKERS = None
