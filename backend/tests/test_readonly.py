"""规则 7：只允许生成 SELECT；任何写操作或结构变更语句都要被拒。"""
from __future__ import annotations

import unittest

from app.query_executor import UnsafeQueryError, assert_select_only


class ReadOnlyGuardTest(unittest.TestCase):
    def test_select_allowed(self):
        assert_select_only("SELECT 1 AS x")
        assert_select_only("WITH t AS (SELECT 1) SELECT * FROM t")

    def test_insert_rejected(self):
        for sql in [
            "INSERT INTO orders VALUES (1)",
            " UPDATE orders SET freight = 0",
            "DELETE FROM order_items",
            "DROP TABLE orders",
            "ALTER TABLE orders ADD COLUMN x int",
            "TRUNCATE orders",
            "CREATE TABLE x (id int)",
            "GRANT ALL ON orders TO bi_user",
            "REFRESH MATERIALIZED VIEW x",
        ]:
            with self.subTest(sql=sql):
                with self.assertRaises(UnsafeQueryError):
                    assert_select_only(sql)

    def test_multi_statement_rejected(self):
        with self.assertRaises(UnsafeQueryError):
            assert_select_only("SELECT 1; DROP TABLE orders")

    def test_translator_only_emits_select(self):
        from tests._helpers import gen

        for d in [
            {"rows": ["channel"], "values": [{"measure_id": "freight_total"}]},
            {"rows": ["category"], "values": [{"measure_id": "total_credit"}],
             "filters": [{"field_id": "channel", "op": "eq", "value": "线上"}]},
            {"values": [{"measure_id": "freight_total"}]},
        ]:
            _, r = gen(d)
            assert_select_only(r.sql)  # 不抛异常即通过
            self.assertNotIn(";", r.sql)

    def test_read_only_transaction_blocks_write(self):
        """数据库侧纵深防御：只读事务中的写语句必须失败。"""
        try:
            import psycopg

            from tests._helpers import TEST_DSN
        except ImportError:
            self.skipTest("psycopg 不可用")
        try:
            conn = psycopg.connect(TEST_DSN)
        except Exception as e:
            self.skipTest(f"PostgreSQL 不可用: {e}")
        try:
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                with conn.transaction():
                    conn.execute("SET TRANSACTION READ ONLY")
                    conn.execute("CREATE TABLE should_not_exist (id int)")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
