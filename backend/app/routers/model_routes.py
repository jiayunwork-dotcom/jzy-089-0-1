"""HTTP routes for semantic modeling (tables / joins / dimensions /
hierarchies / measures / calculated fields)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..modeling import Model
from ..sandbox import validate_expression

router = APIRouter(prefix="/api/model", tags=["model"])


def _store(request: Request):
    return request.app.state.store


@router.get("")
def get_model(request: Request) -> dict:
    return request.app.state.model.model_dump()


@router.put("")
def put_model(request: Request, model: Model) -> dict:
    # validate_model + the expression sandbox both run inside save();
    # expression errors carry source positions back to the editor.
    saved = _store(request).save(model)
    request.app.state.model = saved
    return saved.model_dump()


@router.post("/reset")
def reset_model(request: Request) -> dict:
    saved = _store(request).reset()
    request.app.state.model = saved
    return saved.model_dump()


@router.post("/validate-expression")
def post_validate_expression(request: Request, payload: dict) -> dict:
    source = payload.get("expression", "")
    table_id = payload.get("table_id")
    if not table_id:
        from ..errors import AppError

        raise AppError("table_id is required")
    return validate_expression(source, table_id, request.app.state.model)
