"""沙箱表达式 -> PostgreSQL 的编译与校验。

保存时校验：语法错误、未注册函数、参数个数不符、引用了不存在的字段、
明显的类型错误（数值运算作用于文本等），错误位置以字符偏移返回。

编译保证：所有用户字面量通过 ``allocate(value, type)`` 回收为绑定参数占位符，
SQL 文本中绝不拼接字面量值；字段名由调用方传入已加引号的安全片段。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import ast_nodes as ast
from . import functions as funcs
from .errors import ExpressionError
from .parser import parse, Token

NUMERIC_FAMILY = {"numeric", "integer"}
DATE_FAMILY = {"date", "timestamp"}


@dataclass
class CompiledExpr:
    sql: str
    data_type: str
    node: ast.Node


# 比较运算允许的类型族组合
def _comparable(a: str, b: str) -> bool:
    if a in NUMERIC_FAMILY and b in NUMERIC_FAMILY:
        return True
    if a == "text" and b == "text":
        return True
    if a in DATE_FAMILY and b in DATE_FAMILY:
        return True
    if a == "any" or b == "any":
        return True
    if a == "boolean" and b == "boolean":
        return True
    return False


def _numeric(node: ast.Node, t: str, what: str) -> None:
    if t not in NUMERIC_FAMILY and t != "any":
        raise ExpressionError(f"{what}需要数值，实际为 {t}", node.pos, node.end)


def _boolean(node: ast.Node, t: str) -> None:
    if t != "boolean" and t != "any":
        raise ExpressionError(f"这里需要布尔条件，实际为 {t}", node.pos, node.end)


class _Validator:
    def __init__(self, fields: dict[str, str]) -> None:
        self.fields = fields

    def check(self, node: ast.Node) -> str:
        k = node.kind
        if k == ast.LITERAL:
            return node.data_type or "any"
        if k == ast.FIELD:
            if node.name not in self.fields:
                raise ExpressionError(
                    f"未知字段 {node.name!r}（只能引用当前表上的字段）",
                    node.pos,
                    node.end,
                )
            return self.fields[node.name]
        if k == ast.UNARY:
            t = self.check(node.operand)
            if node.op == "NOT":
                _boolean(node.operand, t)
                return "boolean"
            _numeric(node.operand, t, "一元运算")
            return "numeric"
        if k == ast.BINARY:
            lt = self.check(node.left)
            rt = self.check(node.right)
            op = node.op
            if op in ("AND", "OR"):
                _boolean(node.left, lt)
                _boolean(node.right, rt)
                return "boolean"
            if op in ("IS NULL", "IS NOT NULL"):
                # 右侧固定为 NULL，任意类型均可判空
                return "boolean"
            if op in ("+", "-", "*", "/", "%"):
                _numeric(node.left, lt, "算术运算")
                _numeric(node.right, rt, "算术运算")
                return "numeric"
            # 比较运算
            if not _comparable(lt, rt):
                raise ExpressionError(
                    f"无法比较 {lt} 与 {rt}", node.pos, node.end
                )
            return "boolean"
        if k == ast.FUNC:
            return self._func(node)
        raise ExpressionError("未知表达式节点", node.pos, node.end)  # pragma: no cover

    def _func(self, node: ast.Node) -> str:
        spec = funcs.get(node.func or "")
        if spec is None:
            raise ExpressionError(
                f"不允许的函数 {node.func!r}，请使用白名单中的函数",
                node.pos,
                node.pos + len(node.func or ""),
            )
        argc = len(node.args)
        if argc < spec.min_arity or (spec.max_arity != -1 and argc > spec.max_arity):
            need = (
                f"{spec.min_arity} 个参数"
                if spec.min_arity == spec.max_arity
                else f"至少 {spec.min_arity} 个参数"
            )
            raise ExpressionError(f"函数 {spec.name} 需要{need}", node.pos, node.end)

        # 特判函数的参数检查
        upper = spec.name
        if upper == "IF":
            ct = self.check(node.args[0])
            _boolean(node.args[0], ct)
            t2 = self.check(node.args[1])
            t3 = self.check(node.args[2])
            if not _comparable(t2, t3) and t2 != "any" and t3 != "any":
                raise ExpressionError(
                    f"IF 的两个结果分支类型不一致：{t2} 与 {t3}",
                    node.pos,
                    node.end,
                )
            return t2 if t2 != "any" else t3
        if upper == "ISNULL":
            self.check(node.args[0])
            return "boolean"
        if upper == "IFNULL":
            t1 = self.check(node.args[0])
            self.check(node.args[1])
            return t1
        if upper == "COALESCE":
            types = [self.check(a) for a in node.args]
            return next((t for t in types if t != "any"), types[0])
        if upper == "CONCAT":
            for a in node.args:
                self.check(a)
            return "text"
        if upper == "TRUNC_DATE":
            self._check_date_unit(node.args[1], funcs.DATE_UNITS)
            dt = self.check(node.args[0])
            if dt not in DATE_FAMILY and dt != "any":
                raise ExpressionError(
                    f"TRUNC_DATE 第一个参数必须是日期，实际为 {dt}",
                    node.args[0].pos,
                    node.args[0].end,
                )
            return "date"
        if upper == "DATE_PART":
            self._check_date_unit(node.args[0], funcs.DATE_UNITS)
            dt = self.check(node.args[1])
            if dt not in DATE_FAMILY and dt != "any":
                raise ExpressionError(
                    f"DATE_PART 第二个参数必须是日期，实际为 {dt}",
                    node.args[1].pos,
                    node.args[1].end,
                )
            return "numeric"
        if upper == "DATE_ADD":
            self._check_date_unit(node.args[1], funcs.DATE_ADD_UNITS)
            dt = self.check(node.args[0])
            nt = self.check(node.args[2])
            if dt not in DATE_FAMILY and dt != "any":
                raise ExpressionError("DATE_ADD 第一个参数必须是日期",
                                      node.args[0].pos, node.args[0].end)
            _numeric(node.args[2], nt, "DATE_ADD 的增量")
            return "date"

        # 通用函数：按模板循环检查参数类型
        for i, arg in enumerate(node.args):
            want = spec.arg_types[min(i, len(spec.arg_types) - 1)]
            got = self.check(arg)
            if want == NUMERIC_FAMILY and got not in NUMERIC_FAMILY and got != "any":
                raise ExpressionError(
                    f"函数 {spec.name} 第 {i + 1} 个参数需要数值，实际为 {got}",
                    arg.pos,
                    arg.end,
                )
            if want == "text" and got != "text" and got != "any":
                raise ExpressionError(
                    f"函数 {spec.name} 第 {i + 1} 个参数需要文本，实际为 {got}",
                    arg.pos,
                    arg.end,
                )
        return spec.return_type

    @staticmethod
    def _check_date_unit(node: ast.Node, allowed: set[str]) -> str:
        if node.kind != ast.LITERAL or not isinstance(node.value, str):
            raise ExpressionError(
                "日期单位必须是固定字符串（如 'year'、'month'、'day'）",
                node.pos,
                node.end,
            )
        if node.value.lower() not in allowed:
            raise ExpressionError(
                f"不支持的日期单位 {node.value!r}，允许：{sorted(allowed)}",
                node.pos,
                node.end,
            )
        return node.value.lower()


def validate_expression(
    expression: str, fields: dict[str, str], *, expect_boolean: bool = False
) -> CompiledExpr:
    """解析 + 语义校验。不生成 SQL、不绑定参数。"""
    node = parse(expression)
    dtype = _Validator(dict(fields)).check(node)
    if expect_boolean:
        _boolean(node, dtype)
    return CompiledExpr(sql="", data_type=dtype, node=node)


# 让 parse 模块能拿到 Token（避免被静态检查工具误判未用导入）
_ = Token

# ---------------------------------------------------------------------------
# SQL 编译
# ---------------------------------------------------------------------------

MAKE_INTERVAL_KEY = {
    "year": "years",
    "month": "months",
    "week": "weeks",
    "day": "days",
    "quarter": "months",
}

DIRECT_FUNCS = {
    "ABS", "ROUND", "FLOOR", "CEIL", "POWER", "MOD", "SQRT",
    "UPPER", "LOWER", "TRIM", "LENGTH", "SUBSTR", "LEFT", "RIGHT",
    "REPLACE", "COALESCE",
}

FieldResolver = Callable[[str], str]
ParamAllocator = Callable[[object, str], str]


def compile_expression(
    expression_or_node: str | ast.Node,
    resolve_field: FieldResolver,
    allocate: ParamAllocator,
) -> str:
    node = parse(expression_or_node) if isinstance(expression_or_node, str) else expression_or_node
    return _emit(node, resolve_field, allocate)


def _emit(node: ast.Node, field: FieldResolver, alloc: ParamAllocator) -> str:
    k = node.kind
    if k == ast.LITERAL:
        if node.value is None:
            return "NULL"
        if isinstance(node.value, bool):
            return "TRUE" if node.value else "FALSE"
        placeholder = alloc(node.value, node.data_type or "text")
        # 数值参与算术时统一提升为 numeric，避免整数字面量被推导成 bigint
        if node.data_type == "integer":
            placeholder = placeholder.replace("::bigint", "::numeric")
        return placeholder
    if k == ast.FIELD:
        return field(node.name)
    if k == ast.UNARY:
        inner = _emit(node.operand, field, alloc)
        if node.op == "NOT":
            return f"(NOT {inner})"
        if node.op == "-":
            return f"(-{inner})"
        return f"({inner})"
    if k == ast.BINARY:
        l = _emit(node.left, field, alloc)
        if node.op in ("IS NULL", "IS NOT NULL"):
            return f"({l} {node.op})"
        r = _emit(node.right, field, alloc)
        op = node.op
        return f"({l} {op} {r})"
    if k == ast.FUNC:
        return _func(node, field, alloc)
    raise ExpressionError("内部错误：无法编译的表达式节点", node.pos, node.end)  # pragma: no cover


def _func(node: ast.Node, field: FieldResolver, alloc: ParamAllocator) -> str:
    name = (node.func or "").upper()
    args = [_emit(a, field, alloc) for a in node.args]

    if name in DIRECT_FUNCS:
        return f"{name.lower()}({', '.join(args)})"
    if name == "CONCAT":
        return f"concat({', '.join(args)})"
    if name == "IF":
        return f"CASE WHEN {args[0]} THEN {args[1]} ELSE {args[2]} END"
    if name == "IFNULL":
        return f"COALESCE({args[0]}, {args[1]})"
    if name == "ISNULL":
        return f"({args[0]} IS NULL)"
    if name == "TRUNC_DATE":
        unit = alloc(node.args[1].value, "text")  # 取值已被白名单约束
        return f"date_trunc({unit}, {args[0]})::date"
    if name == "DATE_PART":
        unit = alloc(node.args[0].value, "text")
        return f"date_part({unit}, {args[1]})"
    if name == "DATE_ADD":
        unit = node.args[1].value.lower()
        key = MAKE_INTERVAL_KEY[unit]
        n = args[2]
        if unit == "quarter":
            n = f"({n}) * 3"
        return f"({args[0]} + make_interval({key} => {n}))::date"
    # 理论不可达（校验阶段已拦截）
    raise ExpressionError(f"不允许的函数 {name}", node.pos, node.end)  # pragma: no cover
