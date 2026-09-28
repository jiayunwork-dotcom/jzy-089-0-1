"""建模层标识符白名单：物理结构 id 不允许任何可用于注入的字符。"""
from __future__ import annotations

import unittest

from app.kernel.schema import Column, DataType, Table
from app.sample_model import build_sample_model


class IdentifierGuardTest(unittest.TestCase):
    def test_bad_table_id(self):
        from app.kernel.schema import ID_RE

        for bad in ['a; DROP TABLE orders', 'a--', 'a b', 'a"b', '1x', 'a.b']:
            with self.subTest(bad=bad):
                self.assertIsNone(ID_RE.match(bad))

    def test_model_validate_rejects_evil_identifiers(self):
        model = build_sample_model()
        model.tables['evil; DROP TABLE x'] = Table(
            id='evil; DROP TABLE x', name="恶", physical_name="evil",
            columns=[Column("id", DataType.INTEGER, primary_key=True)],
        )
        with self.assertRaises(ValueError):
            model.validate()

    def test_good_identifiers(self):
        from app.kernel.schema import ID_RE

        for good in ["orders", "order_items", "_t1", "region_hierarchy_2024"]:
            self.assertIsNotNone(ID_RE.match(good))


if __name__ == "__main__":
    unittest.main()
