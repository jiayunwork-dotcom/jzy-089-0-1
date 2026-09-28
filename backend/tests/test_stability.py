"""规则 4：同一组拖拽在字段顺序不变时生成稳定一致的 SQL。"""
from __future__ import annotations

import unittest

from tests._helpers import gen, spec


class StabilityTest(unittest.TestCase):
    def test_sql_byte_identical_across_runs(self):
        d = {"rows": ["region", "segment"],
             "values": [{"measure_id": "sales_amount"},
                        {"measure_id": "total_credit"}],
             "filters": [{"field_id": "channel", "op": "eq", "value": "线上"}],
             "sorts": [{"index": 1, "dir": "desc"}], "limit": 50}
        _, r1 = gen(d)
        _, r2 = gen(d)
        self.assertEqual(r1.sql, r2.sql)
        self.assertEqual(r1.params, r2.params)

    def test_order_of_fields_matters_and_is_respected(self):
        a = {"rows": ["region", "segment"], "values": [{"measure_id": "sales"}]}
        b = {"rows": ["segment", "region"], "values": [{"measure_id": "sales"}]}
        _, ra = gen(a)
        _, rb = gen(b)
        self.assertEqual([c["field_id"] for c in ra.result_columns[:2]],
                         ["region", "segment"])
        self.assertEqual([c["field_id"] for c in rb.result_columns[:2]],
                         ["segment", "region"])
        self.assertIn("region_name", ra.sql)
        self.assertIn("segment", ra.sql)

    def test_values_order_defines_column_order(self):
        d1 = {"rows": ["channel"],
              "values": [{"measure_id": "freight_total"},
                         {"measure_id": "sales"}]}
        d2 = {"rows": ["channel"],
              "values": [{"measure_id": "sales"},
                         {"measure_id": "freight_total"}]}
        _, r1 = gen(d1)
        _, r2 = gen(d2)
        self.assertEqual(r1.result_columns[1]["field_id"], "freight_total")
        self.assertEqual(r2.result_columns[1]["field_id"], "sales")
        # 各跑两次依然逐字符一致
        self.assertEqual(gen(d1)[1].sql, r1.sql)
        self.assertEqual(gen(d2)[1].sql, r2.sql)

    def test_model_dict_insertion_order_independent_reload(self):
        # 从 JSON 反序列化两次，结果应一致（容器顺序保持模型声明顺序）
        from app.kernel.schema import Model
        from app.sample_model import build_sample_model

        m1 = build_sample_model()
        raw = m1.to_dict()
        m2 = Model.from_dict(raw)
        d = {"rows": ["province"], "values": [{"measure_id": "sales_amount"}]}
        self.assertEqual(translate_one(m1, d), translate_one(m2, d))


def translate_one(model, d):
    from app.kernel import translate

    return translate(model, spec(d)).sql


if __name__ == "__main__":
    unittest.main()
