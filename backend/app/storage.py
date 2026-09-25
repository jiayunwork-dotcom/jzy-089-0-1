"""JSON-file persistence for the active semantic model."""
from __future__ import annotations

import json
import os
import threading

from .config import settings
from .errors import AppError
from .modeling import Model, validate_model
from .sample_model import build_sample_model
from .sandbox import compile_expression

_LOCK = threading.Lock()


def default_model() -> Model:
    return build_sample_model()


def _validate_all_expressions(model: Model) -> None:
    """Save-time sandbox validation of every expression, so bad formulas
    fail when the user saves the model rather than at query time."""
    for c in model.calculated_fields:
        compile_expression(c.expression, c.table, model)
    for h in model.hierarchies:
        for lvl in h.levels:
            if lvl.expression:
                compile_expression(lvl.expression, lvl.table, model)
    for m in model.measures:
        if m.expression:
            compile_expression(m.expression, m.table, model)


class ModelStore:
    def __init__(self, path: str | None = None):
        self.path = path or settings.model_path

    def load(self) -> Model:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return Model.from_json(fh.read())
        except FileNotFoundError:
            model = default_model()
            self.save(model)
            return model

    def save(self, model: Model) -> Model:
        validate_model(model)
        _validate_all_expressions(model)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(model.to_json())
        os.replace(tmp, self.path)
        return model

    def reset(self) -> Model:
        return self.save(default_model())


# Process-wide singleton; API state is intentionally small.
store = ModelStore()
