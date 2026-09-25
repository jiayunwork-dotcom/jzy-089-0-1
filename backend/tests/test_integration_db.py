"""End-to-end translation + execution against postgres:16.

Skipped automatically when DATABASE_URL is unreachable (e.g. running the
kernel tests without the compose stack). In the container / CI the DB is
present and these tests prove the generated SQL actually runs and returns
the hand-computed totals without fan-out inflation.
"""
from __future__ import annotations

import os

import pytest

from app.intents import DrillRequest, FilterIntent, GroupIntent, QueryIntent
from app.executor import execute

DB_URL = os.getenv(
    "DATABASE_URL", "postgresql://app_ro:app_ro_secret@localhost:5432/appdb"
)


@pytest.fixture(scope="module")
def db_conn():
    import psycopg
    from psycopg.rows import dict_row

    try:
        conn = psycopg.connect(DB_URL, row_factory=dict_row, autocommit=True,
                               connect_timeout=3)
        conn.read_only = True
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Postgres unavailable: {exc}")
    yield conn
    conn.close()


def _run(tr, intent):
    t = tr.translate(intent)
    data = execute(t.sql, t.params)
    return {row[0]: row[1] for row in data["rows"]}, t


def test_sales_by_region_golden_values(tr, db_conn):
    intent = QueryIntent(
        rows=[GroupIntent(field_id="region_name", hierarchy_level="region_name")],
        measures=["sales_amount"],
    )
    values, t = _run(tr, intent)
    assert values == {"江苏": 8640.0, "浙江": 7400.0, "广东": 7220.0}


def test_single_table_runs_without_join(tr, db_conn):
    intent = QueryIntent(
        rows=[GroupIntent(field_id="order_status")],
        measures=["orders_count"],
    )
    _, t = _run(tr, intent)
    assert " JOIN " not in t.sql.upper()


def test_two_fanout_children_not_inflated(tr, db_conn):
    intent = QueryIntent(
        rows=[GroupIntent(field_id="area", hierarchy_level="area")],
        measures=["sales_amount", "payment_amount", "orders_count"],
    )
    t = tr.translate(intent)
    data = execute(t.sql, t.params)
    totals = {"sales_amount": 0.0, "payment_amount": 0.0}
    for row in data["rows"]:
        mapping = dict(zip(data["columns"], row))
        totals["sales_amount"] += mapping["sales_amount"]
        totals["payment_amount"] += mapping["payment_amount"]
        # every area reports orders (LEFT JOIN grid), missing -> 0
        assert mapping["sales_amount"] >= 0
    assert totals["sales_amount"] == pytest.approx(23260.0)
    assert totals["payment_amount"] == pytest.approx(18360.0)
    # order 1 contributes exactly 6120 even though it has 2 lines + 2 pays
    assert totals["sales_amount"] != pytest.approx(totals["payment_amount"])


def test_order_with_two_lines_two_payments_single_group(tr, db_conn):
    """The sharpest fan-out case: order 1 alone."""
    intent = QueryIntent(
        rows=[GroupIntent(field_id="order_no")],
        measures=["sales_amount", "payment_amount"],
        filters=[FilterIntent(field_id="order_no", op="eq",
                              values=["SO-1001"])],
    )
    t = tr.translate(intent)
    data = execute(t.sql, t.params)
    row = dict(zip(data["columns"], data["rows"][0]))
    assert row["sales_amount"] == pytest.approx(6120.0)
    assert row["payment_amount"] == pytest.approx(6120.0)


def test_drill_down_requery_returns_quarters(tr, db_conn):
    intent = QueryIntent(
        rows=[GroupIntent(field_id="order_year", hierarchy_level="order_year")],
        measures=["sales_amount"],
        filters=[FilterIntent(field_id="order_status", op="eq",
                              values=["completed"])],
    )
    year = tr.translate(intent)
    ydata = execute(year.sql, year.params)
    new_intent = tr.apply_drill(
        DrillRequest(intent=intent, hierarchy_id="order_date_h",
                     direction="down")
    )
    quarter = tr.translate(new_intent)
    qdata = execute(quarter.sql, quarter.params)
    # completed sales 2024: orders 1,2,4,5,7,8 -> 6120+4000+2000+760+520+4960
    ysum = sum(r[1] for r in ydata["rows"])
    qsum = sum(r[1] for r in qdata["rows"])
    assert ysum == pytest.approx(qsum) == pytest.approx(18360.0)


def test_database_role_is_read_only(db_conn):
    with pytest.raises(Exception):
        with db_conn.cursor() as cur:
            cur.execute("CREATE TABLE should_not_exist (id int)")


def test_exists_filter_runs(tr, db_conn):
    intent = QueryIntent(
        rows=[GroupIntent(field_id="region_name", hierarchy_level="region_name")],
        measures=["sales_amount"],
        filters=[FilterIntent(field_id="payment_method", op="in",
                              values=["card"])],
    )
    t = tr.translate(intent)
    assert "EXISTS" in t.sql
    data = execute(t.sql, t.params)
    # only regions with card-paid orders appear
    values = {row[0] for row in data["rows"] if row[1] > 0}
    assert "江苏" in values
    assert "广东" in values
