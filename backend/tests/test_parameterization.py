"""规则 6：所有用户输入字面量必须参数化，禁止字符串拼接进 SQL。"""
from __future__ import annotations

import unittest

from tests._helpers import gen


class ParameterizationTest(unittest.TestCase):
    def test_scalar_filter_is_parameter(self):
        _, r = gen({
            "rows": ["category"],
            "values": [{"measure_id": "sales_amount"}],
            "filters": [{"field_id": "channel", "op": "eq", "value": "线上"}],
        })
        self.assertIn('"channel" = %s::text', r.sql)
        self.assertIn("线上", r.params)
        # SQL 文本里绝不出现该字面量
        self.assertNotIn("'线上'", r.sql)

    def test_in_list_uses_array_parameter(self):
        values = ["电子产品", "家居"]
        _, r = gen({
            "rows": ["channel"],
            "values": [{"measure_id": "sales_amount"}],
            "filters": [{"field_id": "category", "op": "in", "value": values}],
        })
        self.assertRegex(r.sql, r"= ANY\(%s::text\[\]\)")
        self.assertIn(values, r.params)
        for v in values:
            self.assertNotIn(f"'{v}'", r.sql)

    def test_between_dates_use_parameters(self):
        lo, hi = "2024-03-01", "2024-09-30"
        _, r = gen({
            "rows": ["month"],
            "values": [{"measure_id": "freight_total"}],
            "filters": [{"field_id": "day", "op": "between", "value": [lo, hi]}],
        })
        self.assertIn("BETWEEN %s::date AND %s::date", r.sql)
        self.assertIn(lo, r.params)
        self.assertIn(hi, r.params)
        self.assertNotIn(f"'{lo}'", r.sql)

    def test_numeric_between_parameterized(self):
        _, r = gen({
            "rows": ["category"],
            "values": [{"measure_id": "sales_amount"},
                       {"measure_id": "avg_discount"}],
            "filters": [{"field_id": "avg_discount", "op": "between", "value": [0.1, 0.3]}],
        })
        self.assertIn("%s::numeric", r.sql)
        self.assertIn(0.1, r.params)
        self.assertIn(0.3, r.params)

    def test_like_pattern_parameterized_and_escaped(self):
        _, r = gen({
            "rows": ["product"],
            "values": [{"measure_id": "sales"}],
            "filters": [{"field_id": "product", "op": "contains", "value": "100%_x"}],
        })
        self.assertRegex(r.sql, r"LIKE %s::text ESCAPE '\\'")
        patterns = [p for p in r.params if isinstance(p, str) and "100" in p]
        self.assertTrue(patterns)
        self.assertEqual(patterns[0], r"%100\%\_x%")

    def test_limit_parameterized(self):
        _, r = gen({"rows": ["channel"],
                    "values": [{"measure_id": "freight_total"}], "limit": 25})
        self.assertIn("LIMIT %s::bigint", r.sql)
        self.assertEqual(r.params[-1], 25)

    def test_expression_literals_parameterized(self):
        # big_order_count 的过滤 quantity >= 5 是模型自带沙箱表达式
        _, r = gen({"rows": ["channel"], "values": [{"measure_id": "big_order_count"}]})
        # 数字 5 不应裸出现在 SQL 文本（应作为 %s::numeric）
        self.assertIn("%s::numeric", r.sql)
        self.assertIn(5, r.params)


if __name__ == "__main__":
    unittest.main()
