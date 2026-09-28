"""查询执行：只读连接 + 语句分类守卫 + 结果整形。

两道只读防线：
1. ``assert_select_only``：SQL 必须以 SELECT/WITH 开头，且不含分号或写操作关键字；
2. 连接会话设置 ``default_transaction_read_only = on``，
   任何 INSERT/UPDATE/DELETE/DDL 在数据库侧也会被拒绝（纵深防御）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SELECT_PREFIX = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|DROP|ALTER|CREATE|GRANT|"
    r"REVOKE|COMMENT|REINDEX|VACUUM|CLUSTER|REFRESH|COPY|CALL|DO|LOCK|"
    r"REASSIGN|DISCARD|SET\s+ROLE|RESET\s+ROLE)\b",
    re.IGNORECASE,
)


class UnsafeQueryError(Exception):
    pass


def assert_select_only(sql: str) -> None:
    if not SELECT_PREFIX.match(sql):
        raise UnsafeQueryError("只允许执行 SELECT 查询（必须以 SELECT 或 WITH 开头）")
    # 整段查询必须是单条语句（翻译器生成的 SQL 末尾也不带分号）
    if ";" in sql:
        raise UnsafeQueryError("查询中不允许出现分号（禁止多语句）")
    hit = FORBIDDEN.search(sql)
    if hit:
        raise UnsafeQueryError(f"查询包含被禁止的写/结构变更关键字: {hit.group(0).upper()}")


@dataclass
class ResultSet:
    columns: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    row_count: int
    sql: str
    params: list[Any]


def _json_safe(value: Any) -> Any:
    import datetime as dt
    import decimal

    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    return value


def execute(conn, sql: str, params: list[Any], column_meta: list[dict],
            *, statement_timeout_ms: int = 15000) -> ResultSet:
    assert_select_only(sql)  # 第一道：SQL 文本守卫（连接层还有只读事务兜底）
    with conn.cursor() as cur:
        cur.execute(sql, params)
        raw_rows = cur.fetchall()

    keys = [c["key"] for c in column_meta]
    labels = [c["label"] for c in column_meta]
    rows = []
    for raw in raw_rows:
        row = {}
        for key, label, val in zip(keys, labels, raw):
            row[key] = _json_safe(val)
        rows.append(row)
    return ResultSet(
        columns=column_meta,
        rows=rows,
        row_count=len(rows),
        sql=sql,
        params=list(params),
    )
