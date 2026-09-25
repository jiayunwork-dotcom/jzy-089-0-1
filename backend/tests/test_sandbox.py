"""Expression sandbox: whitelist enforcement, save-time validation with
source positions, and parameterised SQL compilation."""
from __future__ import annotations

import ast

import pytest

from app.errors import ExpressionError
from app.sandbox import compile_expression, validate_expression, FUNCTIONS


def test_arithmetic_compiles_to_parameterised_sql(model):
    comp = compile_expression("unit_price * quantity * 0.2", "order_lines", model)
    params: list = []
    sql = comp.render("order_lines", params)
    assert sql == '(("order_lines"."unit_price" * "order_lines"."quantity") * 0.2)'
    assert params == []


def test_string_literal_becomes_parameter(model):
    comp = compile_expression(
        "IF(status = 'paid', 1, 0)", "orders", model
    )
    params: list = []
    sql = comp.render("orders", params)
    assert "'paid'" not in sql
    assert "$1" in sql
    assert params == ["paid"]


def test_division_uses_numeric_cast_and_nullif(model):
    params: list = []
    sql = compile_expression(
        "total_amount / quantity_total", "orders", model
    ).render("orders", params)
    assert "::numeric" in sql
    assert "NULLIF" in sql


def test_unknown_function_rejected_with_position(model):
    with pytest.raises(ExpressionError) as exc:
        compile_expression("eval('1+1')", "orders", model)
    err = exc.value
    assert "whitelist" in err.message
    # points at the function name token
    assert err.offset <= 4


def test_imports_attributes_and_calls_blocked(model):
    bad = [
        "__import__('os')",
        "os.system('ls')",
        "().__class__.__bases__",
        "[x for x in (1,2)]",
        "lambda: 1",
        "open('/etc/passwd')",
        "exec('1')",
    ]
    for expr in bad:
        with pytest.raises(ExpressionError):
            compile_expression(expr, "orders", model)


def test_syntax_error_reports_position(model):
    with pytest.raises(ExpressionError) as exc:
        compile_expression("unit_price * ", "order_lines", model)
    assert exc.value.column >= 1


def test_type_mismatch_reports_position(model):
    with pytest.raises(ExpressionError) as exc:
        compile_expression("UPPER(quantity)", "order_lines", model)
    assert "Type mismatch" in exc.value.message


def test_unknown_column_pointed_out(model):
    with pytest.raises(ExpressionError) as exc:
        compile_expression("nonexistent + 1", "orders", model)
    assert "Unknown column" in exc.value.message


def test_validate_helper_returns_type(model):
    result = validate_expression(
        "IF(unit_price > 100, 'high', 'low')", "products", model
    )
    assert result == {"ok": True, "resultType": "string"}


def test_whitelist_functions_exist():
    for name in ("YEAR", "MONTH", "QUARTER", "DAY", "CONCAT", "UPPER",
                 "LOWER", "SUBSTRING", "IF", "COALESCE", "DATEDIFF",
                 "DATEADD", "ROUND", "IN", "STARTSWITH"):
        assert name in FUNCTIONS


def test_in_with_literal_list(model):
    params: list = []
    sql = compile_expression(
        "status in ['paid', 'shipped']", "orders", model
    ).render("orders", params)
    assert "IN (" in sql
    assert params == ["paid", "shipped"]
