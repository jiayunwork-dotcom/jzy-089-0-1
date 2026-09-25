"""Query routes: translate drag intent to SQL, execute, drill, distinct."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..config import settings
from ..executor import execute, to_psycopg_sql
from ..intents import DrillRequest, QueryIntent
from ..translator import Translator

router = APIRouter(prefix="/api", tags=["query"])


def _translator(request: Request) -> Translator:
    return Translator(request.app.state.model)


@router.post("/query/translate")
def translate(request: Request, intent: QueryIntent) -> dict:
    tr = _translator(request)
    result = tr.translate(intent)
    return {
        "sql": result.sql,
        "postgresSql": to_psycopg_sql(result.sql, result.params),
        "params": result.params,
        "columns": result.columns,
        "plan": result.plan,
    }


@router.post("/query")
def run_query(request: Request, intent: QueryIntent) -> dict:
    tr = _translator(request)
    result = tr.translate(intent)
    data = execute(result.sql, result.params, max_rows=settings.max_rows)
    return {
        "columns": result.columns,
        "rows": data["rows"],
        "truncated": data["truncated"],
        "sql": result.sql,
        "postgresSql": to_psycopg_sql(result.sql, result.params),
        "params": result.params,
        "plan": result.plan,
    }


@router.post("/query/drill")
def drill(request: Request, req: DrillRequest) -> dict:
    """Return the next-level intent and its translated SQL, with every
    filter/measure carried over unchanged."""
    tr = _translator(request)
    new_intent = tr.apply_drill(req)
    result = tr.translate(new_intent)
    return {
        "intent": new_intent.model_dump(),
        "sql": result.sql,
        "postgresSql": to_psycopg_sql(result.sql, result.params),
        "params": result.params,
        "columns": result.columns,
        "plan": result.plan,
    }


@router.post("/distinct")
def distinct_values(request: Request, payload: dict) -> dict:
    """Distinct values for filter dropdowns. Also parameterised/readonly."""
    model = request.app.state.model
    field_id = payload.get("field_id")
    limit = min(int(payload.get("limit", 500)), 2000)
    obj = model.field(field_id)

    params: list = []
    if obj.__class__.__name__ == "CalculatedField":
        from ..sandbox import compile_expression

        compiled = compile_expression(obj.expression, obj.table, model)
        expr = compiled.render(obj.table, params)
        table = model.table(obj.table)
        limit_ph = f"${len(params) + 1}"
        sql = (
            f"SELECT DISTINCT {expr} AS v FROM "
            f'"{table.db_name}" "{obj.table}" '
            f"WHERE {expr} IS NOT NULL ORDER BY 1 LIMIT {limit_ph}"
        )
    else:
        table = model.table(obj.table)
        sql = (
            f'SELECT DISTINCT "{obj.table}"."{obj.column}" AS v FROM '
            f'"{table.db_name}" "{obj.table}" WHERE '
            f'"{obj.table}"."{obj.column}" IS NOT NULL ORDER BY 1 LIMIT $1'
        )
    params.append(limit)

    data = execute(sql, params)
    return {"values": [row[0] for row in data["rows"]]}
