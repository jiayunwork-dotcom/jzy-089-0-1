"""Query execution and result shaping.

The translator emits ``$n`` placeholders + an ordered parameter list; this
module converts them to psycopg's ``%s`` positional placeholders and runs
the statement through a read-only connection. Nothing here interpolates
literal values into SQL text.
"""
from __future__ import annotations

import re
from decimal import Decimal
from datetime import date, datetime
from typing import Any

from .db import get_connection
from .errors import TranslationError, UnsafeSqlError
from .translator import assert_select_only

_PLACEHOLDER = re.compile(r"\$(\d+)")


def to_psycopg_sql(sql: str, params: list) -> str:
    def repl(m: re.Match) -> str:
        idx = int(m.group(1))
        if idx < 1 or idx > len(params):
            raise TranslationError(
                f"SQL placeholder ${idx} has no matching parameter"
            )
        return "%s"

    return _PLACEHOLDER.sub(repl, sql)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        # Keep precision for money-like values while remaining JSON-native.
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def execute(sql: str, params: list, max_rows: int | None = None) -> dict:
    assert_select_only(sql)
    pg_sql = to_psycopg_sql(sql, params)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(pg_sql, params)
            if cur.description is None:
                raise UnsafeSqlError("Query returned no result set; rejected")
            columns = [d.name for d in cur.description]
            rows = cur.fetchmany(max_rows + 1) if max_rows else cur.fetchall()
    except Exception as exc:  # surface database errors uniformly as 400
        raise TranslationError(f"Database error: {exc}") from exc

    truncated = False
    if max_rows and len(rows) > max_rows:
        rows = rows[:max_rows]
        truncated = True
    data = [[_jsonable(v) for v in row.values()] for row in rows]
    return {"columns": columns, "rows": data, "truncated": truncated}
