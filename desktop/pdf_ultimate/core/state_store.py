from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pdf_ultimate.core.paths import app_root


@dataclass(slots=True)
class DocumentViewState:
    page: int = 0
    zoom: float = 1.0
    fit_mode: str = "width"
    view_mode: str = "continuous"


class AppStateStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_root() / "settings.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = {
            "ui": {},
            "documents": {},
        }
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(loaded, dict):
            ui = loaded.get("ui", {})
            docs = loaded.get("documents", {})
            self._data["ui"] = ui if isinstance(ui, dict) else {}
            self._data["documents"] = docs if isinstance(docs, dict) else {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = json.dumps(self._data, indent=2, ensure_ascii=False)
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            # Some Windows environments intermittently deny atomic replace.
            # Fall back to direct write before giving up.
            try:
                self.path.write_text(payload, encoding="utf-8")
            except OSError:
                return

    def get_ui(self, key: str, default: Any = None) -> Any:
        return self._data.get("ui", {}).get(key, default)

    def set_ui(self, key: str, value: Any) -> None:
        ui = self._data.setdefault("ui", {})
        ui[key] = value
        self._save()

    def get_document(self, pdf_path: Path) -> DocumentViewState | None:
        raw = self._data.get("documents", {}).get(str(pdf_path.resolve()))
        if not isinstance(raw, dict):
            return None
        try:
            page = int(raw.get("page", 0))
            zoom = float(raw.get("zoom", 1.0))
            fit_mode = str(raw.get("fit_mode", "width"))
            view_mode = str(raw.get("view_mode", "continuous"))
        except Exception:
            return None
        return DocumentViewState(
            page=max(0, page),
            zoom=max(0.2, min(6.0, zoom)),
            fit_mode=fit_mode,
            view_mode=view_mode,
        )

    def set_document(self, pdf_path: Path, state: DocumentViewState) -> None:
        docs = self._data.setdefault("documents", {})
        docs[str(pdf_path.resolve())] = asdict(state)
        self._save()
