"""端到端：HTTP API -> 翻译 -> PostgreSQL 执行 -> 结果。

需要 TEST_DATABASE_URL 指向已灌入样例数据的 PostgreSQL 16。
"""
from __future__ import annotations

import os
import unittest

from app.config import settings
from app.sample_model import build_sample_model, SAMPLE_QUERIES

try:
    import psycopg
    _HAS = True
except ImportError:
    _HAS = False

from tests._helpers import TEST_DSN, spec


@unittest.skipUnless(_HAS, "需要 psycopg")
class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.conn = psycopg.connect(TEST_DSN)
            n = cls.conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            if n == 0:
                raise unittest.SkipTest("样例库未初始化")
        except Exception as e:
            raise unittest.SkipTest(f"PostgreSQL 不可用: {e}")
        cls.model = build_sample_model()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def _run(self, d):
        from app.kernel import translate
        from app.query_executor import execute

        tr = translate(self.model, spec(d))
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION READ ONLY")
            rs = execute(self.conn, tr.sql, tr.params, tr.result_columns,
                         statement_timeout_ms=15000)
        return rs

    def test_single_table_totals(self):
        rs = self._run({"rows": ["channel"],
                        "values": [{"measure_id": "freight_total"}]})
        self.assertEqual(rs.row_count, 2)
        total = sum(r["m1"] for r in rs.rows)
        baseline = self.conn.execute("SELECT COALESCE(SUM(freight),0) FROM orders").fetchone()[0]
        self.assertAlmostEqual(total, float(baseline), places=2)

    def test_region_sales_and_drilldown_invariant(self):
        rs1 = self._run({"rows": ["region"],
                         "values": [{"measure_id": "sales_amount"}]})
        self.assertTrue(rs1.row_count >= 1)
        grand1 = sum(r["m1"] for r in rs1.rows)

        # 钻取到省：各省之和 == 各大区之和（同一份事实、同样无额外过滤）
        from app.kernel import drill_down, translate
        from app.query_executor import execute

        s2 = drill_down(self.model, spec({"rows": ["region"],
                                          "values": [{"measure_id": "sales_amount"}]}),
                        "region")
        tr2 = translate(self.model, s2)
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION READ ONLY")
            rs2 = execute(self.conn, tr2.sql, tr2.params, tr2.result_columns)
        grand2 = sum(r["m1"] for r in rs2.rows)
        self.assertAlmostEqual(grand1, grand2, places=2)

    def test_filters_enum_date_number(self):
        rs = self._run({
            "rows": ["channel"],
            "values": [{"measure_id": "freight_total"},
                       {"measure_id": "sales_amount"}],
            "filters": [
                {"field_id": "channel", "op": "in", "value": ["线上"]},
                {"field_id": "day", "op": "between",
                 "value": ["2024-01-01", "2024-12-31"]},
                {"field_id": "sales_amount", "op": "gte", "value": 0},
            ],
        })
        for row in rs.rows:
            self.assertEqual(row["g1"], "线上")

    def test_all_sample_queries_execute(self):
        from app.kernel import drill_down, translate
        from app.query_executor import execute

        for q in SAMPLE_QUERIES:
            s = spec(q["spec"])
            if "drill" in q:
                s = drill_down(self.model, s, q["drill"])
            tr = translate(self.model, s)
            with self.conn.transaction():
                self.conn.execute("SET TRANSACTION READ ONLY")
                rs = execute(self.conn, tr.sql, tr.params, tr.result_columns)
            self.assertGreaterEqual(rs.row_count, 0, q["name"])

    def test_sorting_limit(self):
        rs = self._run({
            "rows": ["category"],
            "values": [{"measure_id": "sales_amount"}],
            "sorts": [{"index": 2, "dir": "desc"}],
            "limit": 2,
        })
        self.assertEqual(rs.row_count, 2)
        vals = [r["m1"] for r in rs.rows]
        self.assertEqual(vals, sorted(vals, reverse=True))

    def test_grand_total_remote_measure(self):
        # 无分组 + 跨表度量（客户授信总额）：标量子查询
        rs = self._run({"values": [{"measure_id": "total_credit"}]})
        baseline = self.conn.execute("SELECT SUM(credit_limit) FROM customers").fetchone()[0]
        self.assertAlmostEqual(rs.rows[0]["m1"], float(baseline), places=2)

    def test_write_rejected_at_db_level(self):
        import psycopg

        with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
            with self.conn.transaction():
                self.conn.execute("SET TRANSACTION READ ONLY")
                self.conn.execute("DELETE FROM order_items")


if __name__ == "__main__":
    unittest.main()
