"""规则 2：跨表连接路径覆盖所有被引用的表，且不重复连接同一张表。"""
from __future__ import annotations

import re
import unittest

from app.kernel.planner import build_plan
from tests._helpers import gen, spec

JOIN_RE = re.compile(
    r"\bJOIN\s+\"?(\w+)\"?\s+AS\s+\"?(t\d+|sub_m\d+_t\d+|ex\d+)\"?",
    re.IGNORECASE,
)


def joined_aliases(sql: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in JOIN_RE.finditer(sql)]


def referenced_physical_tables(sql: str) -> set[str]:
    """FROM/JOIN 中出现过的所有物理表名（含子查询根表的 FROM）。"""
    return set(re.findall(
        r"(?:\bJOIN|FROM)\s+\"?(\w+)\"?\s+AS\s+\"?(?:t\d+|sub_m\d+_t\d+|ex\d+)\"?",
        sql, flags=re.IGNORECASE,
    ))


class JoinPathTest(unittest.TestCase):
    def test_covers_all_referenced_tables_region(self):
        model, r = gen({"rows": ["region"], "values": [{"measure_id": "sales"}]})
        aliases = joined_aliases(r.sql)
        physical = {t.physical_name: tid for tid, t in model.tables.items()}
        joined_tables = {physical[phys] for phys, _ in aliases}
        # order_items -> orders -> customers -> regions
        self.assertEqual(
            joined_tables, {"orders", "customers", "regions"}
        )

    def test_each_table_joined_once_main_tree(self):
        _, r = gen({"rows": ["region"], "values": [{"measure_id": "sales"}]})
        seen = {}
        for phys, alias in joined_aliases(r.sql):
            # 主树别名 t0..tN：同一物理表不得出现两次
            if alias.startswith("t") and alias[1:].isdigit():
                self.assertNotIn(phys, seen, f"{phys} 被重复连接")
                seen[phys] = alias

    def test_dimension_table_measure_joined_in_subquery(self):
        # 类目分组 + 客户授信总额：customers 必须在子查询路径里出现
        _, r = gen({
            "rows": ["category"],
            "values": [{"measure_id": "total_credit"}],
        })
        tables = referenced_physical_tables(r.sql)
        self.assertIn("customers", tables)
        self.assertIn("products", tables)
        self.assertIn("orders", tables)
        self.assertIn("order_items", tables)

    def test_plan_involved_covers_refs(self):
        from app.sample_model import build_sample_model

        model = build_sample_model()
        s = spec({"rows": ["city"], "values": [{"measure_id": "sales_amount"}],
                  "filters": [{"field_id": "category", "op": "eq", "value": "食品饮料"}]})
        plan = build_plan(model, s)
        involved = set(plan.involved_tables)
        for t in ("regions", "customers", "orders", "order_items", "products"):
            self.assertIn(t, involved)

    def test_disconnected_tables_rejected(self):
        from app.kernel import PlanningError
        from app.kernel.schema import (
            AggFunc, Column, DataType, Measure, Table,
        )
        from app.sample_model import build_sample_model

        model = build_sample_model()
        # 孤岛表，没有任何关联
        model.tables["isolated"] = Table(
            id="isolated", name="孤岛", physical_name="isolated",
            columns=[Column("id", DataType.INTEGER, primary_key=True),
                     Column("v", DataType.NUMERIC)],
        )
        model.measures["iso_v"] = Measure(
            id="iso_v", name="孤岛值", table="isolated", column="v",
            agg=AggFunc.SUM,
        )
        with self.assertRaises(PlanningError):
            gen.__wrapped__ if False else build_plan(
                model, spec({"values": [{"measure_id": "iso_v"}],
                             "rows": ["region"]})
            )


if __name__ == "__main__":
    unittest.main()
