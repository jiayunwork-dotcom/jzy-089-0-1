"""Rules 1-4: join derivation, coverage, no duplicate joins, single-table."""
from __future__ import annotations

import re

import pytest

from expected_queries import EXPECTED_QUERIES
from app.intents import GroupIntent, QueryIntent
from app.translator import plan_paths


def _block(t, name):
    return next(b for b in t.plan["blocks"] if b["name"] == name)


def test_single_table_query_emits_no_join(tr):
    """Rule: a query touching one table must not produce ANY join."""
    intent = QueryIntent(
        rows=[GroupIntent(field_id="order_status")],
        measures=["orders_count"],
    )
    t = tr.translate(intent)
    assert " JOIN " not in t.sql.upper()
    assert t.plan["blocks"][0]["tables"] == ["orders"]
    assert t.plan["blocks"][0]["joins"] == []


def test_cross_table_path_covers_all_tables_once(tr):
    """Rule: the join path covers every referenced table and never joins a
    table twice within a query block."""
    case = EXPECTED_QUERIES["sales_by_region"]
    t = tr.translate(case["intent"])
    block = _block(t, "m_sales_amount")
    expected_tables = case["expect"]["referenced_tables"]
    assert set(block["tables"]) == expected_tables
    # no table appears twice
    assert len(block["tables"]) == len(set(block["tables"]))
    # each declared join used at most once
    assert len(block["joins"]) == len(set(block["joins"]))
    assert set(block["joins"]) == case["expect"]["joins"]
    # one physical LEFT JOIN line per connected table (anchor has none)
    physical_joins = re.findall(r"LEFT JOIN", t.sql)
    assert len(physical_joins) == len(block["joins"]) + 1  # +1 assembly join
    # no physical table name is joined twice in the same CTE block
    cte = t.sql.split("m_sales_amount AS (")[1].split(")")[0]
    joined = re.findall(r'LEFT JOIN "([a-z_]+)"', cte)
    assert len(joined) == len(set(joined))


def test_plan_rejects_grouping_through_fanout_edge(model, tr):
    """Grouping along a one-to-many fan-out edge is ambiguous and rejected
    rather than returning inflated numbers."""
    # payment_method lives on payments (many side of payments 1:N orders);
    # sales_amount is anchored on order_lines; there is no fan-out-safe path
    # from the fact anchor to payments for grouping.
    intent = QueryIntent(
        rows=[GroupIntent(field_id="payment_method")],
        measures=["sales_amount"],
    )
    with pytest.raises(Exception):  # noqa: B017 - TranslationError
        tr.translate(intent)


def test_plan_bfs_directly():
    from app.sample_model import build_sample_model
    from app.modeling import validate_model, Cardinality

    model = validate_model(build_sample_model())
    # anchor order_lines -> orders -> customers -> regions and -> products
    plan = plan_paths(
        model,
        "order_lines",
        {"regions", "products", "orders", "customers"},
        fanout_safe=True,
    )
    assert set(plan.tables) == {
        "order_lines", "orders", "customers", "regions", "products"
    }
    # traversing orders -> order_lines from the orders anchor IS a fanout
    plan2 = plan_paths(model, "orders", {"order_lines"}, fanout_safe=True)
    assert "order_lines" not in plan2.steps


def test_many_to_many_edges_are_never_fanout_safe(model, tr):
    from app.modeling import (
        Cardinality, Column, Join, Table, validate_model,
    )
    m = model.model_copy(deep=True)
    m.tables.append(Table(id="tags", db_name="tags", label="tags",
                          columns=[Column(name="tag", type="string"),
                                   Column(name="order_id", type="integer")]))
    m.joins.append(Join(
        id="orders_tags", left_table="orders", left_column="order_id",
        right_table="tags", right_column="order_id",
        cardinality=Cardinality.many_to_many,
    ))
    plan = plan_paths(validate_model(m), "orders", {"tags"}, fanout_safe=True)
    assert "tags" not in plan.steps
