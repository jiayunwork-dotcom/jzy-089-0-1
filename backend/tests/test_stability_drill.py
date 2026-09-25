"""Rule 4: stable SQL for identical drag state; Rule 6: drill keeps
filters and measures, only changes group grain."""
from __future__ import annotations

import pytest

from app.intents import DrillRequest, FilterIntent, GroupIntent, QueryIntent


def test_stable_sql_for_identical_drag(tr):
    intent = QueryIntent(
        rows=[GroupIntent(field_id="region_name", hierarchy_level="region_name")],
        measures=["sales_amount", "orders_count"],
        filters=[FilterIntent(field_id="customer_tier", op="eq", values=["VIP"])],
    )
    first = tr.translate(intent)
    for _ in range(5):
        again = tr.translate(intent.model_copy(deep=True))
        assert again.sql == first.sql
        assert again.params == first.params


def test_field_order_is_significant(tr):
    """Different drop order => different output ordinal order (explicitly
    documented); identical order => identical SQL."""
    a = QueryIntent(
        rows=[GroupIntent(field_id="area", hierarchy_level="area")],
        measures=["sales_amount", "payment_amount"],
    )
    b = QueryIntent(
        rows=[GroupIntent(field_id="area", hierarchy_level="area")],
        measures=["payment_amount", "sales_amount"],
    )
    assert tr.translate(a).sql == tr.translate(a.model_copy(deep=True)).sql
    assert tr.translate(a).sql != tr.translate(b).sql


def test_drill_down_changes_only_group_grain(tr):
    intent = QueryIntent(
        rows=[GroupIntent(field_id="order_year", hierarchy_level="order_year")],
        measures=["sales_amount"],
        filters=[FilterIntent(field_id="order_status", op="eq",
                              values=["completed"])],
    )
    before = tr.translate(intent)
    new_intent = tr.apply_drill(
        DrillRequest(intent=intent, hierarchy_id="order_date_h",
                     direction="down")
    )
    after = tr.translate(new_intent)

    assert new_intent.rows[0].field_id == "order_quarter"
    assert new_intent.measures == ["sales_amount"]
    assert new_intent.filters[0].values == ["completed"]

    # Group expression changed (year -> quarter), everything else identical:
    assert "EXTRACT(YEAR" in before.sql
    assert "EXTRACT(QUARTER" in after.sql
    assert "EXTRACT(YEAR" not in after.sql
    # filters and measure aggregate are preserved
    assert after.sql.count("status") == before.sql.count("status")
    assert "SUM(\"order_lines\".\"line_amount\")" in after.sql
    # param list unchanged in value and order
    assert after.params == before.params == ["completed"]


def test_drill_all_the_way_then_up(tr):
    intent = QueryIntent(
        rows=[GroupIntent(field_id="order_year", hierarchy_level="order_year")],
        measures=["orders_count"],
    )
    req = DrillRequest(intent=intent, hierarchy_id="order_date_h")
    levels = ["order_year"]
    for expected in ("order_quarter", "order_month", "order_day"):
        req.intent = tr.apply_drill(req)
        levels.append(req.intent.rows[0].field_id)
        assert req.intent.rows[0].field_id == expected
    # already at finest -> rejected
    with pytest.raises(Exception):
        tr.apply_drill(req)
    req.direction = "up"
    up = tr.apply_drill(req)
    assert up.rows[0].field_id == "order_month"
    assert levels == ["order_year", "order_quarter", "order_month", "order_day"]
