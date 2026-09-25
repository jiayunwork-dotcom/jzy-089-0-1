"""Whitelisted spreadsheet-style expression sandbox.

Calculated fields (and optional per-row measure expressions) are parsed
with Python's :mod:`ast`, checked against a strict node/function
whitelist, type-inferred, and *compiled to parameterised SQL*.

Security properties
-------------------
* ``eval`` / ``exec`` / imports / attribute access / comprehensions /
  lambdas are rejected at parse time.
* Only the functions in :data:`FUNCTIONS` are callable.
* String/number literals never reach SQL as text: strings are emitted as
  ``$n`` placeholders; ints/floats are emitted inline only after a
  primitive type check; identifiers must be columns of the bound table.
* Syntax and semantic errors carry the source offset/length so the UI can
  underline the offending token (validation happens at save time, not
  query time).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Callable, Optional

from .errors import ExpressionError
from .modeling import ColumnType, Model, Table

# ---------------------------------------------------------------------------
# Function whitelist
# ---------------------------------------------------------------------------

# type signature: (arg types...) -> return type; "*" marks variadic.
# The check is intentionally light: "any" matches everything, "num"
# matches integer/number/date differences only where we care.
# Supported DATEADD/DATEDIFF units.
DATE_UNITS = ("day", "hour", "minute", "second", "month", "year")


@dataclass(frozen=True)
class FuncSpec:
    sql: Callable[[list[str]], str]
    arg_types: tuple[str, ...]
    variadic: bool = False
    returns: ColumnType = "number"


def _extract(field: str) -> Callable[[list[str]], str]:
    def build(args: list[str]) -> str:
        return f"EXTRACT({field} FROM {args[0]})::integer"

    return build


def _like(mode: str) -> Callable[[list[str]], str]:
    # mode: "startswith" | "endswith" | "contains". The haystack pattern
    # is escaped for LIKE metacharacters and matched case-insensitively.
    def escaped(expr: str) -> str:
        return (
            f"REPLACE(REPLACE(REPLACE({expr}, '\\', '\\\\'), "
            f"'%', '\\%'), '_', '\\_')"
        )

    def build(args: list[str]) -> str:
        needle = escaped(args[1])
        if mode == "startswith":
            pattern = f"({needle} || '%')"
        elif mode == "endswith":
            pattern = f"('%' || {needle})"
        else:
            pattern = f"('%' || {needle} || '%')"
        return f"({args[0]} ILIKE {pattern} ESCAPE '\\')"

    return build


def _dateadd(args: list[str]) -> str:
    unit = args[0].lower()
    if unit not in DATE_UNITS:
        raise ExpressionError(f"DATEADD unit must be one of {DATE_UNITS}")
    return f"({args[2]} + MAKE_INTERVAL({unit}s => {args[1]}::integer))"


def _datediff(args: list[str]) -> str:
    unit = args[0].lower()
    factor = {
        "day": "86400.0",
        "hour": "3600.0",
        "minute": "60.0",
        "second": "1.0",
    }.get(unit)
    if factor is None:
        raise ExpressionError("DATEDIFF supports day/hour/minute/second")
    return f"((EXTRACT(EPOCH FROM ({args[2]} - {args[1]}))) / {factor})"


def _substring(args: list[str]) -> str:
    if len(args) == 2:
        return f"SUBSTRING({args[0]} FROM {args[1]})"
    return f"SUBSTRING({args[0]} FROM {args[1]} FOR {args[2]})"


def _in_expr(args: list[str], negate: bool = False) -> str:
    # The second argument is guaranteed (by the type checker) to be a
    # list literal rendered as an inline value list.
    keyword = "NOT IN" if negate else "IN"
    inner = args[1]
    if inner == "__EMPTY_LIST__":
        return ("FALSE" if not negate else "TRUE")
    return f"({args[0]} {keyword} {inner})"


FUNCTIONS: dict[str, FuncSpec] = {
    # numeric
    "ABS": FuncSpec(lambda a: f"ABS({a[0]})", ("num",), returns="number"),
    "ROUND": FuncSpec(
        lambda a: f"ROUND({a[0]}, {a[1]})" if len(a) == 2 else f"ROUND({a[0]})",
        ("num",), variadic=True, returns="number"
    ),
    "CEIL": FuncSpec(lambda a: f"CEIL({a[0]})", ("num",), returns="number"),
    "FLOOR": FuncSpec(lambda a: f"FLOOR({a[0]})", ("num",), returns="number"),
    "SQRT": FuncSpec(lambda a: f"SQRT({a[0]})", ("num",), returns="number"),
    "POWER": FuncSpec(lambda a: f"POWER({a[0]}, {a[1]})", ("num", "num")),
    "MOD": FuncSpec(lambda a: f"MOD({a[0]}, {a[1]})", ("num", "num")),
    "EXP": FuncSpec(lambda a: f"EXP({a[0]})", ("num",), returns="number"),
    "LN": FuncSpec(lambda a: f"LN({a[0]})", ("num",), returns="number"),
    "SIGN": FuncSpec(lambda a: f"SIGN({a[0]})", ("num",), returns="integer"),
    # string
    "UPPER": FuncSpec(lambda a: f"UPPER({a[0]})", ("str",), returns="string"),
    "LOWER": FuncSpec(lambda a: f"LOWER({a[0]})", ("str",), returns="string"),
    "TRIM": FuncSpec(lambda a: f"TRIM({a[0]})", ("str",), returns="string"),
    "LTRIM": FuncSpec(lambda a: f"LTRIM({a[0]})", ("str",), returns="string"),
    "RTRIM": FuncSpec(lambda a: f"RTRIM({a[0]})", ("str",), returns="string"),
    "LENGTH": FuncSpec(lambda a: f"LENGTH({a[0]})", ("str",), returns="integer"),
    "CONCAT": FuncSpec(
        lambda a: f"CONCAT({', '.join(a)})", ("any",), variadic=True,
        returns="string",
    ),
    "LEFT": FuncSpec(lambda a: f"LEFT({a[0]}, {a[1]})", ("str", "num")),
    "RIGHT": FuncSpec(lambda a: f"RIGHT({a[0]}, {a[1]})", ("str", "num")),
    "SUBSTRING": FuncSpec(_substring, ("str", "num"), variadic=True),
    "REPLACE": FuncSpec(
        lambda a: f"REPLACE({a[0]}, {a[1]}, {a[2]})",
        ("str", "str", "str"), returns="string",
    ),
    "STARTSWITH": FuncSpec(_like("startswith"), ("str", "str"), returns="boolean"),
    "ENDSWITH": FuncSpec(_like("endswith"), ("str", "str"), returns="boolean"),
    "CONTAINS": FuncSpec(_like("contains"), ("str", "str"), returns="boolean"),
    # dates
    "YEAR": FuncSpec(_extract("YEAR"), ("date",), returns="integer"),
    "QUARTER": FuncSpec(_extract("QUARTER"), ("date",), returns="integer"),
    "MONTH": FuncSpec(_extract("MONTH"), ("date",), returns="integer"),
    "DAY": FuncSpec(_extract("DAY"), ("date",), returns="integer"),
    "DATEDIFF": FuncSpec(_datediff, ("str", "date", "date"), returns="number"),
    "DATEADD": FuncSpec(_dateadd, ("str", "num", "date"), returns="date"),
    "TODAY": FuncSpec(lambda a: "CURRENT_DATE", (), returns="date"),
    "NOW": FuncSpec(lambda a: "CURRENT_TIMESTAMP", (), returns="datetime"),
    # conditional / null
    "IF": FuncSpec(
        lambda a: f"CASE WHEN {a[0]} THEN {a[1]} ELSE {a[2]} END",
        ("boolean", "any", "any"), returns="any",
    ),
    "COALESCE": FuncSpec(
        lambda a: f"COALESCE({', '.join(a)})", ("any",), variadic=True,
        returns="any",
    ),
    "NULLIF": FuncSpec(lambda a: f"NULLIF({a[0]}, {a[1]})", ("any", "any")),
    "IN": FuncSpec(lambda a: _in_expr(a, False), ("any", "list"), returns="boolean"),
    "NOT_IN": FuncSpec(
        lambda a: _in_expr(a, True), ("list",), returns="boolean"
    ),
}
# Fix NOT_IN signature (first arg scalar, second list):
FUNCTIONS["NOT_IN"] = FuncSpec(
    lambda a: _in_expr(a, True), ("any", "list"), returns="boolean"
)

NUMERIC_TYPES = {"integer", "number"}
DATEISH = {"date", "datetime"}

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare, ast.IfExp,
    ast.Call, ast.Name, ast.Constant, ast.Load, ast.And, ast.Or, ast.Not,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.USub, ast.UAdd,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot,
    ast.In, ast.NotIn, ast.List, ast.Tuple,
)


def _err(node: ast.AST, message: str) -> ExpressionError:
    length = max(1, (node.end_col_offset or node.col_offset + 1) - node.col_offset)
    return ExpressionError(
        message,
        line=getattr(node, "lineno", 1),
        column=node.col_offset + 1,
        end_column=node.col_offset + 1 + length,
        offset=node.col_offset,
        length=length,
    )


@dataclass
class CompiledExpression:
    """A validated expression bound to a model table.

    Rendering is re-entrant: the same compiled expression may be rendered
    into several CTEs; each render appends its own parameters.
    """

    source: str
    tree: ast.Expression
    table: Table
    result_type: ColumnType

    def render(self, alias: str, params: list) -> str:
        ctx = _RenderCtx(self.table, alias, params, source=self.source)
        return ctx.visit(self.tree.body)


class _RenderCtx:
    def __init__(self, table: Table, alias: str, params: list, source: str):
        self.table = table
        self.alias = alias
        self.params = params
        self.source = source
        # cache column types
        self.cols = {c.name: c.type for c in table.columns}

    def add_param(self, value) -> str:
        self.params.append(value)
        return f"${len(self.params)}"

    def visit(self, node: ast.AST) -> str:
        method = "v_" + type(node).__name__
        visitor = getattr(self, method, None)
        if visitor is None:
            raise _err(node, f"Syntax {type(node).__name__} is not allowed")
        return visitor(node)

    # -- literals -------------------------------------------------------------
    def v_Constant(self, node: ast.Constant) -> str:
        val = node.value
        if val is None:
            return "NULL"
        if isinstance(val, bool):
            return "TRUE" if val else "FALSE"
        if isinstance(val, int):
            return str(val)
        if isinstance(val, float):
            if val != val or val in (float("inf"), float("-inf")):
                raise _err(node, "NaN / infinity literals are not allowed")
            return repr(val)
        if isinstance(val, str):
            return self.add_param(val)
        raise _err(node, f"Unsupported literal type {type(val).__name__}")

    def _v_seq(self, node) -> str:
        # Used only as the second argument of IN / NOT_IN.
        parts = [self.visit(elt) for elt in node.elts]
        if not parts:
            return "__EMPTY_LIST__"
        return "(" + ", ".join(parts) + ")"

    v_List = _v_seq
    v_Tuple = _v_seq

    def v_Name(self, node: ast.Name) -> str:
        if node.id not in self.cols:
            raise _err(
                node,
                f"Unknown column {node.id!r} on table {self.table.id!r}",
            )
        return f'"{self.alias}"."{node.id}"'

    # -- operators ------------------------------------------------------------
    def v_BoolOp(self, node: ast.BoolOp) -> str:
        op = "AND" if isinstance(node.op, ast.And) else "OR"
        return "(" + f" {op} ".join(self.visit(v) for v in node.values) + ")"

    def v_UnaryOp(self, node: ast.UnaryOp) -> str:
        if isinstance(node.op, ast.Not):
            return f"(NOT {self.visit(node.operand)})"
        if isinstance(node.op, ast.USub):
            return f"(-{self.visit(node.operand)})"
        return f"(+{self.visit(node.operand)})"

    def v_BinOp(self, node: ast.BinOp) -> str:
        left, right = self.visit(node.left), self.visit(node.right)
        for op_type, sym in (
            (ast.Add, "+"), (ast.Sub, "-"), (ast.Mult, "*"),
        ):
            if isinstance(node.op, op_type):
                return f"({left} {sym} {right})"
        if isinstance(node.op, ast.Div):
            # Cast numerator to numeric so integer columns divide with
            # decimals; NULLIF turns division by zero into NULL.
            return f"(({left})::numeric / NULLIF(({right}), 0))"
        if isinstance(node.op, ast.Mod):
            return f"MOD({left}, {right})"
        if isinstance(node.op, ast.Pow):
            return f"POWER({left}, {right})"
        raise _err(node, "Unsupported operator")

    _CMP = {
        ast.Eq: "=", ast.NotEq: "<>", ast.Lt: "<", ast.LtE: "<=",
        ast.Gt: ">", ast.GtE: ">=",
    }

    def v_Compare(self, node: ast.Compare) -> str:
        parts: list[str] = []
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            right = self.visit(comparator)
            if isinstance(op, (ast.In, ast.NotIn)):
                if not isinstance(comparator, (ast.List, ast.Tuple)):
                    raise _err(
                        comparator,
                        "IN / NOT IN requires a literal list on the right "
                        "side, e.g. status in ['paid', 'shipped']",
                    )
                keyword = "NOT IN" if isinstance(op, ast.NotIn) else "IN"
                parts.append(
                    "TRUE" if right == "__EMPTY_LIST__" and isinstance(op, ast.NotIn)
                    else "FALSE" if right == "__EMPTY_LIST__"
                    else f"({left} {keyword} {right})"
                )
            elif isinstance(op, (ast.Is, ast.IsNot)):
                if not isinstance(comparator, ast.Constant) or comparator.value is not None:
                    raise _err(comparator, "Only comparisons against None are allowed")
                parts.append(
                    f"({left} IS NOT NULL)" if isinstance(op, ast.IsNot)
                    else f"({left} IS NULL)"
                )
            else:
                parts.append(f"({left} {self._CMP[type(op)]} {right})")
            left = right
        return "(" + " AND ".join(parts) + ")"

    def v_IfExp(self, node: ast.IfExp) -> str:
        return (
            f"CASE WHEN {self.visit(node.test)} "
            f"THEN {self.visit(node.body)} "
            f"ELSE {self.visit(node.orelse)} END"
        )

    def v_Call(self, node: ast.Call) -> str:
        if not isinstance(node.func, ast.Name):
            raise _err(node, "Only whitelisted functions may be called")
        fname = node.func.id.upper()
        spec = FUNCTIONS.get(fname)
        if spec is None:
            raise _err(
                node.func,
                f"Function {node.func.id!r} is not in the whitelist",
            )
        if node.keywords:
            raise _err(node, f"{fname}() does not accept keyword arguments")
        arg_sql = [self.visit(a) for a in node.args]
        if spec.variadic:
            if len(arg_sql) < len(spec.arg_types):
                raise _err(node, f"{fname}() needs at least {len(spec.arg_types)} arguments")
        elif len(arg_sql) != len(spec.arg_types):
            raise _err(
                node,
                f"{fname}() takes {len(spec.arg_types)} arguments, "
                f"got {len(arg_sql)}",
            )
        return spec.sql(arg_sql)


# ---------------------------------------------------------------------------
# Validation / type inference
# ---------------------------------------------------------------------------

class _Validator:
    def __init__(self, source: str, table: Table):
        self.source = source
        self.table = table
        self.cols = {c.name: c.type for c in table.columns}

    def check(self, node: ast.AST) -> ColumnType:
        if not isinstance(node, _ALLOWED_NODES):
            raise _err(node, f"Syntax {type(node).__name__} is not allowed")
        method = "t_" + type(node).__name__
        return getattr(self, method)(node)

    def t_Expression(self, node):
        return self.check(node.body)

    def t_Constant(self, node) -> ColumnType:
        val = node.value
        if val is None or isinstance(val, str):
            return "string" if isinstance(val, str) else "string"
        if isinstance(val, bool):
            return "boolean"
        if isinstance(val, int):
            return "integer"
        if isinstance(val, float):
            return "number"
        raise _err(node, "Unsupported literal type")

    def t_Name(self, node) -> ColumnType:
        if node.id not in self.cols:
            raise _err(node, f"Unknown column {node.id!r} on table {self.table.id!r}")
        return self.cols[node.id]

    def t_BoolOp(self, node):
        for v in node.values:
            t = self.check(v)
            if t != "boolean":
                raise _err(v, "AND / OR operands must be boolean")
        return "boolean"

    def t_UnaryOp(self, node):
        t = self.check(node.operand)
        if isinstance(node.op, ast.Not):
            if t != "boolean":
                raise _err(node.operand, "NOT requires a boolean operand")
            return "boolean"
        if t not in NUMERIC_TYPES:
            raise _err(node.operand, "Sign +/- requires a numeric operand")
        return t

    def t_BinOp(self, node):
        lt = self.check(node.left)
        rt = self.check(node.right)
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow)):
            if lt not in NUMERIC_TYPES or rt not in NUMERIC_TYPES:
                raise _err(node, "Arithmetic requires numeric operands")
            return "number" if isinstance(node.op, (ast.Div, ast.Pow)) or lt != rt else lt
        raise _err(node, "Unsupported operator")

    def t_Compare(self, node):
        self.check(node.left)
        for comparator in node.comparators:
            self.check(comparator)
        return "boolean"

    def t_IfExp(self, node):
        if self.check(node.test) != "boolean":
            raise _err(node.test, "IF condition must be boolean")
        bt = self.check(node.body)
        self.check(node.orelse)
        return bt

    def t_seq(self, node):
        for elt in node.elts:
            self.check(elt)
        return "list"

    t_List = t_seq
    t_Tuple = t_seq

    def t_Call(self, node) -> ColumnType:
        if not isinstance(node.func, ast.Name):
            raise _err(node, "Only whitelisted functions may be called")
        fname = node.func.id.upper()
        spec = FUNCTIONS.get(fname)
        if spec is None:
            raise _err(node.func, f"Function {node.func.id!r} is not in the whitelist")
        if node.keywords:
            raise _err(node, f"{fname}() does not accept keyword arguments")
        types = [self.check(a) for a in node.args]
        if spec.variadic:
            if len(types) < len(spec.arg_types):
                raise _err(node, f"{fname}() needs at least {len(spec.arg_types)} arguments")
        elif len(types) != len(spec.arg_types):
            raise _err(
                node,
                f"{fname}() takes {len(spec.arg_types)} arguments, got {len(types)}",
            )
        for got, expected in zip(types, spec.arg_types):
            self._expect(node, got, expected)
        # variadic remaining args follow the type of the last declared one
        if spec.variadic:
            tail = spec.arg_types[-1]
            for got in types[len(spec.arg_types):]:
                self._expect(node, got, tail)
        ret = spec.returns
        if ret == "any":
            ret = types[1] if fname == "IF" else types[0]
        return ret

    @staticmethod
    def _expect(node, got: ColumnType, expected: str) -> None:
        if expected == "any":
            return
        if expected == "num" and got in NUMERIC_TYPES:
            return
        if expected == "str" and got == "string":
            return
        if expected == "date" and got in DATEISH:
            return
        if expected == "list" and got == "list":
            return
        if expected == got:
            return
        raise _err(node, f"Type mismatch: expected {expected}, got {got}")


def compile_expression(
    source: str, table_id: str, model: Model
) -> CompiledExpression:
    """Parse, whitelist-check and type-infer ``source`` against a table.

    Raises :class:`ExpressionError` (with source position) on any problem.
    """
    if not isinstance(source, str) or not source.strip():
        raise ExpressionError("Expression is empty", column=1)
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        offset = exc.offset or 1
        text = exc.text or source
        length = max(1, len(text.rstrip()) - (offset - 1))
        raise ExpressionError(
            f"Syntax error: {exc.msg}",
            line=exc.lineno or 1,
            column=offset,
            end_column=offset + 1,
            offset=offset - 1,
            length=length,
        )
    table = model.table(table_id)
    result_type = _Validator(source, table).check(tree)
    return CompiledExpression(
        source=source, tree=tree, table=table, result_type=result_type
    )


def validate_expression(source: str, table_id: str, model: Model) -> dict:
    """JSON-friendly wrapper used by the save/validate endpoints."""
    compiled = compile_expression(source, table_id, model)
    return {"ok": True, "resultType": compiled.result_type}
