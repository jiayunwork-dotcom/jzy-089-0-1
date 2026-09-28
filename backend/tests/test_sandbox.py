"""表达式白名单沙箱：保存期语法/语义校验与错误定位。"""
from __future__ import annotations

import unittest

from app.kernel import sandbox


FIELDS = {"qty": "integer", "price": "numeric", "name": "text",
          "d": "date", "flag": "boolean"}


class SandboxSyntaxTest(unittest.TestCase):
    def test_valid_expressions(self):
        for expr in [
            "qty * price",
            "(qty + 1) / 2",
            'IF(qty >= 10, price * 0.9, price)',
            'UPPER(TRIM(name))',
            "CONCAT(name, '-', qty)",
            "TRUNC_DATE(d, 'month')",
            "DATE_PART('year', d)",
            "DATE_ADD(d, 'day', 7)",
            'IFNULL(price, 0) - 1',
            "qty > 3 AND flag = TRUE",
            'name IS NULL',
            'SUBSTR(name, 1, 3)',
        ]:
            with self.subTest(expr=expr):
                sandbox.validate_expression(expr, FIELDS)

    def test_syntax_error_reports_position(self):
        try:
            sandbox.validate_expression("qty * ", FIELDS)
        except sandbox.ExpressionError as e:
            self.assertIn("不完整", e.message)
            self.assertEqual(e.start, 6)
        else:
            self.fail("应抛出语法错误")

    def test_unknown_function_rejected_with_position(self):
        try:
            sandbox.validate_expression("EVAL(qty)", FIELDS)
        except sandbox.ExpressionError as e:
            self.assertIn("EVAL", e.message)
            self.assertEqual(e.start, 0)
            self.assertEqual(e.end, 4)

    def test_dangerous_names_not_functions(self):
        for expr in ["SYSTEM('ls')", "EXEC(qty)", "OPEN('/etc/passwd')",
                     "EVAL_PYTHON('1')", "__import__('os')"]:
            with self.subTest(expr=expr), self.assertRaises(sandbox.ExpressionError):
                sandbox.validate_expression(expr, FIELDS)

    def test_unknown_field(self):
        with self.assertRaises(sandbox.ExpressionError) as cm:
            sandbox.validate_expression("unknown_col + 1", FIELDS)
        self.assertIn("unknown_col", cm.exception.message)

    def test_type_mismatch_arithmetic(self):
        with self.assertRaises(sandbox.ExpressionError):
            sandbox.validate_expression("name + 1", FIELDS)

    def test_boolean_required_for_filter(self):
        with self.assertRaises(sandbox.ExpressionError):
            sandbox.validate_expression("qty", FIELDS, expect_boolean=True)
        sandbox.validate_expression("qty > 0", FIELDS, expect_boolean=True)

    def test_bad_date_unit(self):
        with self.assertRaises(sandbox.ExpressionError):
            sandbox.validate_expression("TRUNC_DATE(d, 'fortnight')", FIELDS)

    def test_arity_check(self):
        with self.assertRaises(sandbox.ExpressionError):
            sandbox.validate_expression("ROUND(qty)", FIELDS)

    def test_unclosed_string(self):
        with self.assertRaises(sandbox.ExpressionError):
            sandbox.validate_expression("UPPER('abc)", FIELDS)

    def test_compilation_parameterizes_literals(self):
        params = []
        sql = sandbox.compile_expression(
            'IF(qty >= 5, price * 0.9, price)',
            lambda n: {"qty": '"qty"', "price": '"price"'}[n],
            lambda v, t: (params.append(v) or "%s"),
        )
        self.assertNotIn("0.9", sql)
        self.assertIn(0.9, params)
        self.assertIn("CASE WHEN", sql)

    def test_nested_calc_fields_and_cycle_detection(self):
        from app.sample_model import build_sample_model
        from app.kernel.renderer import Scope, ParamBag
        from app.kernel.schema import CalculatedField, DataType

        model = build_sample_model()
        model.calculated_fields["cyc_a"] = CalculatedField(
            id="cyc_a", name="A", table="products", expression="cyc_b + 1",
            data_type=DataType.NUMERIC)
        model.calculated_fields["cyc_b"] = CalculatedField(
            id="cyc_b", name="B", table="products", expression="cyc_a + 1",
            data_type=DataType.NUMERIC)
        scope = Scope(model, {"products": "t0"})
        bag = ParamBag()
        scope._alloc = lambda v, t: bag.add(v, t)
        with self.assertRaises(sandbox.ExpressionError):
            scope.field_sql("cyc_a")


if __name__ == "__main__":
    unittest.main()
