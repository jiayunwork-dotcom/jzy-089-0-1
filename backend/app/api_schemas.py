"""HTTP 请求/响应模型（pydantic）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ValueItemIn(BaseModel):
    measure_id: str | None = None
    field_id: str | None = None
    agg: str = "sum"


class SortItemIn(BaseModel):
    index: int
    dir: str = "asc"


class FilterIn(BaseModel):
    field_id: str
    op: str
    value: Any = None


class QueryIn(BaseModel):
    rows: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    values: list[ValueItemIn] = Field(default_factory=list)
    filters: list[FilterIn] = Field(default_factory=list)
    sorts: list[SortItemIn] = Field(default_factory=list)
    limit: int | None = None


class DrillIn(BaseModel):
    spec: QueryIn
    dimension_id: str


class ExpressionIn(BaseModel):
    table: str
    expression: str
    expect_boolean: bool = False
