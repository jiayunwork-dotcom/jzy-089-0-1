"""Semantic modeling layer.

The *model* is everything the SQL translator knows about the physical
database: tables/columns, join relationships, dimensions with drill
hierarchies, aggregated measures and sandboxed calculated fields.

Only structural declarations live here. SQL generation lives in
``app.translator`` and expression parsing in ``app.sandbox``.
"""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .errors import ValidationError

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Allowed values are a subset of the types understood by the expression
# sandbox and by the UI filter editors.
ColumnType = Literal["string", "integer", "number", "boolean", "date", "datetime"]


def validate_identifier(value: str, what: str = "identifier") -> str:
    """Identifiers emitted into SQL are always quoted, but keeping to
    ``[A-Za-z_][A-Za-z0-9_]*`` avoids a whole class of quoting/normalisation
    pitfalls across Postgres and generated JSON."""
    if not isinstance(value, str) or not IDENT_RE.match(value):
        raise ValidationError(
            f"Illegal {what} {value!r}: use letters, digits and underscore, "
            "starting with a letter or underscore"
        )
    return value


class Cardinality(str, Enum):
    one_to_one = "one_to_one"
    one_to_many = "one_to_many"
    many_to_many = "many_to_many"


class Column(BaseModel):
    name: str
    type: ColumnType = "string"
    description: Optional[str] = None


class Table(BaseModel):
    id: str
    db_name: str = Field(description="Physical table name in Postgres")
    label: str
    columns: list[Column] = Field(default_factory=list)

    def column(self, name: str) -> Column:
        for col in self.columns:
            if col.name == name:
                return col
        raise ValidationError(f"Table {self.id!r} has no column {name!r}")


class Join(BaseModel):
    """Declared relationship between two tables.

    ``cardinality`` is read from the perspective of
    ``left_table`` -> ``right_table``: ``one_to_many`` means one row on the
    left matches many rows on the right (right is the fan-out / "many" side).
    The translator treats the edge as undirected for path finding and
    derives fan-out direction from this declaration.
    """

    id: str
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    cardinality: Cardinality

    def endpoint(self, table_id: str) -> tuple[str, str, bool]:
        """Return ``(other_table, other_column, other_is_many_side)`` for a
        traversal *toward* the other table starting at ``table_id``.

        Direction matters: in a 1:N edge the right table is the "many"
        side only when arriving there from the left. Traversing back from
        right to left lands on the "one" side and never fans out.
        """
        if table_id == self.left_table:
            other_is_many = self.cardinality == Cardinality.one_to_many
            return self.right_table, self.right_column, other_is_many
        if table_id == self.right_table:
            return self.left_table, self.left_column, False
        raise ValidationError(f"Join {self.id!r} does not touch table {table_id!r}")

    def fanout_from(self, table_id: str) -> bool:
        """True if traversing this edge away from ``table_id`` lands on the
        "many" side, i.e. one row of ``table_id`` fans out to several rows."""
        _, _, other_is_many = self.endpoint(table_id)
        return other_is_many

    def predicate(self, left_alias: str, right_alias: str) -> str:
        return (
            f'"{left_alias}"."{self.left_column}" = '
            f'"{right_alias}"."{self.right_column}"'
        )


class Level(BaseModel):
    """One step of a drill hierarchy."""

    id: str
    label: str
    table: str
    column: str
    column_type: ColumnType = "string"
    expression: Optional[str] = Field(
        default=None,
        description="Optional sandboxed expression for derived levels such "
        "as YEAR(order_date); defaults to the raw column.",
    )


class Hierarchy(BaseModel):
    id: str
    label: str
    levels: list[Level] = Field(description="Coarse -> fine order, e.g. year..day")


class Dimension(BaseModel):
    id: str
    label: str
    table: str
    column: str
    column_type: ColumnType = "string"
    hierarchy_id: Optional[str] = Field(
        default=None, description="If set, this dimension represents one level "
        "of a hierarchy (the UI shows drill up/down)."
    )


