"""拖拽意图（查询规格）的内核数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schema import Filter, FilterOp


@dataclass
class ValueItem:
    """数值区条目：引用度量，或把计算字段按给定聚合拖入（默认 sum）。"""

    measure_id: str | None = None
    field_id: str | None = None
    agg: str = "sum"  # 仅 field_id 为计算字段时生效

    @property
    def key(self) -> str:
        return self.measure_id or self.field_id or "?"


@dataclass
class SortItem:
    index: int  # 结果列序号，1-based
    dir: str    # asc / desc


@dataclass
class QuerySpec:
    rows: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    values: list[ValueItem] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    sorts: list[SortItem] = field(default_factory=list)
    limit: int | None = None

    @property
    def group_ids(self) -> list[str]:
        # 保持投放顺序，去重（同一维度同时在行/列只分组一次）
        seen: set[str] = set()
        out: list[str] = []
        for i in self.rows + self.columns:
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "QuerySpec":
        filters: list[Filter] = []
        for f in raw.get("filters", []):
            filters.append(
                Filter(
                    field_id=f["field_id"],
                    op=FilterOp(f["op"]),
                    value=f.get("value"),
                )
            )
        values = [
            ValueItem(
                measure_id=v.get("measure_id"),
                field_id=v.get("field_id"),
                agg=v.get("agg", "sum"),
            )
            for v in raw.get("values", [])
        ]
        sorts = [
            SortItem(index=int(s["index"]), dir=str(s.get("dir", "asc")).lower())
            for s in raw.get("sorts", [])
        ]
        for s in sorts:
            if s.dir not in ("asc", "desc"):
                raise ValueError(f"排序方向非法: {s.dir}")
        limit = raw.get("limit")
        return cls(
            rows=list(raw.get("rows", [])),
            columns=list(raw.get("columns", [])),
            values=values,
            filters=filters,
            sorts=sorts,
            limit=None if limit is None else int(limit),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": list(self.rows),
            "columns": list(self.columns),
            "values": [
                {"measure_id": v.measure_id, "field_id": v.field_id, "agg": v.agg}
                for v in self.values
            ],
            "filters": [
                {"field_id": f.field_id, "op": f.op.value, "value": f.value}
                for f in self.filters
            ],
            "sorts": [{"index": s.index, "dir": s.dir} for s in self.sorts],
            "limit": self.limit,
        }


@dataclass
class TranslationResult:
    sql: str
    params: list[Any]
    result_columns: list[dict[str, Any]]  # [{key,label,kind,data_type}]
