"""HTTP 路由层。

路由只负责：取模型 -> 转换入参 -> 调服务/内核 -> 映射错误。
SQL 生成、建模、执行均在各自模块中。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..kernel import PlanningError, QuerySpec, drill_down
from ..kernel.schema import Model
from ..kernel.sandbox import ExpressionError
from ..query_executor import UnsafeQueryError
from .. import service
from ..api_schemas import DrillIn, ExpressionIn, QueryIn

router = APIRouter(prefix="/api")


def _store(request: Request):
    return request.app.state.store


def _model(request: Request) -> Model:
    return _store(request).load()


def _spec(payload: QueryIn) -> QuerySpec:
    try:
        return QuerySpec.from_dict(payload.model_dump())
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"查询规格非法: {e}")


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/model")
def get_model(request: Request):
    return service.model_to_json(_model(request))


@router.put("/model")
def put_model(request: Request, raw: dict):
    try:
        model = service.model_from_json(raw)
    except service.ServiceError as e:
        raise HTTPException(status_code=400, detail=e.args[0])
    except Exception as e:  # 结构校验错误
        raise HTTPException(status_code=400, detail=str(e))
    _store(request).save(model)
    return {"ok": True}


@router.post("/model/reset-sample")
def reset_sample(request: Request):
    from ..sample_model import build_sample_model

    model = build_sample_model()
    _store(request).save(model)
    return {"ok": True}


@router.post("/validate-expression")
def validate_expression(request: Request, payload: ExpressionIn):
    model = _model(request)
    try:
        return service.validate_saved_expression(
            model, payload.table, payload.expression,
            expect_boolean=payload.expect_boolean,
        )
    except service.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/meta/fields")
def meta_fields(request: Request):
    model = _model(request)
    return {
        "tables": [
            {"id": t.id, "name": t.name, "physical_name": t.physical_name,
             "columns": [{"name": c.name, "data_type": c.data_type.value,
                          "primary_key": c.primary_key} for c in t.columns]}
            for t in model.tables.values()
        ],
        "joins": [
            {"id": j.id, "left_table": j.left_table, "right_table": j.right_table,
             "keys": [{"left_column": k.left_column, "right_column": k.right_column}
                      for k in j.keys],
             "cardinality": j.cardinality.value}
            for j in model.joins.values()
        ],
        "dimensions": [
            {"id": d.id, "name": d.name, "table": d.table,
             "hierarchy_id": d.hierarchy_id, "data_type": d.data_type.value}
            for d in model.dimensions.values()
        ],
        "hierarchies": [
            {"id": h.id, "name": h.name, "levels": h.levels}
            for h in model.hierarchies.values()
        ],
        "measures": [
            {"id": m.id, "name": m.name, "table": m.table, "agg": m.agg.value}
            for m in model.measures.values()
        ],
        "calculated_fields": [
            {"id": c.id, "name": c.name, "table": c.table,
             "data_type": c.data_type.value}
            for c in model.calculated_fields.values()
        ],
    }


@router.get("/meta/enum")
def meta_enum(request: Request, field_id: str, limit: int = 500):
    model = _model(request)
    try:
        with request.app.state.db() as conn:
            return service.enum_values(model, conn, field_id, limit=limit)
    except service.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/translate")
def translate_query(request: Request, payload: QueryIn):
    model = _model(request)
    spec = _spec(payload)
    try:
        return service.translate_only(model, spec, request.app.state.settings)
    except PlanningError as e:
        raise HTTPException(status_code=400, detail=f"查询规划失败: {e}")
    except ExpressionError as e:
        raise JSONResponse(status_code=400, content={"detail": e.to_dict()})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/query")
def run_query(request: Request, payload: QueryIn):
    model = _model(request)
    spec = _spec(payload)
    try:
        with request.app.state.db() as conn:
            return service.run_query(model, spec, conn, request.app.state.settings)
    except PlanningError as e:
        raise HTTPException(status_code=400, detail=f"查询规划失败: {e}")
    except UnsafeQueryError as e:
        raise HTTPException(status_code=403, detail=f"只读保护: {e}")
    except ExpressionError as e:
        return JSONResponse(status_code=400, content={"detail": e.to_dict()})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/drill")
def drill(request: Request, payload: DrillIn):
    model = _model(request)
    spec = _spec(payload.spec)
    try:
        new_spec = drill_down(model, spec, payload.dimension_id)
    except PlanningError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with request.app.state.db() as conn:
        return service.run_query(model, new_spec, conn, request.app.state.settings)


@router.post("/sample/reset-db")
def reset_db(request: Request):
    """重建样例库并灌入确定性数据（需要管理员连接，仅开发/演示用）。"""
    from ..seed.seed_data import seed
    import pathlib

    schema = (pathlib.Path(__file__).resolve().parents[2] / "seed" / "schema.sql")
    with request.app.state.admin_db() as conn:
        conn.execute(schema.read_text(encoding="utf-8"))
        seed(conn)
    return {"ok": True}
