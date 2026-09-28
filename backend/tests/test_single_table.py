"""规则 1：只涉及单表的查询不应产生任何 JOIN。"""
from __future__ import annotations

import unittest

from tests._helpers import gen


class SingleTableTest(unittest.TestCase):
    def test_single_table_grouped_no_join(self):
        _, r = gen({"rows": ["channel"], "values": [{"measure_id": "freight_total"}]})
        self.assertNotIn("JOIN", r.sql.upper())
        # 也不应出现为跨表准备的包装子查询
        self.assertIn("FROM \"orders\"", r.sql)

    def test_single_table_grand_total_no_join(self):
        _, r = gen({"values": [{"measure_id": "freight_total"}]})
        self.assertNotIn("JOIN", r.sql.upper())
        self.assertIn("SUM(", r.sql.upper())

    def test_single_table_filter_no_join(self):
        _, r = gen({
            "values": [{"measure_id": "freight_total"}],
            "filters": [{"field_id": "channel", "op": "eq", "value": "线上"}],
        })
        self.assertNotIn("JOIN", r.sql.upper())
        self.assertIn("WHERE", r.sql.upper())


if __name__ == "__main__":
    unittest.main()
