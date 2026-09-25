"""Drag-and-drop query intent DTOs (the translator's input contract)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

FilterOp = Literal[
    "eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte",
    "between", "is_null", "not_null",
]


class FilterIntent(BaseModel):
    """A filter dropped in the filter well.

    ``field_id`` may reference a dimension or a calculated field.
    Date/number ranges map to ``between``; dropdowns map to ``in``.
    """

    field_id: str
    op: FilterOp
    values: list = Field(default_factory=list)


class GroupIntent(BaseModel):
    """A dimension dropped on rows/columns.

    ``hierarchy_level`` is the currently shown level id; drilling down
    replaces this entry with the next level (see translator.apply_drill).
    """

    field_id: str
    hierarchy_level: Optional[str] = None


class SortIntent(BaseModel):
    field_id: str
    direction: Literal["asc", "desc"] = "asc"


class QueryIntent(BaseModel):
    # Both rows and columns are grouping axes; the UI may pivot later.
    rows: list[GroupIntent] = Field(default_factory=list)
    columns: list[GroupIntent] = Field(default_factory=list)
    measures: list[str] = Field(default_factory=list)
    filters: list[FilterIntent] = Field(default_factory=list)
    # Measure-level post-aggregation filters (HAVING on returned measures).
    measure_filters: list[FilterIntent] = Field(default_factory=list)
    sort: list[SortIntent] = Field(default_factory=list)
    limit: Optional[int] = Field(default=1000, ge=1, le=100000)
    offset: int = Field(default=0, ge=0)


class DrillRequest(BaseModel):
    intent: QueryIntent
    hierarchy_id: str
    # "down" replaces the current level with the next finer one,
    # "up" moves one level coarser.
    direction: Literal["down", "up"] = "down"


class ValidateIntentRequest(BaseModel):
    intent: QueryIntent
