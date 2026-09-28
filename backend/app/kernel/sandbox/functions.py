"""白名单函数注册表。

所有函数名大小写不敏感；每个函数声明：
- arity：允许的参数个数（None 表示变参，但必须 >= min_arity）
- 类型推断：根据参数类型给出返回类型
- 编译为 PostgreSQL 的规则在 compiler.py 中
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

NUMERIC = "numeric"
TEXT = "text"
DATE = "date"
BOOLEAN = "boolean"
ANY = "any"


@dataclass(frozen=True)
class FuncSpec:
    name: str
    min_arity: int
    max_arity: int  # -1 表示无上限
    arg_types: tuple[str, ...]  # 形参类型（ANY 表示任意），变参时为循环模板
    return_type: str
    # 部分函数（如 IF/IFNULL/DATE 系列）在编译器中特判
    special: bool = False


def _v(name: str, argc: int, arg: str, ret: str, special: bool = False) -> FuncSpec:
    return FuncSpec(name, argc, argc, tuple([arg] * argc), ret, special)


# @formatter:off
REGISTRY: dict[str, FuncSpec] = {}
for _f in [
    # 数学
    _v("ABS", 1, NUMERIC, NUMERIC), _v("ROUND", 2, NUMERIC, NUMERIC),
    _v("FLOOR", 1, NUMERIC, NUMERIC), _v("CEIL", 1, NUMERIC, NUMERIC),
    _v("POWER", 2, NUMERIC, NUMERIC), _v("MOD", 2, NUMERIC, NUMERIC),
    _v("SQRT", 1, NUMERIC, NUMERIC),
    # 聚合类函数不允许出现在行级表达式里（度量有独立的聚合声明）
    # 字符串
    FuncSpec("CONCAT", 1, -1, (ANY,), TEXT, True),
    _v("UPPER", 1, TEXT, TEXT), _v("LOWER", 1, TEXT, TEXT),
    _v("TRIM", 1, TEXT, TEXT),
    FuncSpec("SUBSTR", 3, 3, (TEXT, NUMERIC, NUMERIC), TEXT),
    FuncSpec("LEFT", 2, 2, (TEXT, NUMERIC), TEXT),
    FuncSpec("RIGHT", 2, 2, (TEXT, NUMERIC), TEXT),
    FuncSpec("REPLACE", 3, 3, (TEXT, TEXT, TEXT), TEXT),
    _v("LENGTH", 1, TEXT, NUMERIC),
    # 日期（单位仅允许固定白名单字符串，见 parser 的字面量校验）
    FuncSpec("TRUNC_DATE", 2, 2, (DATE, TEXT), DATE, True),
    FuncSpec("DATE_PART", 2, 2, (TEXT, DATE), NUMERIC, True),
    FuncSpec("DATE_ADD", 3, 3, (DATE, TEXT, NUMERIC), DATE, True),
    # 条件/空值
    FuncSpec("IF", 3, 3, (BOOLEAN, ANY, ANY), ANY, True),
    FuncSpec("IFNULL", 2, 2, (ANY, ANY), ANY, True),
    FuncSpec("COALESCE", 2, -1, (ANY,), ANY, True),
    _v("ISNULL", 1, ANY, BOOLEAN, True),
]:
    REGISTRY[_f.name] = _f
# @formatter:on

# DATE_PART / TRUNC_DATE 允许的时间单位白名单
DATE_UNITS = {
    "year", "quarter", "month", "week", "day",
    "hour", "minute", "second",
}
DATE_ADD_UNITS = {"year", "quarter", "month", "week", "day"}


def get(name: str) -> FuncSpec | None:
    return REGISTRY.get(name.upper())
