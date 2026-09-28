"""规则 5：钻取前后除分组粒度外，过滤与度量保持不变。"""
from __future__ import annotations

import unittest

from app.kernel import QuerySpec, drill_down
from app.sample_model import build_sample_model
from tests._helpers import gen, spec


class DrillDownTest(unittest.TestCase):
    def setUp(self):
        self.model = build_sample_model()

    def test_drill_appends_next_level(self):
        s = spec({"rows": ["region"], "values": [{"measure_id": "sales_amount"}]})
        nxt = drill_down(self.model, s, "region")
        self.assertEqual(nxt.rows, ["region", "province"])
        # 数值与过滤不变
        self.assertEqual(s.values, nxt.values)
        self.assertEqual(s.filters, nxt.filters)

    def test_drill_time_hierarchy(self):
        s = spec({"rows": ["year"], "values": [{"measure_id": "sales"}]})
        nxt = drill_down(self.model, s, "year")
        self.assertEqual(nxt.rows, ["year", "quarter"])
        nxt2 = drill_down(self.model, nxt, "quarter")
        self.assertEqual(nxt2.rows, ["year", "quarter", "month"])

    def test_drill_sql_preserves_filters_and_measure(self):
        s = spec({
            "rows": ["region"],
            "values": [{"measure_id": "sales_amount"}],
            "filters": [{"field_id": "channel", "op": "eq", "value": "线上"}],
        })
        from app.kernel import translate

        before = translate(self.model, s)
        after = translate(self.model, drill_down(self.model, s, "region"))

        # 度量表达式（行金额）和筛选字面量在两条 SQL 中都保留
        self.assertIn('"unit_price"', before.sql)
        self.assertIn('"unit_price"', after.sql)
        self.assertIn('"channel" = %s::text', before.sql)
        self.assertIn('"channel" = %s::text', after.sql)
        # 筛选参数一致（除新增分组列带来的额外 date_trunc 参数等，这里没有）
        self.assertEqual(before.params, after.params)

        # 分组键从 1 个变 2 个
        self.assertIn("GROUP BY 1", before.sql)
        self.assertIn("GROUP BY 1, 2", after.sql)
        self.assertIn('"region_name" AS "g1"', after.sql)
        self.assertIn('"province" AS "g2"', after.sql)

    def test_drill_at_leaf_rejected(self):
        from app.kernel import PlanningError

        s = spec({"rows": ["city"], "values": [{"measure_id": "sales"}]})
        with self.assertRaises(PlanningError):
            drill_down(self.model, s, "city")

    def test_drill_without_hierarchy_rejected(self):
        from app.kernel import PlanningError

        s = spec({"rows": ["category"], "values": [{"measure_id": "sales"}]})
        with self.assertRaises(PlanningError):
            drill_down(self.model, s, "category")


if __name__ == "__main__":
    unittest.main()
