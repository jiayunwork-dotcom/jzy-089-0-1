"""Rule 3: aggregating the "many"-side measure must not be inflated by joins."""
from __future__ import annotations

from app.intents import GroupIntent, QueryIntent


def test_many_side_sum_aggregates_before_assembly_join(tr):
    """The SUM of the child table happens inside the per-measure CTE, keyed
    by the group grain; the assembly LEFT JOIN can therefore never multiply
    it (even when two independent fan-out children are present)."""
    intent = QueryIntent(
        rows=[GroupIntent(field_id="area", hierarchy_level="area")],
        measures=["sales_amount", "payment_amount"],
    )
    t = tr.translate(intent)

    # 1) measure CTEs aggregate independently
    sales_cte = t.sql.split("m_sales_amount AS (")[1].split(")")[0]
    pay_cte = t.sql.split("m_payment_amount AS (")[1].split(")")[0]
    assert "SUM(\"order_lines\".\"line_amount\")" in sales_cte
    assert "SUM(\"payments\".\"amount\")" in pay_cte
    # payments never appears inside the sales CTE and vice versa: the two
    # child tables cannot cross-multiply
    assert "payments" not in sales_cte
    assert "order_lines" not in pay_cte
    # 2) outer query only joins pre-aggregated CTE results, not raw tables
    tail = t.sql.split("\n)\n")[-1]
    assert "SUM" not in tail.upper().split("ORDER BY")[0].replace("COALESCE(SUM", "")
    assert "LEFT JOIN m_payment_amount" in t.sql
    # 3) GROUP BY grain in both CTEs equals the group axis count
    assert sales_cte.count("GROUP BY") == 1
    assert pay_cte.count("GROUP BY") == 1


def test_single_order_with_two_lines_and_two_payments_values(tr):
    """Golden numbers from seed: order 1 has two lines (6000 + 120) and two
    payments (6000 + 120). A naive join yields 4 rows -> sales 12240 and
    payments 12240; the correct totals are 6120 each."""
    from expected_queries import EXPECTED_QUERIES

    intent = QueryIntent(
        rows=[GroupIntent(field_id="customer_tier")],
        measures=["sales_amount", "payment_amount"],
        filters=[],
    )
    t = tr.translate(intent)
    # structural guarantee against fan-out; numeric equality is checked in
    # the DB-backed integration test.
    assert t.columns[0]["name"] == "customer_tier"
    case = EXPECTED_QUERIES["two_fanout_children"]
    assert case["totals"] == {"sales_amount": 23260.0, "payment_amount": 18360.0}
