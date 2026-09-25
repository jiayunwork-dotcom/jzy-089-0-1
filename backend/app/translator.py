"""SQL translation kernel.

Given a :class:`~app.intents.QueryIntent` (which dimensions are grouped,
which measures are aggregated, which filters apply) and a semantic
:class:`~app.modeling.Model`, derive the required join paths and emit
parameterised, read-only PostgreSQL.

Anti fan-out design
-------------------
Every measure is aggregated **inside its own CTE at the requested group
grain before any assembly join**. The anchor of a measure CTE is the
measure's own (fact) table. Dimension tables are reached only through
edges that are *not* fan-out edges from the current row, so a fact row is
never multiplied before ``SUM``/``COUNT``. Filters that can only be
reached through fan-out / many-to-many edges are applied with correlated
``EXISTS`` subqueries instead of joins, which likewise never multiplies
rows. The per-measure CTEs are then joined to the distinct dimension grid
on the group keys, so two independent child tables (payments *and* order
lines off orders) can never cross-multiply each other.

Grouping *along* a fan-out edge (e.g. "sales by payment method", where one
order has several payments) has no unambiguous single SQL semantics and is
rejected with a clear error instead of silently returning inflated totals.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .errors import TranslationError
from .intents import DrillRequest, FilterIntent, GroupIntent, QueryIntent, SortIntent
from .modeling import (
    Cardinality,
    Join,
    Model,
    Table,
)
from .sandbox import compile_expression

# ---------------------------------------------------------------------------
# Read-only / safety guards
# ---------------------------------------------------------------------------

_FORBIDDEN_SQL_PATTERNS = re.compile(
    r"(;)"                        # statement terminators (no stacked queries)
    r"|(--)"                     # line comments
    r"|(/\*)"                    # block comments
    r"|\b(insert|update|delete|merge|drop|alter|create|truncate|grant|"
    r"revoke|vacuum|copy|call|do|commit|rollback|set\s+session|explain)\b",
    re.IGNORECASE,
)


def assert_select_only(sql: str) -> None:
    """Defence-in-depth: the generator only emits SELECT/WITH, but never
    let anything else (or a stacked second statement) through."""
    stripped = sql.lstrip()
    if not re.match(r"^(SELECT|WITH)\b", stripped, re.IGNORECASE):
        raise TranslationError("Only SELECT queries are allowed")
    hit = _FORBIDDEN_SQL_PATTERNS.search(sql)
    if hit:
        raise TranslationError(
            f"Generated SQL contains forbidden token near "
            f"{hit.group(0)!r}; query rejected"
        )


def qident(name: str) -> str:
    """Quote an identifier that has already passed the ident whitelist."""
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        # Should never happen: model validation gates identifiers earlier.
        raise TranslationError(f"Refusing to quote unsafe identifier {name!r}")
    return f'"{name}"'


# ---------------------------------------------------------------------------
# Resolved drag objects
# ---------------------------------------------------------------------------

@dataclass
class ResolvedGroup:
    out_name: str           # output JSON key / SQL alias = current field id
    label: str
    table_id: str
    column_name: Optional[str]      # plain column, or None for calc
    calc_id: Optional[str]
    column_type: str
    level_expression: Optional[str] = None
    hierarchy_id: Optional[str] = None
    level_id: Optional[str] = None
    level_index: int = 0
    can_drill_down: bool = False
    can_drill_up: bool = False

    def render(self, alias: str, params: list, model: Model) -> str:
        if self.calc_id is not None:
            calc = model.calculated(self.calc_id)
            compiled = compile_expression(calc.expression, calc.table, model)
            return compiled.render(alias, params)
        if self.level_expression is not None:
            compiled = compile_expression(
                self.level_expression, self.table_id, model
            )
            return compiled.render(alias, params)
        return f"{qident(alias)}.{qident(self.column_name)}"


@dataclass
class ResolvedFilter:
    out_name: str
    table_id: str
    column_name: Optional[str]
    calc_id: Optional[str]
    column_type: str
    op: str
    values: list
    is_measure_filter: bool = False

    def render_expr(self, alias: str, params: list, model: Model) -> str:
        if self.calc_id is not None:
            calc = model.calculated(self.calc_id)
            left = compile_expression(calc.expression, calc.table, model).render(
                alias, params
            )
        else:
            left = f"{qident(alias)}.{qident(self.column_name)}"
        op, vals = self.op, self.values
        if op == "is_null":
            return f"({left} IS NULL)"
        if op == "not_null":
            return f"({left} IS NOT NULL)"
        if op in ("in", "not_in"):
            placeholders = ", ".join(_push(v, params) for v in vals)
            keyword = "NOT IN" if op == "not_in" else "IN"
            return f"({left} {keyword} ({placeholders}))"
        if op == "between":
            lo, hi = (_push(v, params) for v in vals)
            return f"({left} BETWEEN {lo} AND {hi})"
        sym = {
            "eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<",
            "lte": "<=",
        }[op]
        return f"({left} {sym} {_push(vals[0], params)})"


@dataclass
class ResolvedMeasure:
    out_name: str
    label: str
    table_id: str
    column_name: Optional[str]
    calc_expression: Optional[str]
    aggregation: str
    measure_filter: Optional[ResolvedFilter]
    column_type: str = "number"

    def render_value(self, alias: str, params: list, model: Model) -> str:
        """The per-row value being aggregated."""
        if self.calc_expression is not None:
            compiled = compile_expression(
                self.calc_expression, self.table_id, model
            )
            return compiled.render(alias, params)
        if self.column_name is not None:
            return f"{qident(alias)}.{qident(self.column_name)}"
        if self.aggregation == "count":
            return "*"
        raise TranslationError(
            f"Measure {self.out_name!r}: aggregation {self.aggregation} "
            "requires a column or expression"
        )

    def render_aggregate(self, alias: str, params: list, model: Model) -> str:
        target = self.render_value(alias, params, model)
        agg = self.aggregation
        if agg == "count":
            inner = "*" if target == "*" else f"{target}"
            return f"COUNT({inner})"
        if agg == "count_distinct":
            if target == "*":
                raise TranslationError(
                    f"Measure {self.out_name!r}: count_distinct needs a column"
                )
            return f"COUNT(DISTINCT {target})"
        if agg == "avg":
            return f"AVG(({target})::numeric)" if target != "*" else ""
        if agg == "sum":
            return f"SUM({target})"
        if agg == "min":
            return f"MIN({target})"
        if agg == "max":
            return f"MAX({target})"
        raise TranslationError(f"Unsupported aggregation {agg!r}")

    @property
    def coalesce_to_zero(self) -> bool:
        return self.aggregation in ("sum", "count", "count_distinct")


# ---------------------------------------------------------------------------
# Join path planning
# ---------------------------------------------------------------------------

@dataclass
class EdgeStep:
    join: Join
    from_table: str
    to_table: str
    fanout: bool


@dataclass
class PathPlan:
    anchor: str
    # BFS visitation: table -> step used to reach it
    steps: dict[str, EdgeStep] = field(default_factory=dict)

    @property
    def tables(self) -> list[str]:
        return [self.anchor] + list(self.steps.keys())

    def ordered_tables(self) -> list[str]:
        """Anchor first, then BFS visitation order (parents before children)."""
        return self.tables


def _reachable(model: Model, start: str, *, allow_fanout: bool) -> set[str]:
    """All tables reachable from ``start`` under the traversal rules."""
    seen = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop(0)
        for join, other in model.joins_of(current):
            if other in seen:
                continue
            fanout = join.fanout_from(current)
            if not allow_fanout and (
                fanout or join.cardinality == Cardinality.many_to_many
            ):
                continue
            seen.add(other)
            frontier.append(other)
    return seen


def plan_paths(
    model: Model, anchor: str, required: set[str], *, fanout_safe: bool
) -> PathPlan:
    """Breadth-first search from ``anchor`` covering ``required`` tables.

    ``fanout_safe=True`` refuses to traverse edges that fan out from the
    current row (1:N "many" side, and all many-to-many edges). BFS gives a
    shortest-path, deterministic (model-declaration-order) tree with no
    table visited twice — the generated JOIN chain therefore connects every
    referenced table exactly once.

    Only tables that can still lead to a *required* table are expanded, so
    irrelevant side branches of the graph are never joined.
    """
    if anchor not in {t.id for t in model.tables}:
        raise TranslationError(f"Anchor table {anchor!r} does not exist")
    plan = PathPlan(anchor=anchor)
    seen = {anchor}
    frontier = [anchor]
    while frontier:
        current = frontier.pop(0)
        for join, other in model.joins_of(current):
            if other in seen:
                continue
            fanout = join.fanout_from(current)
            if fanout_safe and (
                fanout or join.cardinality == Cardinality.many_to_many
            ):
                continue
            # Prune branches that cannot reach anything still needed.
            beyond = _reachable(model, other, allow_fanout=not fanout_safe)
            remaining = required - seen
            if remaining and not (beyond & remaining):
                continue
            seen.add(other)
            plan.steps[other] = EdgeStep(
                join=join, from_table=current, to_table=other, fanout=fanout
            )
            frontier.append(other)
    missing = required - seen
    if missing:
        raise TranslationError(
            "Cannot build a fan-out-safe join path from "
            f"{anchor!r} to {sorted(missing)}; grouping through one-to-many "
            "or many-to-many child rows is ambiguous (rows would multiply). "
            "Move the measure to the finer-grained table, or use the child "
            "values as a filter rather than a grouping axis."
        )
    return plan


def find_any_path(model: Model, anchor: str, target: str) -> Optional[list[EdgeStep]]:
    """Unrestricted BFS used for EXISTS filter subqueries."""
    if anchor == target:
        return []
    plan = plan_paths(model, anchor, set(), fanout_safe=False)
    # plan with no required set explores the whole connected component
    if target not in plan.steps:
        return None
    steps: list[EdgeStep] = []
    cur = target
    while cur != anchor:
        step = plan.steps[cur]
        steps.append(step)
        cur = step.from_table
    steps.reverse()
    return steps


def _join_predicate(join: Join, aliases: dict[str, str]) -> str:
    return (
        f"{qident(aliases[join.left_table])}.{qident(join.left_column)} = "
        f"{qident(aliases[join.right_table])}.{qident(join.right_column)}"
    )


# ---------------------------------------------------------------------------
# Translator
# ---------------------------------------------------------------------------

_SCALAR_TYPES = (str, int, float, bool)


class Translator:
    def __init__(self, model: Model):
        self.model = model

    # -- resolution -----------------------------------------------------------
    def resolve_group(self, item: GroupIntent) -> ResolvedGroup:
        obj = self.model.field(item.field_id)
        # Calculated field used as a grouping axis
        if obj.__class__.__name__ == "CalculatedField":
            return ResolvedGroup(
                out_name=obj.id,
                label=obj.label,
                table_id=obj.table,
                column_name=None,
                calc_id=obj.id,
                column_type=obj.result_type,
            )
        if obj.__class__.__name__ != "Dimension":
            raise TranslationError(
                f"{item.field_id!r} is a measure; only dimensions and "
                "calculated fields can be used as rows/columns"
            )
        dim = obj
        if dim.hierarchy_id is None:
            return ResolvedGroup(
                out_name=dim.id,
                label=dim.label,
                table_id=dim.table,
                column_name=dim.column,
                calc_id=None,
                column_type=dim.column_type,
            )
        hierarchy = self.model.hierarchy(dim.hierarchy_id)
        if item.hierarchy_level:
            idx = next(
                (i for i, lv in enumerate(hierarchy.levels)
                 if lv.id == item.hierarchy_level),
                None,
            )
            if idx is None:
                raise TranslationError(
                    f"Level {item.hierarchy_level!r} is not in hierarchy "
                    f"{hierarchy.id!r}"
                )
        else:
            idx = next(
                (i for i, lv in enumerate(hierarchy.levels) if lv.id == dim.id),
                0,
            )
        level = hierarchy.levels[idx]
        return ResolvedGroup(
            out_name=dim.id,
            label=level.label,
            table_id=level.table,
            column_name=level.column,
            calc_id=None,
            column_type=level.column_type,
            level_expression=level.expression,
            hierarchy_id=hierarchy.id,
            level_id=level.id,
            level_index=idx,
            can_drill_down=idx < len(hierarchy.levels) - 1,
            can_drill_up=idx > 0,
        )

    def resolve_groups(self, intent: QueryIntent) -> list[ResolvedGroup]:
        out: list[ResolvedGroup] = []
        seen: set[str] = set()
        for item in [*intent.rows, *intent.columns]:
            rg = self.resolve_group(item)
            if rg.out_name in seen:
                continue
            seen.add(rg.out_name)
            out.append(rg)
        return out

    def resolve_filter(self, f: FilterIntent) -> ResolvedFilter:
        obj = self.model.field(f.field_id)
        if obj.__class__.__name__ == "Measure":
            raise TranslationError(
                "Measure filters belong in measure_filters (HAVING), not filters"
            )
        if obj.__class__.__name__ == "CalculatedField":
            table_id, col, calc_id, ctype = (
                obj.table, None, obj.id, obj.result_type,
            )
        else:
            table_id, col, calc_id, ctype = (
                obj.table, obj.column, None, obj.column_type,
            )
        values = self._check_filter_values(f, ctype)
        return ResolvedFilter(
            out_name=obj.id,
            table_id=table_id,
            column_name=col,
            calc_id=calc_id,
            column_type=ctype,
            op=f.op,
            values=values,
        )

    @staticmethod
    def _check_filter_values(f: FilterIntent, ctype: str) -> list:
        if f.op in ("is_null", "not_null"):
            return []
        if f.op == "between":
            if len(f.values) != 2:
                raise TranslationError(
                    f"Filter {f.field_id}: between needs exactly 2 values"
                )
            vals = list(f.values)
        elif f.op in ("in", "not_in"):
            vals = list(f.values)
        else:
            if len(f.values) != 1:
                raise TranslationError(
                    f"Filter {f.field_id}: op {f.op} needs exactly 1 value"
                )
            vals = [f.values[0]]
        for v in vals:
            if not isinstance(v, _SCALAR_TYPES):
                raise TranslationError(
                    f"Filter {f.field_id}: unsupported literal value {v!r}"
                )
            if isinstance(v, bool) and ctype != "boolean":
                raise TranslationError(
                    f"Filter {f.field_id}: boolean literal used on {ctype} column"
                )
        return vals

    def resolve_measure(self, measure_id: str) -> ResolvedMeasure:
        m = self.model.measure(measure_id)
        col_type = "number"
        if m.column is not None:
            col_type = self.model.table(m.table).column(m.column).type
        mf = None
        if m.filter is not None:
            d = m.filter
            mf = ResolvedFilter(
                out_name=f"_filter_{m.id}",
                table_id=d.table,
                column_name=d.column,
                calc_id=None,
                column_type=self.model.table(d.table).column(d.column).type,
                op=d.op,
                values=self._check_filter_values(
                    FilterIntent(field_id="_", op=d.op, values=d.values),
                    self.model.table(d.table).column(d.column).type,
                ),
            )
        return ResolvedMeasure(
            out_name=m.id,
            label=m.label,
            table_id=m.table,
            column_name=m.column,
            calc_expression=m.expression,
            aggregation=m.aggregation,
            measure_filter=mf,
            column_type=col_type,
        )

    # -- per-block SQL --------------------------------------------------------
    def _block_from_and_joins(
        self, plan: PathPlan, aliases: dict[str, str], *, anchor_alias: str
    ) -> list[str]:
        """Emit ``FROM anchor a LEFT JOIN ...`` lines for a planned tree."""
        anchor_table = self.model.table(plan.anchor)
        lines = [
            f"FROM {qident(anchor_table.db_name)} {qident(anchor_alias)}"
        ]
        for table_id, step in plan.steps.items():
            other = self.model.table(table_id)
            child_alias = aliases[table_id]
            parent_alias = aliases[step.from_table]
            pred = (
                f"{qident(parent_alias)}.{qident(step.join.left_column if step.from_table == step.join.left_table else step.join.right_column)} = "
                f"{qident(child_alias)}.{qident(step.join.right_column if step.from_table == step.join.left_table else step.join.left_column)}"
            )
            lines.append(
                f"LEFT JOIN {qident(other.db_name)} {qident(child_alias)} "
                f"ON {pred}"
            )
        return lines

    def _aliases_for(self, plan: PathPlan) -> dict[str, str]:
        # Inside a CTE every model table appears at most once, aliased by
        # its stable model id.
        return {tid: tid for tid in plan.tables}

    def _exists_subquery(
        self,
        anchor_table: str,
        steps: list[EdgeStep],
        target_table: str,
        rf: ResolvedFilter,
        params: list,
    ) -> str:
        """Correlated EXISTS applying ``rf`` on a fan-out/m2m reachable table."""
        aliases: dict[str, str] = {}
        first = steps[0]
        # Correlation column on the outer anchor row
        edge = first.join
        if edge.left_table == anchor_table:
            anchor_col, inner_col = edge.left_column, edge.right_column
        else:
            anchor_col, inner_col = edge.left_column, edge.right_column
        lines = ["EXISTS (SELECT 1"]
        prev_table = anchor_table
        prev_alias = None
        for i, step in enumerate(steps):
            tbl = self.model.table(step.to_table)
            alias = f"ex_{step.to_table}"
            aliases[step.to_table] = alias
            if i == 0:
                lines.append(
                    f"FROM {qident(tbl.db_name)} {qident(alias)}"
                )
                lines.append(
                    f"WHERE {qident(alias)}.{qident(inner_col)} = "
                    f"{qident(anchor_table)}.{qident(anchor_col)}"
                )
            else:
                pe = step.join
                if pe.left_table == prev_table:
                    lc, rc = pe.left_column, pe.right_column
                else:
                    lc, rc = pe.right_column, pe.left_column
                lines.append(
                    f"JOIN {qident(tbl.db_name)} {qident(alias)} "
                    f"ON {qident(aliases[prev_table])}.{qident(lc)} = "
                    f"{qident(alias)}.{qident(rc)}"
                )
            prev_table = step.to_table
        pred = rf.render_expr(aliases[target_table], params, self.model)
        lines.append(f"AND {pred}")
        lines.append(")")
        return "\n".join(lines)

    def _global_filter_predicates(
        self,
    # -- public entry ---------------------------------------------------------
    def translate(self, intent: QueryIntent) -> "Translation":
        groups = self.resolve_groups(intent)
        measures = [self.resolve_measure(mid) for mid in dict.fromkeys(intent.measures)]
        filters = [self.resolve_filter(f) for f in intent.filters]
        measure_having = [
            self._resolve_measure_having(f) for f in intent.measure_filters
        ]
        if not groups and not measures:
            raise TranslationError(
                "Drop at least one dimension or measure to build a query"
            )
        self._validate_sort(intent, groups, measures)

        params: list = []
        blocks: list[dict] = []
        ctes: list[tuple[str, str]] = []

        group_tables = {g.table_id for g in groups}
        measure_anchors = [m.table_id for m in measures]
        measure_local_tables = {
            m.measure_filter.table_id for m in measures if m.measure_filter
        }
        anchor = measure_anchors[0] if measures else groups[0].table_id
        involved_tables = (
            group_tables
            | set(measure_anchors)
            | {f.table_id for f in filters}
            | measure_local_tables
        )

        # -- fast path: everything lives on ONE physical table ----------------
        if len(involved_tables) == 1 and all(
            g.calc_id is None and g.level_expression is None for g in groups
        ):
            sql = self._build_direct(
                groups, measures, filters, measure_having,
                intent, anchor, params, blocks,
            )
            assert_select_only(sql)
            return Translation(
                sql=sql,
                params=params,
                columns=self._columns_meta(groups, measures),
                plan={"blocks": blocks, "referencedTables": sorted(involved_tables)},
            )

        # -- measure CTEs -----------------------------------------------------
        measure_cte_names: list[str] = []
        for i, rm in enumerate(measures):
            cte_name = f"m_{rm.out_name}"
            measure_cte_names.append(cte_name)
            reach = set(group_tables)
            # filters on other tables are handled via EXISTS, not the BFS tree
            plan = plan_paths(
                self.model, rm.table_id, reach, fanout_safe=True
            )
            sql = self._build_measure_block(
                cte_name=cte_name,
                plan=plan,
                groups=groups,
                measure=rm,
                global_filters=filters,
                params=params,
                block_meta=blocks,
            )
            ctes.append((cte_name, sql))

        # -- dimension grid CTE ----------------------------------------------
        dim_cte_name: Optional[str] = None
        if groups:
            dim_cte_name = "dims"
            required_for_dims = set(group_tables)
            plan = plan_paths(
                self.model, anchor, required_for_dims, fanout_safe=True
            )
            sql = self._build_dim_block(
                plan=plan,
                groups=groups,
                global_filters=filters,
                params=params,
                block_meta=blocks,
            )
            # prepend dimension block
            ctes.insert(0, (dim_cte_name, sql))
            blocks.insert(0, {
                "name": dim_cte_name,
                "anchor": anchor,
                "tables": sorted(plan.tables),
                "joins": sorted({s.join.id for s in plan.steps.values()}),
            })

        sql = self._assemble(
            ctes=ctes,
            groups=groups,
            measures=measures,
            dim_cte=dim_cte_name,
            measure_ctes=measure_cte_names,
            having=measure_having,
            sort=intent.sort,
            limit=intent.limit,
            offset=intent.offset,
            params=params,
        )
        columns = self._columns_meta(groups, measures)
        referenced = sorted(involved_tables)
        assert_select_only(sql)
        return Translation(
            sql=sql,
            params=params,
            columns=columns,
            plan={"blocks": blocks, "referencedTables": referenced},
        )

    def _build_direct(
        self, groups, measures, filters, having, intent, table_id, params, blocks
    ) -> str:
        """Plain single-table grouped SELECT: no CTEs, no joins at all."""
        table = self.model.table(table_id)
        alias = table_id
        select_parts = [
            f"{g.render(alias, params, self.model)} AS {qident(g.out_name)}"
            for g in groups
        ]
        for rm in measures:
            select_parts.append(
                f"{rm.render_aggregate(alias, params, self.model)} "
                f"AS {qident(rm.out_name)}"
            )
        lines = [
            "SELECT " + ", ".join(select_parts),
            f"FROM {qident(table.db_name)} {qident(alias)}",
        ]
        wheres: list[str] = []
        for rf in filters:
            wheres.append(rf.render_expr(alias, params, self.model))
        for rm in measures:
            if rm.measure_filter is not None:
                wheres.append(
                    rm.measure_filter.render_expr(alias, params, self.model)
                )
        if wheres:
            lines.append("WHERE " + "\n  AND ".join(wheres))
        if groups:
            ordinals = ", ".join(str(i) for i in range(1, len(groups) + 1))
            lines.append(f"GROUP BY {ordinals}")
        lines = self._order_and_limit(
            groups, measures, intent.sort, intent.limit, intent.offset,
            params, direct=True, having=having, lines=lines,
        )
        blocks.append({
            "name": "main",
            "anchor": table_id,
            "tables": [table_id],
            "joins": [],
        })
        return "\n".join(lines)

    # -- block builders -------------------------------------------------------
    def _filter_wheres(
        self,
        anchor: str,
        inline_tables: set[str],
        aliases: dict[str, str],
        filters: list[ResolvedFilter],
        params: list,
    ) -> list[str]:
        wheres: list[str] = []
        for rf in filters:
            if rf.table_id in inline_tables:
                wheres.append(rf.render_expr(aliases[rf.table_id], params, self.model))
                continue
            steps = find_any_path(self.model, anchor, rf.table_id)
            if steps is None:
                raise TranslationError(
                    f"Filter on {rf.out_name!r}: table {rf.table_id!r} is not "
                    f"connected to {anchor!r} in the model"
                )
            wheres.append(
                self._exists_subquery(anchor, steps, rf.table_id, rf, params)
            )
        return wheres

    def _order_and_limit(
        self, groups, measures, sort, limit, offset, params,
        *, direct: bool, having=None, lines=None,
    ) -> list[str]:
        output_names = [g.out_name for g in groups] + [m.out_name for m in measures]
        order_cols: list[str] = []
        used: set[str] = set()
        for s in sort:
            pos = output_names.index(s.field_id) + 1
            order_cols.append(f"{pos} {'ASC' if s.direction == 'asc' else 'DESC'}")
            used.add(s.field_id)
        if groups:
            for i, g in enumerate(groups, start=1):
                if g.out_name not in used:
                    order_cols.append(f"{i} ASC")
        out = list(lines or [])
        if having:
            haves: list[str] = []
            sym = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=",
                   "lt": "<", "lte": "<="}
            measure_ids = [m.out_name for m in measures]
            for hf in having:
                if hf.out_name not in measure_ids:
                    raise TranslationError(
                        f"Measure filter on unknown measure {hf.out_name!r}"
                    )
                target = qident(hf.out_name)
                if hf.op == "between":
                    haves.append(
                        f"({target} BETWEEN {_push(hf.values[0], params)} "
                        f"AND {_push(hf.values[1], params)})"
                    )
                elif hf.op in ("is_null", "not_null"):
                    kw = "IS NULL" if hf.op == "is_null" else "IS NOT NULL"
                    haves.append(f"({target} {kw})")
                else:
                    haves.append(
                        f"({target} {sym[hf.op]} {_push(hf.values[0], params)})"
                    )
            if haves:
                out.append("HAVING " + "\n  AND ".join(haves))
        if order_cols:
            out.append("ORDER BY " + ", ".join(order_cols))
        if limit is not None:
            out.append(f"LIMIT {_push(int(limit), params)}")
            if offset:
                out.append(f"OFFSET {_push(int(offset), params)}")
        return out

    def _build_measure_block(
        self, cte_name, plan, groups, measure: ResolvedMeasure,
        global_filters, params, block_meta,
    ) -> str:
        aliases = self._aliases_for(plan)
        inline_tables = set(plan.tables)
        select_parts = []
        for i, g in enumerate(groups, start=1):
            expr = g.render(aliases[g.table_id], params, self.model)
            select_parts.append(f"{expr} AS {qident(g.out_name)}")
        agg = measure.render_aggregate(aliases.get(measure.table_id, "m"), params, self.model)
        select_parts.append(f"{agg} AS {qident(measure.out_name)}")

        lines = [f"{cte_name} AS (", "  SELECT " + ", ".join(select_parts)]
        lines += ["  " + l for l in self._block_from_and_joins(
            plan, aliases, anchor_alias=measure.table_id
        )]
        wheres: list[str] = []
        # measure-local filter
        if measure.measure_filter is not None:
            wheres.append(
                measure.measure_filter.render_expr(
                    aliases[measure.table_id], params, self.model
                )
            )
        wheres += self._filter_wheres(
            measure.table_id, inline_tables, aliases, global_filters, params
        )
        if wheres:
            lines.append("  WHERE " + "\n    AND ".join(wheres))
        if groups:
            ordinals = ", ".join(str(i) for i in range(1, len(groups) + 1))
            lines.append(f"  GROUP BY {ordinals}")
        lines.append(")")
        block_meta.append({
            "name": cte_name,
            "anchor": measure.table_id,
            "tables": sorted(plan.tables),
            "joins": sorted({s.join.id for s in plan.steps.values()}),
        })
        return "\n".join(lines)

    def _build_dim_block(
        self, plan, groups, global_filters, params, block_meta
    ) -> str:
        aliases = self._aliases_for(plan)
        inline_tables = set(plan.tables)
        anchor = plan.anchor
        select_parts = []
        rendered: set[str] = set()
        for g in groups:
            if g.out_name in rendered:
                continue
            rendered.add(g.out_name)
            expr = g.render(aliases[g.table_id], params, self.model)
            select_parts.append(f"{expr} AS {qident(g.out_name)}")
        lines = [
            "dims AS (",
            "  SELECT DISTINCT " + ", ".join(select_parts),
        ]
        lines += ["  " + l for l in self._block_from_and_joins(
            plan, aliases, anchor_alias=anchor
        )]
        wheres = self._filter_wheres(
            anchor, inline_tables, aliases, global_filters, params
        )
        if wheres:
            lines.append("  WHERE " + "\n    AND ".join(wheres))
        lines.append(")")
        return "\n".join(lines)

    # -- assembly -------------------------------------------------------------
    def _assemble(
        self, ctes, groups, measures, dim_cte, measure_ctes, having,
        sort: list[SortIntent], limit, offset, params,
    ) -> str:
        select_cols: list[str] = []
        for g in groups:
            src = "d" if dim_cte else "m0"
            select_cols.append(
                f"{src}.{qident(g.out_name)} AS {qident(g.out_name)}"
            )
        for i, rm in enumerate(measures):
            col = f"m{i}.{qident(rm.out_name)}"
            if groups and rm.coalesce_to_zero:
                col = f"COALESCE({col}, 0)"
            select_cols.append(f"{col} AS {qident(rm.out_name)}")

        body: list[str] = []
        if groups:
            body.append(f"FROM {dim_cte} d")
            for i, name in enumerate(measure_ctes):
                keys = " AND ".join(
                    f"m{i}.{qident(g.out_name)} IS NOT DISTINCT FROM "
                    f"d.{qident(g.out_name)}"
                    for g in groups
                )
                body.append(f"LEFT JOIN {name} m{i} ON {keys}")
        else:
            if len(measures) == 1:
                body.append(f"FROM {measure_ctes[0]} m0")
            else:
                body.append(f"FROM {measure_ctes[0]} m0")
                for i in range(1, len(measures)):
                    body.append(f"CROSS JOIN {measure_ctes[i]} m{i}")

        wheres: list[str] = []
        for hf in having:
            rm = self.resolve_measure(hf.out_name)
            idx = [m.out_name for m in measures].index(rm.out_name)
            sym = {
                "eq": "=", "ne": "<>", "gt": ">", "gte": ">=",
                "lt": "<", "lte": "<=",
            }
            if hf.op == "between":
                lo = _push(hf.values[0], params)
                hi = _push(hf.values[1], params)
                wheres.append(
                    f"(m{idx}.{qident(rm.out_name)} BETWEEN {lo} AND {hi})"
                )
            elif hf.op in ("is_null", "not_null"):
                kw = "IS NULL" if hf.op == "is_null" else "IS NOT NULL"
                wheres.append(f"(m{idx}.{qident(rm.out_name)} {kw})")
            else:
                wheres.append(
                    f"(m{idx}.{qident(rm.out_name)} {sym[hf.op]} "
                    f"{_push(hf.values[0], params)})"
                )
        if wheres:
            body.append("WHERE " + "\n  AND ".join(wheres))

        # ORDER BY: explicit sort first (by output ordinal), default fill the
        # remaining group axes so results come back in stable key order.
        order_cols: list[str] = []
        output_names = [g.out_name for g in groups] + [m.out_name for m in measures]
        used: set[str] = set()
        for s in sort:
            if s.field_id not in output_names:
                raise TranslationError(f"Cannot sort by unknown field {s.field_id!r}")
            pos = output_names.index(s.field_id) + 1
            order_cols.append(f"{pos} {'ASC' if s.direction == 'asc' else 'DESC'}")
            used.add(s.field_id)
        if groups:
            for i, g in enumerate(groups, start=1):
                if g.out_name not in used:
                    order_cols.append(f"{i} ASC")
        if order_cols:
            body.append("ORDER BY " + ", ".join(order_cols))
        if limit is not None:
            body.append(f"LIMIT {_push(int(limit), params)}")
            if offset:
                body.append(f"OFFSET {_push(int(offset), params)}")

        core = "SELECT\n  " + ",\n  ".join(select_cols) + "\n" + "\n".join(body)
        if ctes:
            with_sql = ",\n".join(sql for _name, sql in ctes)
            return f"WITH\n{with_sql}\n{core}"
        return core

    # -- metadata / helpers ---------------------------------------------------
    def _resolve_measure_having(self, f: FilterIntent) -> ResolvedFilter:
        m = self.model.measure(f.field_id)
        values = self._check_filter_values(f, "number")
        return ResolvedFilter(
            out_name=m.id, table_id=m.table, column_name=m.column,
            calc_id=None, column_type="number", op=f.op, values=values,
            is_measure_filter=True,
        )

    def _validate_sort(self, intent, groups, measures) -> None:
        valid = {g.out_name for g in groups} | {m.out_name for m in measures}
        for s in intent.sort:
            if s.field_id not in valid:
                raise TranslationError(
                    f"Sort field {s.field_id!r} is not in the query"
                )

    def _columns_meta(self, groups, measures) -> list[dict]:
        cols = []
        for g in groups:
            cols.append({
                "name": g.out_name,
                "label": g.label,
                "type": g.column_type,
                "kind": "group",
                "hierarchyId": g.hierarchy_id,
                "levelId": g.level_id,
                "drillDown": g.can_drill_down,
                "drillUp": g.can_drill_up,
            })
        for m in measures:
            cols.append({
                "name": m.out_name,
                "label": m.label,
                "type": "number",
                "kind": "measure",
            })
        return cols

    # -- drill ----------------------------------------------------------------
    def apply_drill(self, req: DrillRequest) -> QueryIntent:
        """Return a new intent with the hierarchy's current level replaced by
        the next (coarser/finer) level. Filters and measures are untouched,
        so the re-query keeps everything but the grouping grain identical."""
        hierarchy = self.model.hierarchy(req.hierarchy_id)
        rows_cols = [*req.intent.rows, *req.intent.columns]
        current_level_ids = []
        for item in rows_cols:
            dim = self.model.dimension(item.field_id)
            if dim.hierarchy_id == hierarchy.id:
                current_level_ids.append(item.hierarchy_level or dim.id)
        if not current_level_ids:
            raise TranslationError(
                f"Hierarchy {hierarchy.id!r} has no axis in the query"
            )
        current_level = current_level_ids[0]
        idx = next(
            (i for i, lv in enumerate(hierarchy.levels) if lv.id == current_level),
            None,
        )
        if idx is None:
            raise TranslationError(f"Current level {current_level!r} not found")
        new_idx = idx + 1 if req.direction == "down" else idx - 1
        if not 0 <= new_idx < len(hierarchy.levels):
            raise TranslationError(
                f"Already at the {'finest' if req.direction == 'down' else 'coarsest'} level"
            )
        target = hierarchy.levels[new_idx]
        # A drill target must be usable as a normal dimension of the model.
        self.model.dimension(target.id)

        new_intent = req.intent.model_copy(deep=True)
        replaced = False
        for bucket in (new_intent.rows, new_intent.columns):
            for item in bucket:
                dim = self.model.dimension(item.field_id)
                if dim.hierarchy_id == hierarchy.id:
                    item.field_id = target.id
                    item.hierarchy_level = target.id
                    replaced = True
                    break
            if replaced:
                break
        return new_intent


@dataclass
class Translation:
    sql: str
    params: list
    columns: list[dict]
    plan: dict


def _push(value, params: list) -> str:
    """Append a literal as a bound parameter and return its placeholder.

    All user-supplied literals go through here: nothing is ever formatted
    straight into SQL text."""
    params.append(value)
    return f"${len(params)}"
