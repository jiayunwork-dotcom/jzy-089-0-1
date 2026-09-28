"""业务服务层：模型编辑校验、表达式沙箱校验、翻译/执行编排。

路由层只处理 HTTP；所有"拖拽意图 -> SQL/结果"的逻辑都在这里或内核中。
"""
from __future__ import annotations

from typing import Any

from .kernel import sandbox
from .kernel.query import QuerySpec
from .kernel.renderer import translate
from .kernel.schema import Cardinality, Model
from .query_executor import ResultSet, execute


class ServiceError(Exception):
    pass


# ---------------------------------------------------------------------------
# 字段环境（表达式沙箱可用的标识符 -> 类型）
# ---------------------------------------------------------------------------

def table_field_types(model: Model, table_id: str) -> dict[str, str]:
    """某张表上的表达式可见字段：本表列 + 本表计算字段 + 多对一维表的列。

    多对一维表列允许直接裸用（星型模型的常见诉求，如行金额引用 unit_price）；
    若列名在多张可见表中重名则拒绝裸用，必须新建计算字段包装。
    """
    fields: dict[str, str] = {}
    for col in model.tables[table_id].columns:
        fields[col.name] = col.data_type.value
    for calc in model.calculated_fields.values():
        if calc.table == table_id:
            fields[calc.id] = calc.data_type.value
    # 多对一维表（从本表的"多"侧走向"一"侧，不放大）
    for join, other in model.neighbors(table_id):
        if (
            join.cardinality is Cardinality.ONE_TO_MANY
            and join.right_table == table_id
            and join.left_table == other
        ):
            for col in model.tables[other].columns:
                if col.name in fields and fields[col.name] != col.data_type.value:
                    raise ServiceError(
                        f"字段 {col.name} 在多张关联表中重名且类型不同，"
                        f"请在计算字段中显式处理"
                    )
                fields.setdefault(col.name, col.data_type.value)
    return fields


def validate_saved_expression(
    model: Model, table_id: str, expression: str, *, expect_boolean: bool = False
) -> dict:
    """保存时校验表达式，返回 {ok,data_type,error}。"""
    if table_id not in model.tables:
        raise ServiceError(f"未知表: {table_id}")
    fields = table_field_types(model, table_id)
    try:
        result = sandbox.validate_expression(
            expression, fields, expect_boolean=expect_boolean
        )
    except sandbox.ExpressionError as e:
        return {"ok": False, "error": e.to_dict()}
    return {"ok": True, "data_type": result.data_type}


# ---------------------------------------------------------------------------
# 模型编辑（全部先结构校验 + 表达式校验，再落盘）
# ---------------------------------------------------------------------------

def validate_model_expressions(model: Model) -> None:
    """对模型中所有表达式做保存期校验，错误带字段归属信息抛出。"""
    errors: list[dict] = []
    for d in model.dimensions.values():
        if d.expression:
            r = validate_saved_expression(model, d.table, d.expression)
            if not r["ok"]:
                errors.append({"owner": f"dimension:{d.id}", **r["error"]})
    for m in model.measures.values():
        fields = table_field_types(model, m.table)
        if m.expression:
            try:
                sandbox.validate_expression(m.expression, fields)
            except sandbox.ExpressionError as e:
                errors.append({"owner": f"measure:{m.id}", **e.to_dict()})
        if m.filter_expression:
            try:
                sandbox.validate_expression(m.filter_expression, fields,
                                           expect_boolean=True)
            except sandbox.ExpressionError as e:
                errors.append({"owner": f"measure:{m.id}.filter", **e.to_dict()})
    for c in model.calculated_fields.values():
        r = validate_saved_expression(model, c.table, c.expression)
        if not r["ok"]:
            errors.append({"owner": f"calculated_field:{c.id}", **r["error"]})
    if errors:
        raise ServiceError({"message": "表达式校验失败", "errors": errors})


# ---------------------------------------------------------------------------
# 查询编排
# ---------------------------------------------------------------------------

def translate_only(model: Model, spec: QuerySpec, settings) -> dict:
    result = translate(
        model, spec,
        default_limit=settings.default_limit,
        max_limit=settings.max_limit,
    )
    return {
        "sql": result.sql,
        "params": _json_params(result.params),
        "columns": result.result_columns,
    }


def run_query(model: Model, spec: QuerySpec, conn, settings) -> dict:
    result = translate(
        model, spec,
        default_limit=settings.default_limit,
        max_limit=settings.max_limit,
    )
    rs: ResultSet = execute(
        conn, result.sql, result.params, result.result_columns,
        statement_timeout_ms=settings.statement_timeout_ms,
    )
    return {
        "columns": rs.columns,
        "rows": rs.rows,
        "row_count": rs.row_count,
        "sql": rs.sql,
        "params": _json_params(rs.params),
    }


def enum_values(model: Model, conn, field_id: str, limit: int = 500) -> dict:
    """筛选区下拉枚举：对维度/计算字段 SELECT DISTINCT。"""
    field = _lookup_field(model, field_id)
    table = model.tables[field.table]
    bag_params: list[Any] = []

    def alloc(v: Any, t: str) -> str:
        bag_params.append(v)
        return "%s"

    if field.expression:
        expr_sql = sandbox.compile_expression(
            field.expression,
            lambda name: f'"t0"."{name}"' if table.has(name) else _resolve_dim_col(model, name),
            alloc,
        )
    else:
        expr_sql = f'"t0"."{field.column}"'
    sql = (
        f'SELECT DISTINCT {expr_sql} AS v FROM "{table.physical_name}" AS "t0" '
        f'WHERE {expr_sql} IS NOT NULL ORDER BY {expr_sql} LIMIT {int(limit)}'
    )
    with conn.cursor() as cur:
        cur.execute(sql, bag_params)
        values = [_json(row[0]) for row in cur.fetchall()]
    return {"field_id": field_id, "values": values}


def _resolve_dim_col(model: Model, name: str) -> str:
    # 供 enum_values 中引用多对一维表的极简回退（枚举通常直接用列维度）
    owners = [t.id for t in model.tables.values() if t.has(name)]
    if len(owners) == 1:
        return f'"t0"."{name}"'
    raise ServiceError(f"枚举字段表达式中存在歧义列: {name}")


def _json(v: Any) -> Any:
    import datetime as dt
    import decimal

    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    return v


def _json_params(params: list[Any]) -> list[Any]:
    return [_json(p) if not isinstance(p, list) else [_json(x) for x in p]
            for p in params]


def _lookup_field(model: Model, field_id: str):
    if field_id in model.dimensions:
        return model.dimensions[field_id]
    if field_id in model.calculated_fields:
        return model.calculated_fields[field_id]
    raise ServiceError(f"未知字段: {field_id}")


# ---------------------------------------------------------------------------
# 模型序列化（给 /model 接口）
# ---------------------------------------------------------------------------

def model_to_json(model: Model) -> dict:
    raw = model.to_dict()
    # 附带层级下钻顺序等派生信息
    return raw


def model_from_json(raw: dict) -> Model:
    model = Model.from_dict(raw)
    model.validate()
    validate_model_expressions(model)
    return model
