"""Stage complete outputs and publish without replacing existing files."""
from __future__ import annotations

from functools import wraps
import errno
import inspect
import os
from pathlib import Path
import shutil
import tempfile


def unique_path(path: Path) -> Path:
    path = Path(path)
    candidate = path
    number = 1
    while candidate.exists():
        candidate = path.with_name(f'{path.stem} ({number}){path.suffix}')
        number += 1
    return candidate


def publish_file(staged: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    while True:
        target = unique_path(destination)
        try:
            if os.name == 'nt':
                # Windows rename fails if target exists; it never replaces it.
                os.rename(staged, target)
            else:
                os.link(staged, target)
                staged.unlink()
            return target
        except FileExistsError:
            continue
        except OSError as exc:
            if exc.errno != errno.EXDEV and getattr(exc, 'winerror', None) != 17:
                raise
            # Publish atomically even when the worker workspace is on another drive.
            fd, name = tempfile.mkstemp(prefix='.homepdf-', dir=destination.parent)
            os.close(fd)
            copied = Path(name)
            try:
                shutil.copyfile(staged, copied)
                with copied.open('r+b') as handle:
                    os.fsync(handle.fileno())
                result = publish_file(copied, destination)
                try:
                    staged.unlink()
                except OSError:
                    result.unlink(missing_ok=True)
                    raise
                return result
            finally:
                copied.unlink(missing_ok=True)


def safe_outputs(method):
    """Keep all toolkit writes private until the operation succeeds."""
    signature = inspect.signature(method)
    parameter = 'output_path' if 'output_path' in signature.parameters else 'output_dir'

    @wraps(method)
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        destination = Path(bound.arguments[parameter]).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.homepdf-', dir=destination.parent))
        private = staging / destination.name if parameter == 'output_path' else staging
        bound.arguments[parameter] = private
        published = []
        try:
            result = method(*bound.args, **bound.kwargs)
            def publish(path):
                path = Path(path)
                target = destination if parameter == 'output_path' else destination / path.relative_to(staging)
                actual = publish_file(path, target)
                published.append(actual)
                return actual
            if isinstance(result, Path):
                return publish(result)
            if isinstance(result, list):
                return [publish(path) for path in result]
            # ConversionResult: preserve its public result shape.
            result.outputs = [publish(path) for path in result.outputs]
            return result
        except Exception:
            for path in published:
                path.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return wrapped
