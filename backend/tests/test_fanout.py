"""规则 3：一对多关联下对"多"侧（或沿放大路径被复制的）度量求和
不应因 JOIN 被错误放大（避免重复计数）。

静态断言：度量必须经 "DISTINCT 主键" 去重子查询聚合，或在无放大边的树上直接聚合。
集成断言（有 PostgreSQL 时）：翻译结果等于独立基准查询，
并显式证明"朴素 JOIN 后 SUM"会给出更大（被放大）的数字。
"""
from __future__ import annotations

import re
import unittest

from app.kernel.planner import build_plan
from tests._helpers import gen, spec


class FanoutStaticTest(unittest.TestCase):
    def test_orders_measure_by_category_uses_distinct_pk_subquery(self):
        # orders.freight 沿 orders -> order_items(1:N) -> products 路径会被复制
        model, r = gen({"rows": ["category"],
                        "values": [{"measure_id": "freight_total"}]})
        plan = build_plan(model, spec({"rows": ["category"],
                                       "values": [{"measure_id": "freight_total"}]}))
        self.assertEqual(plan.direct_measures, [])
        self.assertTrue(plan.remote_measures)
        # 去重层：SELECT DISTINCT ... 主键 + 原始值
        self.assertRegex(r.sql, r"SELECT DISTINCT .*\"order_id\" AS \"entity_pk\"")
        self.assertRegex(r.sql, r"\"freight\" AS \"raw_value\"")
        # 外层先去重再求和
        self.assertRegex(r.sql, r"COALESCE\(SUM\(\"raw_value\"\), 0\)")

    def test_dimension_table_measure_dedup(self):
        # customers.credit_limit 按类目聚合：同一客户多单多行，必须按客户主键去重
        _, r = gen({"rows": ["category"],
                    "values": [{"measure_id": "total_credit"}]})
        self.assertRegex(r.sql, r"SELECT DISTINCT .*\"customer_id\" AS \"entity_pk\"")
        self.assertRegex(r.sql, r"\"credit_limit\" AS \"raw_value\"")

    def test_star_path_many_to_one_is_safe_direct(self):
        # 事实 -> 维度（多对一）不会放大：按大区汇总销量可以直接聚合
        model, r = gen({"rows": ["region"], "values": [{"measure_id": "sales"}]})
        plan = build_plan(model, spec({"rows": ["region"],
                                       "values": [{"measure_id": "sales"}]}))
        self.assertTrue(plan.direct_measures)
        # 直接 SUM，没有去重子查询
        self.assertNotIn("entity_pk", r.sql)

    def test_filter_on_fanout_dim_uses_correlated_exists(self):
        # 按类目聚合运费，同时对渠道（orders 侧、放大边之后）筛选：
        # 子查询中必须用相关 EXISTS 而不是直接 WHERE JOIN 链
        _, r = gen({
            "rows": ["category"],
            "values": [{"measure_id": "freight_total"}],
            "filters": [{"field_id": "channel", "op": "eq", "value": "线上"}],
        })
        self.assertIn("EXISTS", r.sql.upper())


try:
    import psycopg  # noqa: F401
    _HAS_PSYCOPG = True
except ImportError:
    _HAS_PSYCOPG = False


@unittest.skipUnless(_HAS_PSYCOPG, "需要 psycopg + PostgreSQL")
class FanoutIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import psycopg

            from tests._helpers import TEST_DSN

            cls.conn = psycopg.connect(TEST_DSN)
            # 库内是否已灌样例数据
            n = cls.conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            if n == 0:
                raise unittest.SkipTest("样例库未初始化")
        except Exception as e:
            raise unittest.SkipTest(f"PostgreSQL 不可用: {e}")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def _fetch(self, d):
        from app.query_executor import assert_select_only, execute
        from app.sample_model import build_sample_model
        from app.kernel import translate

        model = build_sample_model()
        tr = translate(model, spec(d))
        assert_select_only(tr.sql)
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION READ ONLY")
            rs = execute(self.conn, tr.sql, tr.params, tr.result_columns)
        return rs

    def test_credit_by_category_matches_baseline(self):
        # customers.credit_limit 沿 类目 <- 明细 <- 订单 <- 客户 路径，
        # 同一客户多单多行时朴素 SUM 会被严重放大。
        rs = self._fetch({
            "rows": ["category"],
            "values": [{"measure_id": "total_credit"}],
        })
        got = {row["g1"]: row["m1"] for row in rs.rows}

        # 正确基准：该客户下单过该类目 -> 授信计入一次（按 类目+客户 去重）
        baseline = dict(self.conn.execute(
            """
            SELECT x.category, SUM(x.credit_limit)
            FROM (
                SELECT DISTINCT p.category, c.customer_id, c.credit_limit
                FROM customers c
                JOIN orders o ON o.customer_id = c.customer_id
                JOIN order_items oi ON oi.order_id = o.order_id
                JOIN products p ON p.product_id = oi.product_id
            ) x
            GROUP BY x.category
            """
        ).fetchall())
        for cat, val in baseline.items():
            self.assertAlmostEqual(got[cat], float(val), places=2,
                                   msg=f"类目 {cat} 客户授信被 JOIN 放大或丢失")

    def test_naive_join_actually_inflates(self):
        """证明测试场景确实能抓住 bug：朴素 SUM(credit_limit) 必须 > 正确值。"""
        naive = dict(self.conn.execute(
            """
            SELECT p.category, SUM(c.credit_limit)
            FROM customers c
            JOIN orders o ON o.customer_id = c.customer_id
            JOIN order_items oi ON oi.order_id = o.order_id
            JOIN products p ON p.product_id = oi.product_id
            GROUP BY p.category
            """
        ).fetchall())
        correct = dict(self.conn.execute(
            """
            SELECT x.category, SUM(x.credit_limit)
            FROM (
                SELECT DISTINCT p.category, c.customer_id, c.credit_limit
                FROM customers c
                JOIN orders o ON o.customer_id = c.customer_id
                JOIN order_items oi ON oi.order_id = o.order_id
                JOIN products p ON p.product_id = oi.product_id
            ) x
            GROUP BY x.category
            """
        ).fetchall())
        inflated = [c for c in naive if float(naive[c]) > float(correct[c])]
        self.assertTrue(inflated, "样例数据中应至少有一个类目在朴素 JOIN 下被放大")

        # 翻译器结果必须等于正确值、明显小于朴素值
        rs = self._fetch({
            "rows": ["category"],
            "values": [{"measure_id": "total_credit"}],
        })
        got = {row["g1"]: row["m1"] for row in rs.rows}
        for c in inflated:
            self.assertAlmostEqual(got[c], float(correct[c]), places=2)
            self.assertLess(got[c], float(naive[c]))


if __name__ == "__main__":
    unittest.main()
