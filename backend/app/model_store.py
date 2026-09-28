"""模型 JSON 文件的持久化（简单可靠：整文件读改写 + 临时文件原子替换）。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from .kernel.schema import Model


class ModelStore:
    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(_empty(), ensure_ascii=False, indent=2),
                                 encoding="utf-8")

    def load(self) -> Model:
        with self._lock, self.path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        model = Model.from_dict(raw)
        model.validate()
        return model

    def save(self, model: Model) -> None:
        model.validate()
        with self._lock:
            data = json.dumps(model.to_dict(), ensure_ascii=False, indent=2)
            fd, tmp = tempfile.mkstemp(prefix=".model-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(data)
                os.replace(tmp, self.path)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    def reset_to(self, model: Model) -> None:
        self.save(model)


def _empty() -> dict:
    return {
        "tables": [], "joins": [], "dimensions": [], "hierarchies": [],
        "measures": [], "calculated_fields": [],
    }
