"""Expected sample queries: hand-authored drag intents with the
properties the generated SQL must satisfy. Used by the test-suite and
shipped as documentation of the drag->SQL mapping."""
from __future__ import annotations

from app.intents import FilterIntent, GroupIntent, QueryIntent, SortIntent

EXPECTED_QUERIES: dict[str, dict] = {
    "sales_by_region": {
        "description": "按地区汇总销售额(跨3表: 明细->订单->客户->地区)",
        "intent": QueryIntent(
            rows=[GroupIntent(field_id="region_name",
                              hierarchy_level="region_name")],
            measures=["sales_amount"],
        ),
        "expect": {
            "referenced_tables": {"order_lines", "orders", "customers", "regions"},
            "joins": {"order_lines_order", "orders_customer", "customers_region"},
        },
        # hand-computed from db/init/01_schema_seed.sql
        "expected_rows": {
            "江苏": 8640.0, "浙江": 7400.0, "广东": 7220.0,
        },
    },
    "single_table_orders": {
        "description": "单表查询(仅订单状态分组)不应产生任何 JOIN",
        "intent": QueryIntent(
            rows=[GroupIntent(field_id="order_status")],
            measures=["orders_count"],
        ),
        "expect": {
            "referenced_tables": {"orders"},
            "joins": set(),
        },
    },
    "sales_with_tier_filter": {
        "description": "大区过滤走安全路径; 支付方式过滤与销售事实无安全路径时走 EXISTS",
        "intent": QueryIntent(
            rows=[GroupIntent(field_id="region_name",
                              hierarchy_level="region_name")],
            measures=["sales_amount"],
            filters=[
                FilterIntent(field_id="customer_tier", op="eq", values=["VIP"]),
                FilterIntent(field_id="payment_method", op="in",
                             values=["card"]),
            ],
        ),
        "expect": {"uses_exists": True},
    },
    "two_fanout_children": {
        "description": "销售额(明细子表)与支付金额(支付子表)同时出数, 不得交叉放大",
        "intent": QueryIntent(
            rows=[GroupIntent(field_id="area", hierarchy_level="area")],
            measures=["sales_amount", "payment_amount"],
        ),
        "expect": {"measure_ctes": ["m_sales_amount", "m_payment_amount"]},
        # 样例数据手工合计: 销售额 23260, 支付金额 18360
        "totals": {"sales_amount": 23260.0, "payment_amount": 18360.0},
    },
    "sales_by_year_drill": {
        "description": "年->季度->月->日 钻取; 过滤与度量保持不变",
        "intent": QueryIntent(
            rows=[GroupIntent(field_id="order_year",
                              hierarchy_level="order_year")],
            measures=["sales_amount"],
            filters=[FilterIntent(field_id="order_status", op="eq",
                                  values=["completed"])],
        ),
        "drill": {"hierarchy_id": "order_date_h", "direction": "down"},
        "drill_expect_level": "order_quarter",
    },
}
