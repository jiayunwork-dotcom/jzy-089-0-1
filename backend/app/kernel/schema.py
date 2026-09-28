"""SQL 翻译内核的建模数据结构（与 FastAPI 路由层解耦）。

模型描述：
- Table：物理表及其列
- Join：两张表之间的关联（可由多对字段组成复合外键）+ 基数（1:1 / 1:N / M:N）
- Dimension：维度（可直接引用列，或给出沙箱表达式），可声明 Hierarchy 钻取路径
- Measure：度量（聚合 + 字段 + 可选过滤条件）
- CalculatedField：计算字段（白名单沙箱表达式，行级）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Cardinality(str, Enum):
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_MANY = "many_to_many"


class DataType(str, Enum):
    TEXT = "text"
    INTEGER = "integer"
    NUMERIC = "numeric"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"


def _check_id(value: str, what: str) -> None:
    if not isinstance(value, str) or not ID_RE.match(value):
        raise ValueError(f"非法{what}: {value!r}（仅允许字母/下划线开头的字母数字下划线）")


@dataclass(frozen=True)
class Column:
    name: str
    data_type: DataType
    primary_key: bool = False

    def __post_init__(self) -> None:
        if not ID_RE.match(self.name):
            raise ValueError(f"非法列名: {self.name!r}")


@dataclass(frozen=True)
class JoinKey:
    left_column: str
    right_column: str


@dataclass
class Join:
    """两张表的关联。

    ``left_table``/``right_table`` 顺序无关，翻译器在生成 JOIN 时按实际需要的方向渲染：
    从 "one" 侧（父）向 "many" 侧（子）渲染为 LEFT JOIN，即左表是父、右表是子。
    """

    id: str
    left_table: str
    right_table: str
    keys: list[JoinKey]
    cardinality: Cardinality

    def other(self, table_id: str) -> str:
        if table_id == self.left_table:
            return self.right_table
        if table_id == self.right_table:
            return self.left_table
        raise KeyError(table_id)

    def child_side(self) -> str:
        """返回 "多" 侧表 id；1:1 时返回 right_table。"""
        if self.cardinality is Cardinality.ONE_TO_MANY:
            return self.right_table
        if self.cardinality is Cardinality.MANY_TO_MANY:
            # M:N 两侧都是多侧，调用方需显式按方向处理；这里给出声明序中的右表
            return self.right_table
        return self.right_table

    def is_fanout_from(self, parent: str) -> bool:
        """从 parent 一侧走过该关联时是否会放大行数。

        1:N 声明为 left=一(父)、right=多(子)：
        - parent == left（从父到子）：放大；
        - parent == right（从子到父）：不放大。
        M:N 两个方向都放大。
        """
        if self.cardinality is Cardinality.MANY_TO_MANY:
            return True
        if self.cardinality is Cardinality.ONE_TO_MANY:
            return parent == self.left_table
        return False

    def validate(self, tables: dict[str, "Table"]) -> None:
        _check_id(self.id, "关联 id")
        for t in (self.left_table, self.right_table):
            if t not in tables:
                raise ValueError(f"关联 {self.id} 引用了不存在的表: {t}")
        if self.left_table == self.right_table:
            raise ValueError(f"关联 {self.id} 不能自关联（当前版本暂不支持）")
        if not self.keys:
            raise ValueError(f"关联 {self.id} 至少需要一对连接字段")
        lt, rt = tables[self.left_table], tables[self.right_table]
        lcols = {c.name for c in lt.columns}
        rcols = {c.name for c in rt.columns}
        for k in self.keys:
            if k.left_column not in lcols:
                raise ValueError(f"关联 {self.id}: 表 {lt.id} 上没有列 {k.left_column}")
            if k.right_column not in rcols:
                raise ValueError(f"关联 {self.id}: 表 {rt.id} 上没有列 {k.right_column}")


@dataclass
class Table:
    id: str
    name: str
    physical_name: str
    columns: list[Column] = field(default_factory=list)

    def col(self, name: str) -> Column:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(f"表 {self.id} 上不存在列 {name}")

    def has(self, name: str) -> bool:
        return any(c.name == name for c in self.columns)

    @property
    def pk_columns(self) -> list[Column]:
        pks = [c for c in self.columns if c.primary_key]
        if not pks:
            raise ValueError(f"表 {self.id} 未声明主键")
        return pks

    @property
    def pk(self) -> Column:
        """单列主键（仅在主键恰好一列时可用）。"""
        pks = self.pk_columns
        if len(pks) != 1:
            raise ValueError(f"表 {self.id} 是复合主键，请使用 pk_columns")
        return pks[0]


@dataclass(frozen=True)
class HierarchyLevel:
    dimension_id: str


@dataclass
class Hierarchy:
    id: str
    name: str
    # 自上而下：最粗 -> 最细
    levels: list[str]  # dimension_id 列表

    def level_index(self, dimension_id: str) -> int:
        try:
            return self.levels.index(dimension_id)
        except ValueError:
            return -1

    def next_level(self, dimension_id: str) -> str | None:
        i = self.level_index(dimension_id)
        if 0 <= i < len(self.levels) - 1:
            return self.levels[i + 1]
        return None


@dataclass
class Dimension:
    id: str
    name: str
    table: str
    column: str | None = None
    # 行级沙箱表达式（可空）；为空时直接使用 column
    expression: str | None = None
    hierarchy_id: str | None = None
    data_type: DataType = DataType.TEXT

    @property
    def is_table_column(self) -> bool:
        return self.expression is None

    @property
    def ref(self) -> tuple[str, str]:
        """(table_id, column_name)；表达式维度的校验由沙箱编译阶段完成。"""
        if self.column is None:
            raise ValueError(f"维度 {self.id} 既没有列也没有表达式")
        return (self.table, self.column)


class AggFunc(str, Enum):
    SUM = "sum"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


@dataclass
class Measure:
    id: str
    name: str
    table: str
    column: str | None  # 与 expression 二选一；COUNT(*) 语义用主键列
    agg: AggFunc
    # 可选行级沙箱表达式（引用度量表字段及其多对一维表字段）
    expression: str | None = None
    # 可选度量过滤（沙箱布尔表达式）
    filter_expression: str | None = None

    def pk_or_column(self, model: "Model") -> str:
        """count_distinct 未指定列时使用表主键。"""
        return self.column or model.tables[self.table].pk.name


@dataclass
class CalculatedField:
    id: str
    name: str
    table: str
    expression: str
    data_type: DataType = DataType.NUMERIC


# ---------------------------------------------------------------------------
# 过滤条件（拖拽投放到"筛选"区的意图）
# ---------------------------------------------------------------------------

class FilterOp(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    BETWEEN = "between"
    IS_NULL = "is_null"
    NOT_NULL = "not_null"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    CONTAINS = "contains"


@dataclass
class Filter:
    """筛选条件。

    field 可以是 "维度 id"、"度量 id" 或 "计算字段 id"。
    度量筛选翻译为 HAVING；其余翻译为 WHERE。
    """

    field_id: str
    op: FilterOp
    value: Any = None  # 标量 / list[标量] / [lo, hi]；is_null 时忽略


@dataclass
class Model:
    tables: dict[str, Table] = field(default_factory=dict)
    joins: dict[str, Join] = field(default_factory=dict)
    dimensions: dict[str, Dimension] = field(default_factory=dict)
    hierarchies: dict[str, Hierarchy] = field(default_factory=dict)
    measures: dict[str, Measure] = field(default_factory=dict)
    calculated_fields: dict[str, CalculatedField] = field(default_factory=dict)

    # -- 邻接表（无向图） ---------------------------------------------------
    def neighbors(self, table_id: str) -> list[tuple[Join, str]]:
        out: list[tuple[Join, str]] = []
        for j in self.joins.values():
            if j.left_table == table_id:
                out.append((j, j.right_table))
            elif j.right_table == table_id:
                out.append((j, j.left_table))
        return sorted(out, key=lambda x: x[0].id)

    def field_table(self, field_id: str) -> str:
        if field_id in self.dimensions:
            return self.dimensions[field_id].table
        if field_id in self.measures:
            return self.measures[field_id].table
        if field_id in self.calculated_fields:
            return self.calculated_fields[field_id].table
        raise KeyError(f"模型中不存在字段: {field_id}")

    def is_measure_filter(self, field_id: str) -> bool:
        return field_id in self.measures

    def validate(self) -> None:
        """结构完整性校验。表达式校验在沙箱模块中单独做（需要保存时即校验）。"""
        for t in self.tables.values():
            _check_id(t.id, "表 id")
            for c in t.columns:
                _check_id(c.name, "列名")
        seen_pairs: set[frozenset[str]] = set()
        for j in self.joins.values():
            j.validate(self.tables)
            pair = frozenset({j.left_table, j.right_table})
            if pair in seen_pairs:
                raise ValueError(f"表 {j.left_table} 与 {j.right_table} 之间存在重复关联")
            seen_pairs.add(pair)
        for d in self.dimensions.values():
            _check_id(d.id, "维度 id")
            if d.table not in self.tables:
                raise ValueError(f"维度 {d.id} 引用不存在的表 {d.table}")
            if d.expression is None and not self.tables[d.table].has(d.column or ""):
                raise ValueError(f"维度 {d.id} 引用不存在的列 {d.column}")
        for h in self.hierarchies.values():
            _check_id(h.id, "层级 id")
            if len(h.levels) < 2:
                raise ValueError(f"层级 {h.id} 至少需要两层")
            if len(set(h.levels)) != len(h.levels):
                raise ValueError(f"层级 {h.id} 的层级维度不能重复")
            prev_table = None
            for did in h.levels:
                d = self.dimensions.get(did)
                if d is None:
                    raise ValueError(f"层级 {h.id} 引用不存在的维度 {did}")
                if d.hierarchy_id != h.id:
                    raise ValueError(f"维度 {did} 未声明属于层级 {h.id}")
                if prev_table is not None and d.table != prev_table:
                    raise ValueError(f"层级 {h.id} 的维度必须来自同一张表")
                prev_table = d.table
        for m in self.measures.values():
            _check_id(m.id, "度量 id")
            if m.table not in self.tables:
                raise ValueError(f"度量 {m.id} 引用不存在的表 {m.table}")
            if m.expression is None and (m.column is None or not self.tables[m.table].has(m.column)):
                raise ValueError(f"度量 {m.id} 必须引用存在的列或给出表达式")
        for c in self.calculated_fields.values():
            _check_id(c.id, "计算字段 id")
            if c.table not in self.tables:
                raise ValueError(f"计算字段 {c.id} 引用不存在的表 {c.table}")
            if not c.expression or not c.expression.strip():
                raise ValueError(f"计算字段 {c.id} 的表达式为空")

    # -- 序列化 -------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": [
                {
                    "id": t.id, "name": t.name, "physical_name": t.physical_name,
                    "columns": [
                        {"name": c.name, "data_type": c.data_type.value,
                         "primary_key": c.primary_key}
                        for c in t.columns
                    ],
                }
                for t in self.tables.values()
            ],
            "joins": [
                {
                    "id": j.id,
                    "left_table": j.left_table,
                    "right_table": j.right_table,
                    "keys": [{"left_column": k.left_column,
                              "right_column": k.right_column} for k in j.keys],
                    "cardinality": j.cardinality.value,
                }
                for j in self.joins.values()
            ],
            "dimensions": [
                {
                    "id": d.id, "name": d.name, "table": d.table,
                    "column": d.column, "expression": d.expression,
                    "hierarchy_id": d.hierarchy_id,
                    "data_type": d.data_type.value,
                }
                for d in self.dimensions.values()
            ],
            "hierarchies": [
                {"id": h.id, "name": h.name, "levels": list(h.levels)}
                for h in self.hierarchies.values()
            ],
            "measures": [
                {
                    "id": m.id, "name": m.name, "table": m.table,
                    "column": m.column, "agg": m.agg.value,
                    "expression": m.expression,
                    "filter_expression": m.filter_expression,
                }
                for m in self.measures.values()
            ],
            "calculated_fields": [
                {"id": c.id, "name": c.name, "table": c.table,
                 "expression": c.expression, "data_type": c.data_type.value}
                for c in self.calculated_fields.values()
            ],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Model":
        m = cls()
        for td in raw.get("tables", []):
            cols = [
                Column(
                    name=c["name"],
                    data_type=DataType(c["data_type"]),
                    primary_key=c.get("primary_key", False),
                )
                for c in td.get("columns", [])
            ]
            m.tables[td["id"]] = Table(
                id=td["id"],
                name=td["name"],
                physical_name=td["physical_name"],
                columns=cols,
            )
        for jd in raw.get("joins", []):
            m.joins[jd["id"]] = Join(
                id=jd["id"],
                left_table=jd["left_table"],
                right_table=jd["right_table"],
                keys=[JoinKey(**k) for k in jd["keys"]],
                cardinality=Cardinality(jd["cardinality"]),
            )
        for dd in raw.get("dimensions", []):
            m.dimensions[dd["id"]] = Dimension(
                id=dd["id"],
                name=dd["name"],
                table=dd["table"],
                column=dd.get("column"),
                expression=dd.get("expression"),
                hierarchy_id=dd.get("hierarchy_id"),
                data_type=DataType(dd.get("data_type", "text")),
            )
        for hd in raw.get("hierarchies", []):
            m.hierarchies[hd["id"]] = Hierarchy(
                id=hd["id"], name=hd["name"], levels=list(hd["levels"])
            )
        for md in raw.get("measures", []):
            m.measures[md["id"]] = Measure(
                id=md["id"],
                name=md["name"],
                table=md["table"],
                column=md.get("column"),
                agg=AggFunc(md["agg"]),
                expression=md.get("expression"),
                filter_expression=md.get("filter_expression"),
            )
        for cd in raw.get("calculated_fields", []):
            m.calculated_fields[cd["id"]] = CalculatedField(
                id=cd["id"],
                name=cd["name"],
                table=cd["table"],
                expression=cd["expression"],
                data_type=DataType(cd.get("data_type", "numeric")),
            )
        return m
