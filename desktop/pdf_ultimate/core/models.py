from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class PdfInfo:
    path: Path
    page_count: int
    encrypted: bool
    title: str = ""
    author: str = ""
    subject: str = ""
    creator: str = ""
    producer: str = ""
    permissions: dict[str, bool] = field(default_factory=dict)


@dataclass(slots=True)
class ConversionResult:
    target: str
    outputs: list[Path]
