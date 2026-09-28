"""SQL 翻译内核（与 FastAPI / HTTP 无关，可独立单测）。"""
from __future__ import annotations

from .planner import PlanningError, build_plan, shortest_path, spanning_tree
from .query import QuerySpec, SortItem, TranslationResult, ValueItem
from .renderer import translate
from .schema import (
    AggFunc,
    CalculatedField,
    Cardinality,
    Column,
    DataType,
    Dimension,
    Filter,
    FilterOp,
    Hierarchy,
    Join,
    JoinKey,
    Measure,
    Model,
    Table,
)


def drill_down(model: Model, spec: QuerySpec, dimension_id: str) -> QuerySpec:
    """钻取：若维度属于某层级，则在分组里追加下一层（更细粒度）。

    不变量：行/列/数值/筛选的其它内容保持不变，仅分组粒度变细。
    """
    dim = model.dimensions.get(dimension_id)
    if dim is None or dim.hierarchy_id is None:
        raise PlanningError(f"维度 {dimension_id} 未声明层级，无法钻取")
    hierarchy = model.hierarchies[dim.hierarchy_id]
    nxt = hierarchy.next_level(dimension_id)
    if nxt is None:
        raise PlanningError(f"维度 {dimension_id} 已是层级 {hierarchy.name} 的最细一层")

    # 下一层默认跟随当前维度所在的轴（行优先）；追加在该轴末尾以保持稳定
    new = QuerySpec.from_dict(spec.to_dict())
    if dimension_id in new.rows:
        if nxt not in new.rows:
            new.rows.append(nxt)
    elif dimension_id in new.columns:
        if nxt not in new.columns:
            new.columns.append(nxt)
    else:
        # 当前维度未直接投放（可能来自层级中间层的前序钻取）：默认加在行上
        new.rows.append(nxt)
    return new


__all__ = [
    "translate",
    "build_plan",
    "spanning_tree",
    "shortest_path",
    "drill_down",
    "PlanningError",
    "QuerySpec",
    "ValueItem",
    "SortItem",
    "TranslationResult",
    "Model",
    "Table",
    "Column",
    "Join",
    "JoinKey",
    "Cardinality",
    "Dimension",
    "Hierarchy",
    "Measure",
    "AggFunc",
    "CalculatedField",
    "DataType",
    "Filter",
    "FilterOp",
]
