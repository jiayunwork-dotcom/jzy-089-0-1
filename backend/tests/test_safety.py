"""Rule 7: every literal is bound as a parameter — never string-concatenated;
Rule 8: only SELECT is ever produced, writes/DDL are rejected."""
from __future__ import annotations

import re

import pytest

from app.errors import TranslationError
from app.intents import (
    FilterIntent, GroupIntent, QueryIntent, SortIntent,
)
from app.translator import assert_select_only
from app.executor import to_psycopg_sql


def test_string_filter_literal_is_parameterized(tr):
    evil = "VIP'; DROP TABLE orders; --"
    intent = QueryIntent(
        rows=[GroupIntent(field_id="customer_tier")],
        measures=["orders_count"],
        filters=[FilterIntent(field_id="customer_tier", op="eq",
                              values=[evil])],
    )
    t = tr.translate(intent)
    # The literal never appears in SQL text...
    assert evil not in t.sql
    assert "DROP" not in t.sql.upper()
    # ...it is transported only as a bound parameter
    assert t.params == [evil]
    assert re.search(r'=\s*\$1\b', t.sql)
    pg_sql = to_psycopg_sql(t.sql, t.params)
    assert pg_sql.count("%s") == len(t.params)


def test_in_list_and_numeric_range_and_limit_are_parameters(tr):
    intent = QueryIntent(
        rows=[GroupIntent(field_id="product_category")],
        measures=["orders_count"],
        filters=[
            FilterIntent(field_id="product_category", op="in",
                         values=["电子", "家居"]),
            FilterIntent(field_id="order_date", op="gte",
                         values=["2024-01-01"]),
        ],
        limit=50,
    )
    t = tr.translate(intent)
    assert t.params == ["电子", "家居", "2024-01-01", 50]
    assert "$1" in t.sql and "$2" in t.sql and "$3" in t.sql and "$4" in t.sql
    # no quoted literals embedded for these values
    assert "'电子'" not in t.sql


def test_between_date_range_parameterized(tr):
    intent = QueryIntent(
        rows=[GroupIntent(field_id="order_status")],
        measures=["orders_count"],
        filters=[FilterIntent(field_id="order_date", op="between",
                              values=["2024-01-01", "2024-06-30"])],
    )
    t = tr.translate(intent)
    assert t.params == ["2024-01-01", "2024-06-30"]
    assert "BETWEEN $1 AND $2" in t.sql


def test_measure_filter_value_also_parameterized(tr):
    intent = QueryIntent(
        rows=[GroupIntent(field_id="area", hierarchy_level="area")],
        measures=["high_value_sales"],
    )
    t = tr.translate(intent)
    # >= 5000 measure-local filter arrives as a bound parameter
    assert 5000 in t.params
    assert '"orders"."total_amount" >= $1' in t.sql


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "DROP TABLE orders",
    "UPDATE orders SET status='x'",
    "INSERT INTO orders VALUES (1)",
    "ALTER TABLE orders ADD COLUMN x int",
    "TRUNCATE orders",
    "SELECT * FROM orders; DROP TABLE orders",
    "WITH x AS (SELECT 1) SELECT * FROM x; DELETE FROM orders",
    "GRANT ALL ON orders TO app_ro",
])
def test_non_select_statements_rejected(sql):
    with pytest.raises(TranslationError):
        assert_select_only(sql)


def test_select_and_cte_allowed(tr):
    assert_select_only("SELECT 1")
    intent = QueryIntent(
        rows=[GroupIntent(field_id="order_status")],
        measures=["orders_count"],
    )
    t = tr.translate(intent)
    assert t.sql.lstrip().upper().startswith(("SELECT", "WITH"))
    assert ";" not in t.sql.rstrip(";")  # no statement separators