class MeasureFilter(BaseModel):
    """Filter folded into the measure's own WHERE clause (CASE-free design)."""

    table: str
    column: str
    op: Literal["eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "between"]
    values: list = Field(default_factory=list)


class Measure(BaseModel):
    id: str
    label: str
    table: str
    column: Optional[str] = Field(
        default=None,
        description="Numeric/date column; may be null for count_distinct(*) "
        "style measures (use count_distinct on the key column instead).",
    )
    aggregation: Literal[
        "sum", "count", "count_distinct", "avg", "min", "max"
    ]
    filter: Optional[MeasureFilter] = None
    expression: Optional[str] = Field(
        default=None,
        description="Optional sandboxed expression evaluated per row "
        "before aggregation (the measure aggregates the expression result).",
    )


class CalculatedField(BaseModel):
    """A reusable sandboxed expression bound to one table."""

    id: str
    label: str
    table: str
    expression: str
    result_type: ColumnType = "number"


class Model(BaseModel):
    name: str = "sample"
    label: Optional[str] = None
    tables: list[Table] = Field(default_factory=list)
    joins: list[Join] = Field(default_factory=list)
    dimensions: list[Dimension] = Field(default_factory=list)
    hierarchies: list[Hierarchy] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)
    calculated_fields: list[CalculatedField] = Field(default_factory=list)

    # --- convenience lookups -------------------------------------------------
    def table(self, table_id: str) -> Table:
        for t in self.tables:
            if t.id == table_id:
                return t
        raise ValidationError(f"Unknown table id {table_id!r}")

    def join(self, join_id: str) -> Join:
        for j in self.joins:
            if j.id == join_id:
                return j
        raise ValidationError(f"Unknown join id {join_id!r}")

    def dimension(self, dim_id: str) -> Dimension:
        for d in self.dimensions:
            if d.id == dim_id:
                return d
        raise ValidationError(f"Unknown dimension id {dim_id!r}")

    def hierarchy(self, hierarchy_id: str) -> Hierarchy:
        for h in self.hierarchies:
            if h.id == hierarchy_id:
                return h
        raise ValidationError(f"Unknown hierarchy id {hierarchy_id!r}")

    def measure(self, measure_id: str) -> Measure:
        for m in self.measures:
            if m.id == measure_id:
                return m
        raise ValidationError(f"Unknown measure id {measure_id!r}")

    def calculated(self, calc_id: str) -> CalculatedField:
        for c in self.calculated_fields:
            if c.id == calc_id:
                return c
        raise ValidationError(f"Unknown calculated field id {calc_id!r}")

    def field(self, field_id: str):
        """Resolve any droppable object id (dimension / measure / calc)."""
        for d in self.dimensions:
            if d.id == field_id:
                return d
        for m in self.measures:
            if m.id == field_id:
                return m
        for c in self.calculated_fields:
            if c.id == field_id:
                return c
        raise ValidationError(f"Unknown field id {field_id!r}")

    def joins_of(self, table_id: str) -> list[tuple[Join, str]]:
        """All joins touching ``table_id`` as ``(join, other_table)``."""
        out: list[tuple[Join, str]] = []
        for j in self.joins:
            if j.left_table == table_id:
                out.append((j, j.right_table))
            elif j.right_table == table_id:
                out.append((j, j.left_table))
        return out

    # --- persistence ----------------------------------------------------------
    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "Model":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"Stored model is not valid JSON: {exc}")
        return cls.model_validate(data)


def ensure_unique(ids: list[str], what: str) -> None:
    seen: set[str] = set()
    for ident in ids:
        validate_identifier(ident, what)
        if ident in seen:
            raise ValidationError(f"Duplicate {what} id {ident!r}")
        seen.add(ident)


def validate_model(model: Model) -> Model:
    """Structural validation: every reference points at something that
    exists, ids are unique, join endpoints/columns line up. Expression
    validation happens in the sandbox layer (see ``api`` on save)."""
    ensure_unique([t.id for t in model.tables], "table")
    ensure_unique([j.id for j in model.joins], "join")
    ensure_unique([d.id for d in model.dimensions], "dimension")
    ensure_unique([h.id for h in model.hierarchies], "hierarchy")
    ensure_unique([m.id for m in model.measures], "measure")
    ensure_unique([c.id for c in model.calculated_fields], "calculated field")

    for t in model.tables:
        validate_identifier(t.db_name, "physical table name")
        ensure_unique([c.name for c in t.columns], f"column of {t.id}")

    table_ids = {t.id for t in model.tables}
    for j in model.joins:
        if j.left_table not in table_ids or j.right_table not in table_ids:
            raise ValidationError(f"Join {j.id!r} references an unknown table")
        if j.left_table == j.right_table:
            raise ValidationError(
                f"Join {j.id!r} is a self-join, which is not supported"
            )
        lt, rt = model.table(j.left_table), model.table(j.right_table)
        lt.column(j.left_column)
        rt.column(j.right_column)

    for d in model.dimensions:
        model.table(d.table).column(d.column)
        if d.hierarchy_id is not None:
            model.hierarchy(d.hierarchy_id)
    for h in model.hierarchies:
        if not h.levels:
            raise ValidationError(f"Hierarchy {h.id!r} needs at least one level")
        level_ids = [lvl.id for lvl in h.levels]
        ensure_unique(level_ids, f"level of {h.id}")
        for lvl in h.levels:
            model.table(lvl.table).column(lvl.column)
    for m in model.measures:
        model.table(m.table)
        if m.column is not None:
            model.table(m.table).column(m.column)
        if m.filter is not None:
            model.table(m.filter.table).column(m.filter.column)
    for c in model.calculated_fields:
        model.table(c.table)

    # Joins between the same pair of tables would make the path ambiguous.
    pairs: set[frozenset[str]] = set()
    for j in model.joins:
        key = frozenset((j.left_table, j.right_table))
        if key in pairs:
            raise ValidationError(
                f"Multiple joins declared between {sorted(key)}; the graph "
                "must have at most one edge per table pair"
            )
        pairs.add(key)
    return model
