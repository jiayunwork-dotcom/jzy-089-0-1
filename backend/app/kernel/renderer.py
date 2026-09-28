"""SQL 渲染器：Plan -> 参数化 PostgreSQL SELECT。

输出性质（由 tests/test_translator.py 守护）：
* 单表不出现 JOIN；
* 主 JOIN 树是一棵生成树：每张表只接入一次，且覆盖所有被引用的表；
* 跨表度量经 "DISTINCT 主键去重子查询" 聚合后再按分组键挂回，JOIN 扇出不会放大求和；
* 同规格、同投放顺序 -> 逐字符一致的 SQL 与参数；
* 所有用户字面量绑定参数（%s），SQL 文本不含任何用户输入的值；
* 只生成 SELECT，不含分号或任何写操作关键字。

分组查询统一采用两层结构：

    SELECT g1, ..., 直接度量, sub.mval, ...
    FROM (
        SELECT <分组表达式> AS g1, ..., AGG(...) AS m_direct...
        FROM <生成树>
        WHERE <可下推的行筛选>
        GROUP BY 1..k
        HAVING <直接度量的度量筛选>
    ) base
    LEFT JOIN (<去重子查询>) sub ON sub.k1 IS NOT DISTINCT FROM base.g1 ...
    WHERE <其余行筛选(含 EXISTS 半连接)>
    ORDER BY ...
    LIMIT ...
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import sandbox
from .planner import (
    FieldExpr,
    Plan,
    ResolvedMeasure,
    SpanningTree,
    TreeEdge,
    build_plan,
    shortest_path,
    spanning_tree,
)
from .query import QuerySpec, TranslationResult
from .schema import AggFunc, DataType, Filter, FilterOp, Model

PG_CAST = {
    "text": "text",
    "integer": "bigint",
    "numeric": "numeric",
    "boolean": "boolean",
    "date": "date",
    "timestamp": "timestamp",
}


def qident(name: str) -> str:
    """加双引号。标识符格式在 Model.validate() 已用白名单正则约束。"""
    return '"' + name.replace('"', '""') + '"'


class ParamBag:
    """绑定参数收集器。

    同一过滤表达式可能同时进入主查询与去重子查询：编译阶段把每个唯一字面量
    记为一个命名占位符（$bN）并缓存其 SQL 片段，避免重复编译；最终渲染时把
    $bN 按出现顺序替换成位置占位符 %s，并同步生成与之逐一对齐的参数列表。
    用户值永远不会出现在 SQL 文本中。
    """

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self._fragments: dict[tuple, str] = {}
        self._n = 0

    def _register(self, key: tuple, value: Any, cast: str) -> str:
        frag = self._fragments.get(key)
        if frag is None:
            self._n += 1
            name = f"$b{self._n}"
            self.values[name] = value
            frag = f"{name}::{cast}"
            self._fragments[key] = frag
        return frag

    def add(self, value: Any, data_type: str) -> str:
        if value is None:
            raise ValueError("空值比较请使用 is_null / not_null")
        cast = PG_CAST.get(data_type, "text")
        return self._register(("v", cast, repr(value)), value, cast)

    def add_array(self, values: list, data_type: str) -> str:
        if any(v is None for v in values):
            raise ValueError("枚举筛选不允许包含空值")
        cast = PG_CAST.get(data_type, "text")
        value = list(values)
        return self._register(("a", cast, repr(value)), value, f"{cast}[]")

    def add_int(self, value: int) -> str:
        value = int(value)
        return self._register(("i", value), value, "bigint")

    def finalize(self, sql: str) -> tuple[str, list[Any]]:
        """把命名占位符按在 SQL 中出现的顺序替换为 %s，返回 (sql, params)。

        同一命名片段在 SQL 中出现几次，参数列表里就重复几次 —— 与 psycopg
        位置占位符一一对应；值仍然完全不进入 SQL 文本。
        """
        import re

        params: list[Any] = []

        def repl(m: re.Match) -> str:
            params.append(self.values[m.group(0)])
            return "%s"

        text = re.sub(r"\$b\d+", repl, sql)
        return text, params


# ---------------------------------------------------------------------------
# 表达式作用域
# ---------------------------------------------------------------------------

@dataclass
class Scope:
    model: Model
    aliases: dict[str, str]
    _expanding: set[str] = field(default_factory=set)

    def table_alias(self, table_id: str) -> str:
        if table_id not in self.aliases:
            raise KeyError(f"作用域内未引入表 {table_id}")
        return self.aliases[table_id]

    def field_sql(self, name: str) -> str:
        calc = self.model.calculated_fields.get(name)
        if calc is not None:
            if name in self._expanding:
                from .sandbox import ExpressionError

                raise ExpressionError(
                    f"计算字段存在循环引用: {name}", 0, 1
                )
            self._expanding.add(name)
            try:
                return sandbox.compile_expression(
                    calc.expression, self.field_sql, self._alloc
                )
            finally:
                self._expanding.discard(name)
        owner = _owner_table(self.model, name)
        return f"{qident(self.table_alias(owner))}.{qident(name)}"

    _alloc: Any = None


def _owner_table(model: Model, column: str) -> str:
    owners = [t.id for t in model.tables.values() if t.has(column)]
    if len(owners) == 1:
        return owners[0]
    if not owners:
        raise sandbox.ExpressionError(f"未知字段 {column!r}", 0, 1)
    raise sandbox.ExpressionError(f"字段 {column!r} 在多张表中存在，引用有歧义", 0, 1)


# ---------------------------------------------------------------------------
# 过滤
# ---------------------------------------------------------------------------

_SQL_OPS = {
    FilterOp.EQ: "=",
    FilterOp.NE: "<>",
    FilterOp.GT: ">",
    FilterOp.GTE: ">=",
    FilterOp.LT: "<",
    FilterOp.LTE: "<=",
}


def _like_escape(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def compile_filter_sql(field_sql: str, field_type: str, flt: Filter, bag: ParamBag) -> str:
    op = flt.op
    if op in _SQL_OPS:
        if not isinstance(flt.value, (str, int, float, bool)):
            raise ValueError(f"{op.value} 需要单个标量值")
        return f"{field_sql} {_SQL_OPS[op]} {bag.add(flt.value, field_type)}"
    if op in (FilterOp.IN, FilterOp.NOT_IN):
        if not isinstance(flt.value, list) or not flt.value:
            raise ValueError("in/not_in 需要非空值列表")
        arr = bag.add_array(flt.value, field_type)
        neg = "NOT " if op == FilterOp.NOT_IN else ""
        return f"{field_sql} {neg}= ANY({arr})"
    if op == FilterOp.BETWEEN:
        if not isinstance(flt.value, list) or len(flt.value) != 2:
            raise ValueError("between 需要 [下限, 上限]")
        lo, hi = flt.value
        return f"{field_sql} BETWEEN {bag.add(lo, field_type)} AND {bag.add(hi, field_type)}"
    if op == FilterOp.IS_NULL:
        return f"{field_sql} IS NULL"
    if op == FilterOp.NOT_NULL:
        return f"{field_sql} IS NOT NULL"
    if op in (FilterOp.STARTS_WITH, FilterOp.ENDS_WITH, FilterOp.CONTAINS):
        if field_type != "text":
            raise ValueError(f"{op.value} 只能用于文本字段")
        if not isinstance(flt.value, str):
            raise ValueError(f"{op.value} 需要文本值")
        if op == FilterOp.STARTS_WITH:
            pattern = f"{_like_escape(flt.value)}%"
        elif op == FilterOp.ENDS_WITH:
            pattern = f"%{_like_escape(flt.value)}"
        else:
            pattern = f"%{_like_escape(flt.value)}%"
        return f"{field_sql} LIKE {bag.add(pattern, 'text')} ESCAPE '\\'"
    raise ValueError(f"不支持的筛选操作: {op}")  # pragma: no cover


def _is_additive(m: ResolvedMeasure) -> bool:
    return m.agg in (AggFunc.SUM, AggFunc.COUNT, AggFunc.COUNT_DISTINCT)


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else line for line in text.split("\n"))


class Renderer:
    def __init__(self, plan: Plan, default_limit: int, max_limit: int) -> None:
        self.plan = plan
        self.model = plan.model
        self.spec = plan.spec
        self.bag = ParamBag()
        self.default_limit = default_limit
        self.max_limit = max_limit

    # -- 公共入口 ------------------------------------------------------------
    def render(self) -> TranslationResult:
        groups = self.plan.groups
        direct = self.plan.direct_measures
        remote = self.plan.remote_measures

        # 度量别名：严格按数值区投放顺序 m1..mN（结果列/排序序号与此一致）
        measure_aliases: dict[str, str] = {}
        for j, m in enumerate(self.plan.measures, start=1):
            measure_aliases[m.key] = f"m{j}"
        group_aliases = {g.id: f"g{i}" for i, g in enumerate(groups, start=1)}

        if not groups:
            body = self._render_ungrouped(direct, remote, measure_aliases)
        elif self.plan.main_tree is None and not direct:
            # 分组 + 全部远端度量（Form B，内层分组树可能单表）
            body = self._render_grouped(direct, remote, groups, measure_aliases, group_aliases)
        elif self.plan.main_tree is None:
            body = self._render_single_table_grouped(measure_aliases, group_aliases)
        else:
            # 分组 + 多表：有直接度量走 Form A；无直接度量走 Form B（内层仅分组树）
            body = self._render_grouped(direct, remote, groups, measure_aliases, group_aliases)

        columns = self._result_columns(groups, self.plan.measures,
                                       group_aliases, measure_aliases)
        sql, params = self.bag.finalize(body)
        return TranslationResult(sql=sql, params=params, result_columns=columns)

    # -- 无分组 --------------------------------------------------------------
    def _render_ungrouped(self, direct, remote, measure_aliases) -> str:
        select_cols: list[str] = []
        from_sql = ""
        joins: list[str] = []
        inner_where: list[str] = []
        outer_where: list[str] = []
        inner_having: list[str] = []
        scope: Scope | None = None

        if self.plan.main_tree is None:
            only = self.plan.involved_tables[0]
            scope = Scope(self.model, {only: "t0"})
            scope._alloc = self._alloc
            from_sql = f"FROM {self._physical(only)} AS {qident('t0')}"
            inner_where = [
                compile_filter_sql(self._expr_sql(f, scope), f.data_type, flt, self.bag)
                for f, flt in self.plan.where_filters
            ]
        elif direct:
            tree = self.plan.main_tree
            aliases = {tid: f"t{i}" for i, tid in enumerate(tree.order)}
            scope = Scope(self.model, aliases)
            scope._alloc = self._alloc
            from_sql, joins = self._render_tree(tree, aliases)
            inner_where = self._inner_where_parts(scope, tree)
            outer_where = self._outer_where_parts(tree, aliases)
        # 无内层（全部为远端标量子查询）：所有筛选已在各子查询内部应用，外层无需 WHERE

        if scope is not None:
            for m in self.plan.measures:
                if m not in direct:
                    continue
                alias = measure_aliases[m.key]
                select_cols.append(f"{self._agg_sql(m, scope)} AS {qident(alias)}")
            # 直接度量的度量筛选 -> HAVING（无 GROUP BY 时合法）
            for m, flt in self.plan.having_filters:
                if m in direct:
                    expr = self._agg_sql(m, scope)
                    inner_having.append(compile_filter_sql(expr, "numeric", flt, self.bag))

        # 远端度量 -> 标量子查询（按投放顺序插入）
        sub_renders = self._render_remote_subqueries(remote, [])
        sub_by_key = {m.key: sub for m, sub in sub_renders}
        for m in self.plan.measures:
            if m in remote:
                alias = measure_aliases[m.key]
                select_cols.append(f"({sub_by_key[m.key]['sql']}) AS {qident(alias)}")

        parts = ["SELECT " + ", ".join(select_cols), from_sql, *joins]
        if inner_where:
            parts.append("WHERE " + " AND ".join(f"({p})" for p in inner_where))
        if inner_having:
            parts.append("HAVING " + " AND ".join(f"({p})" for p in inner_having))
        # 无分组 Form A 的主树覆盖所有涉及表且无放大边，不会产生外层筛选
        parts.append("LIMIT " + self.bag.add_int(self._limit()))
        return "\n".join(p for p in parts if p)

    # -- 单表分组（无 JOIN、无包装子查询） ------------------------------------
    def _render_single_table_grouped(self, measure_aliases, group_aliases) -> str:
        groups = self.plan.groups
        only = self.plan.involved_tables[0]
        scope = Scope(self.model, {only: "t0"})
        scope._alloc = self._alloc

        select_cols = [
            f"{self._expr_sql(g, scope)} AS {qident(group_aliases[g.id])}"
            for g in groups
        ]
        for m in self.plan.measures:
            select_cols.append(
                f"{self._agg_sql(m, scope)} AS {qident(measure_aliases[m.key])}"
            )
        where_parts = [
            compile_filter_sql(self._expr_sql(f, scope), f.data_type, flt, self.bag)
            for f, flt in self.plan.where_filters
        ]
        having_parts = []
        for m, flt in self.plan.having_filters:
            having_parts.append(
                compile_filter_sql(self._agg_sql(m, scope), "numeric", flt, self.bag)
            )

        parts = [
            "SELECT " + ", ".join(select_cols),
            f"FROM {self._physical(only)} AS {qident('t0')}",
        ]
        if where_parts:
            parts.append("WHERE " + " AND ".join(f"({p})" for p in where_parts))
        parts.append("GROUP BY " + ", ".join(str(i) for i in range(1, len(groups) + 1)))
        if having_parts:
            parts.append("HAVING " + " AND ".join(f"({p})" for p in having_parts))
        order_sql = self._render_order(len(groups), len(self.plan.measures))
        if order_sql:
            parts.append(order_sql)
        parts.append("LIMIT " + self.bag.add_int(self._limit()))
        return "\n".join(p for p in parts if p)

    # -- 分组 ----------------------------------------------------------------
    def _render_grouped(self, direct, remote, groups, measure_aliases, group_aliases) -> str:
        inner_select: list[str] = []
        inner_from = ""
        inner_joins: list[str] = []
        inner_where: list[str] = []
        inner_having: list[str] = []
        outer_where: list[str] = []
        outer_joins: list[str] = []
        scope: Scope | None = None
        tree: SpanningTree | None = None

        # 内层：分组表达式 + 直接度量
        if not direct:
            # Form B：内层只承载分组维度（及其表达式）所需的表；
            # 所有度量都在去重子查询中聚合后挂回。
            from .planner import _expression_tables

            group_tables = {g.table for g in groups}
            for g in groups:
                if g.expression:
                    group_tables.update(_expression_tables(self.model, g.table, g.expression))
            if len(group_tables) == 1:
                only = next(iter(group_tables))
                aliases = {only: "t0"}
                scope = Scope(self.model, aliases)
                scope._alloc = self._alloc
                inner_from = f"FROM {self._physical(only)} AS {qident('t0')}"
            else:
                anchor = sorted(group_tables)[0]
                tree = spanning_tree(self.model, anchor, group_tables)
                aliases = {tid: f"t{i}" for i, tid in enumerate(tree.order)}
                scope = Scope(self.model, aliases)
                scope._alloc = self._alloc
                inner_from, inner_joins = self._render_tree(tree, aliases)
        elif self.plan.main_tree is None:
            # Form A 单表（所有内容都落在同一张表）
            only = self.plan.involved_tables[0]
            scope = Scope(self.model, {only: "t0"})
            scope._alloc = self._alloc
            inner_from = f"FROM {self._physical(only)} AS {qident('t0')}"
        else:
            # Form A：内层生成树覆盖全部涉及表
            tree = self.plan.main_tree
            aliases = {tid: f"t{i}" for i, tid in enumerate(tree.order)}
            scope = Scope(self.model, aliases)
            scope._alloc = self._alloc
            inner_from, inner_joins = self._render_tree(tree, aliases)

        # 行筛选分流：
        # - 字段在本地内层树内、且到它的路径无放大边 -> 下推 WHERE；
        # - 字段在本地内层树内、但在放大边之后 -> 外层相关 EXISTS（锚 t0）；
        # - 字段不在内层树（Form B 中事实侧维度等）：
        #   内层不可引用 -> 生成与 base.gN 相关的 EXISTS（事实侧按分组键半连接）；
        #   同时远端去重子查询内部也已带上相同条件，保持度量与筛选一致。
        inner_where = self._inner_where_parts(scope, tree)
        outer_where = self._outer_where_parts(tree, scope.aliases)
        if not direct:
            for fexpr, flt in self.plan.where_filters:
                if tree is not None and fexpr.table in scope.aliases:
                    continue  # 已在内层/外层树路径上处理
                outer_where.append(
                    self._group_correlated_exists(fexpr, flt, groups, group_aliases)
                )

        for i, g in enumerate(groups, start=1):
            inner_select.append(f"{self._expr_sql(g, scope)} AS {qident(group_aliases[g.id])}")
        for j, m in enumerate(direct, start=1):
            alias = measure_aliases[m.key]
            inner_select.append(f"{self._agg_sql(m, scope)} AS {qident(alias)}")

        # 直接度量的度量筛选 -> 内层 HAVING
        for m, flt in self.plan.having_filters:
            if m in direct:
                expr = self._agg_sql(m, scope)
                inner_having.append(compile_filter_sql(expr, "numeric", flt, self.bag))

        inner_parts = [
            "SELECT " + ", ".join(inner_select),
            inner_from,
            *inner_joins,
        ]
        if inner_where:
            inner_parts.append("WHERE " + " AND ".join(f"({p})" for p in inner_where))
        inner_parts.append(
            "GROUP BY " + ", ".join(str(i) for i in range(1, len(groups) + 1))
        )
        if inner_having:
            inner_parts.append("HAVING " + " AND ".join(f"({p})" for p in inner_having))
        inner_sql = "\n".join(p for p in inner_parts if p)

        # 外层
        outer_select = [
            f"{qident('base')}.{qident(group_aliases[g.id])} AS {qident(group_aliases[g.id])}"
            for g in groups
        ]
        sub_renders = self._render_remote_subqueries(remote, groups)
        sub_by_key = {m.key: sub for m, sub in sub_renders}
        for m, sub in sub_renders:
            on = " AND ".join(
                f"{qident(sub['alias'])}.{qident(f'k{i}')} "
                f"IS NOT DISTINCT FROM {qident('base')}.{qident(group_aliases[groups[i - 1].id])}"
                for i in range(1, sub["nkeys"] + 1)
            )
            outer_joins.append(
                f"LEFT JOIN (\n{_indent(sub['sql'], '  ')}\n) AS {qident(sub['alias'])}\n  ON {on}"
            )
        # 外层度量列严格按数值区投放顺序输出
        for m in self.plan.measures:
            alias = measure_aliases[m.key]
            if m in direct:
                outer_select.append(
                    f"{qident('base')}.{qident(alias)} AS {qident(alias)}"
                )
            else:
                col = f"{qident(sub_by_key[m.key]['alias'])}.{qident('mval')}"
                if _is_additive(m):
                    col = f"COALESCE({col}, 0)"
                outer_select.append(f"{col} AS {qident(alias)}")

        # 远端度量的度量筛选 -> 外层 WHERE
        for m, flt in self.plan.having_filters:
            if m in remote:
                col = f"{qident(measure_aliases[m.key])}"
                outer_where.append(compile_filter_sql(col, "numeric", flt, self.bag))

        outer_parts = [
            "SELECT " + ", ".join(outer_select),
            "FROM (\n" + _indent(inner_sql, "  ") + "\n) AS " + qident("base"),
            *outer_joins,
        ]
        if outer_where:
            outer_parts.append("WHERE " + " AND ".join(f"({p})" for p in outer_where))
        order_sql = self._render_order(len(groups), len(self.plan.measures))
        if order_sql:
            outer_parts.append(order_sql)
        outer_parts.append("LIMIT " + self.bag.add_int(self._limit()))
        return "\n".join(p for p in outer_parts if p)

    # -- 过滤位置 ------------------------------------------------------------
    def _inner_where_parts(self, scope: Scope, tree: SpanningTree | None) -> list[str]:
        """可安全下推到内层的行筛选：从根到字段路径上没有放大边。"""
        parts: list[str] = []
        for fexpr, flt in self.plan.where_filters:
            if tree is None:
                # 单表作用域：字段必须在该表上，否则交给外层/子查询
                if fexpr.table not in scope.aliases:
                    continue
            elif fexpr.table not in scope.aliases:
                continue
            elif any(e.fanout for e in tree.path_to(fexpr.table)):
                continue
            col = self._expr_sql(fexpr, scope)
            parts.append(compile_filter_sql(col, fexpr.data_type, flt, self.bag))
        return parts

    def _outer_where_parts(self, tree: SpanningTree | None, aliases: dict[str, str]) -> list[str]:
        parts: list[str] = []
        root = tree.root if tree is not None else self.plan.involved_tables[0]
        root_alias = aliases.get(root, "t0")
        for fexpr, flt in self.plan.where_filters:
            if tree is None or fexpr.table not in aliases:
                # 字段不在（内层）树中：由远端去重子查询自行处理，外层不可引用
                continue
            if not any(e.fanout for e in tree.path_to(fexpr.table)):
                continue  # 已下推内层
            parts.append(self._exists_filter(fexpr, flt, root, root_alias))
        return parts

    def _group_correlated_exists(
        self, fexpr: FieldExpr, flt: Filter,
        groups: list[FieldExpr], group_aliases: dict[str, str],
    ) -> str:
        """Form B 外层、字段不在分组树内时：与 base 分组键相关的 EXISTS 半连接。"""
        anchor = sorted({g.table for g in groups})[0]
        path = shortest_path(self.model, anchor, fexpr.table)
        local: dict[str, str] = {}
        for i, e in enumerate(path):
            local[e.node] = f"gx{i}"
        first = path[0]
        first_alias = local[first.node]
        chain_sql: list[str] = []
        for e in path[1:]:
            on = self._join_on(e.join, e.parent, local[e.parent], e.node, local[e.node])
            chain_sql.append(
                f"JOIN {self._physical(e.node)} AS {qident(local[e.node])} ON {on}"
            )
        scope = Scope(self.model, local)
        scope._alloc = self._alloc
        cond = compile_filter_sql(
            self._expr_sql(fexpr, scope), fexpr.data_type, flt, self.bag
        )
        corr_parts: list[str] = []
        for g in groups:
            if g.table in local:
                col = self._expr_sql(g, scope)
            elif g.table == anchor:
                col = f"{qident(first_alias)}.{qident(g.column)}"
            else:
                col = self._corr_group_column(g, anchor, local, first_alias)
            corr_parts.append(
                f"{col} IS NOT DISTINCT FROM {qident('base')}.{qident(group_aliases[g.id])}"
            )
        return (
            f"EXISTS (SELECT 1 FROM {self._physical(first.node)} AS {qident(first_alias)} "
            + " ".join(chain_sql)
            + " WHERE " + " AND ".join(corr_parts + [cond]) + ")"
        )

    def _corr_group_column(self, g: FieldExpr, anchor: str,
                           local: dict[str, str]) -> str:
        """分组维度所在表不在 EXISTS 路径上时，沿 anchor 反查它的列。

        常见情形：分组表就是路径起点 anchor；多分组表时按模型再做一跳连接。
        """
        if g.table == anchor:
            return f"{qident(local[anchor])}.{qident(g.column)}"
        # 分组表与 anchor 的一跳连接（样例模型中分组维度同表，这里做兜底）
        path = shortest_path(self.model, anchor, g.table)
        if len(path) == 1:
            alias = "gx_a"
            return f"{qident(alias)}.{qident(g.column)}"
        raise RuntimeError(f"无法为分组字段 {g.id} 构造相关条件")

    def _expr_on_alias(self, g: FieldExpr, scope: Scope, table_id: str) -> str:
        # 维度表达式在相关 EXISTS 中可能引用分组树之外的列；相关列只需裸列即可
        if g.expression:
            return sandbox.compile_expression(g.expression, scope.field_sql, self._alloc)
        return f"{qident(scope.table_alias(table_id))}.{qident(g.column)}"

    def _exists_filter(
        self,
        fexpr: FieldExpr,
        flt: Filter,
        root: str,
        root_alias: str,
        local_aliases: dict[str, str] | None = None,
    ) -> str:
        """从指定根表出发到筛选表的相关 EXISTS 半连接（支持多跳路径）。"""
        target = fexpr.table
        if target == root:
            scope = Scope(self.model, {root: root_alias})
            scope._alloc = self._alloc
            col = self._expr_sql(fexpr, scope)
            return compile_filter_sql(col, fexpr.data_type, flt, self.bag)
        path = shortest_path(self.model, root, target)
        local: dict[str, str] = {}
        for i, e in enumerate(path):
            local[e.node] = f"ex{i}"
        first = path[0]
        first_alias = local[first.node]
        chain_sql: list[str] = []
        for e in path[1:]:
            on = self._join_on(e.join, e.parent, local[e.parent], e.node, local[e.node])
            chain_sql.append(
                f"JOIN {self._physical(e.node)} AS {qident(local[e.node])} ON {on}"
            )
        # 第一跳：与（子）查询根的相关条件
        root_on = self._join_on(first.join, root, root_alias, first.node, first_alias)
        scope = Scope(self.model, local)
        scope._alloc = self._alloc
        col = self._expr_sql(fexpr, scope)
        cond = compile_filter_sql(col, fexpr.data_type, flt, self.bag)
        return (
            f"EXISTS (SELECT 1 FROM {self._physical(first.node)} AS {qident(first_alias)} "
            + " ".join(chain_sql)
            + f" WHERE {root_on} AND {cond})"
        )

    # -- 树 / JOIN -----------------------------------------------------------
    def _physical(self, table_id: str) -> str:
        return qident(self.model.tables[table_id].physical_name)

    def _tree_aliases(self, tree: SpanningTree) -> dict[str, str]:
        return {tid: f"t{i}" for i, tid in enumerate(tree.order)}

    def _render_tree(self, tree: SpanningTree, aliases: dict[str, str]):
        joins = [
            self._render_join(tree.edges_by_node[node], aliases)
            for node in tree.order[1:]
        ]
        first = f"FROM {self._physical(tree.root)} AS {qident(aliases[tree.root])}"
        return first, joins

    def _render_join(self, te: TreeEdge, aliases: dict[str, str]) -> str:
        on = self._join_on(
            te.join, te.parent, aliases[te.parent], te.node, aliases[te.node]
        )
        return f"LEFT JOIN {self._physical(te.node)} AS {qident(aliases[te.node])}\n  ON {on}"

    def _join_on(self, join, parent_table: str, parent_alias: str,
                 child_table: str, child_alias: str) -> str:
        if join.left_table == parent_table and join.right_table == child_table:
            lp, rp = "left_column", "right_column"
        elif join.left_table == child_table and join.right_table == parent_table:
            lp, rp = "right_column", "left_column"
        else:  # pragma: no cover
            raise RuntimeError("JOIN 边与表不匹配")
        parts = [
            f"{qident(parent_alias)}.{qident(getattr(k, lp))} "
            f"IS NOT DISTINCT FROM {qident(child_alias)}.{qident(getattr(k, rp))}"
            for k in join.keys
        ]
        return " AND ".join(parts)

    # -- 表达式 / 聚合 -------------------------------------------------------
    def _alloc(self, value: Any, data_type: str) -> str:
        return self.bag.add(value, data_type)

    def _expr_sql(self, fexpr: FieldExpr, scope: Scope) -> str:
        if fexpr.expression:
            return sandbox.compile_expression(
                fexpr.expression, scope.field_sql, self._alloc
            )
        return f"{qident(scope.table_alias(fexpr.table))}.{qident(fexpr.column)}"

    def _agg_sql(self, m: ResolvedMeasure, scope: Scope) -> str:
        if m.expression:
            inner = sandbox.compile_expression(m.expression, scope.field_sql, self._alloc)
        else:
            inner = f"{qident(scope.table_alias(m.table))}.{qident(m.column)}"
        if m.filter_expression:
            cond = sandbox.compile_expression(
                m.filter_expression, scope.field_sql, self._alloc
            )
            if m.agg is AggFunc.COUNT:
                return f"COUNT(CASE WHEN {cond} THEN {inner} END)"
            if m.agg is AggFunc.COUNT_DISTINCT:
                return f"COUNT(DISTINCT CASE WHEN {cond} THEN {inner} END)"
            return f"{m.agg.value.upper()}(CASE WHEN {cond} THEN {inner} END)"
        if m.agg is AggFunc.COUNT:
            return f"COUNT({inner})"
        if m.agg is AggFunc.COUNT_DISTINCT:
            return f"COUNT(DISTINCT {inner})"
        return f"{m.agg.value.upper()}({inner})"

    # -- 远端去重子查询 ------------------------------------------------------
    def _remote_subtree(self, measure: ResolvedMeasure):
        from .planner import _expression_tables

        need = {measure.table}
        for g in self.plan.groups:
            need.add(g.table)
            if g.expression:
                need.update(_expression_tables(self.model, g.table, g.expression))
        for fexpr, _ in self.plan.where_filters:
            need.add(fexpr.table)
            if fexpr.expression:
                need.update(_expression_tables(self.model, fexpr.table, fexpr.expression))
        if measure.expression:
            need.update(_expression_tables(self.model, measure.table, measure.expression))
        if measure.filter_expression:
            need.update(
                _expression_tables(self.model, measure.table, measure.filter_expression)
            )
        return spanning_tree(self.model, measure.table, need)

    def _render_remote_subqueries(self, remote, groups):
        out = []
        for idx, m in enumerate(remote, start=1):
            alias = f"sub_m{idx}"
            tree = self._remote_subtree(m)
            aliases = {tid: f"{alias}_t{i}" for i, tid in enumerate(tree.order)}
            scope = Scope(self.model, aliases)
            scope._alloc = self._alloc

            sub_select: list[str] = []
            for i, g in enumerate(groups, start=1):
                sub_select.append(f"{self._expr_sql(g, scope)} AS {qident(f'k{i}')}")
            pk_cols = self.model.tables[m.table].pk_columns
            pk_alias = scope.table_alias(m.table)
            if m.expression:
                value_expr = sandbox.compile_expression(
                    m.expression, scope.field_sql, self._alloc
                )
            else:
                value_expr = f"{qident(pk_alias)}.{qident(m.column)}"
            if len(pk_cols) == 1:
                pk_expr = f"{qident(pk_alias)}.{qident(pk_cols[0].name)}"
            else:
                pk_expr = "ROW(" + ", ".join(
                    f"{qident(pk_alias)}.{qident(c.name)}" for c in pk_cols
                ) + ")"
            sub_select.append(f"{pk_expr} AS {qident('entity_pk')}")
            sub_select.append(f"{value_expr} AS {qident('raw_value')}")

            where_parts: list[str] = []
            root_alias = aliases[m.table]
            for fexpr, flt in self.plan.where_filters:
                if fexpr.table not in aliases:
                    continue
                if any(e.fanout for e in tree.path_to(fexpr.table)):
                    # 放大边之后的筛选：以子查询根为锚做相关 EXISTS，保持事实粒度语义
                    where_parts.append(
                        self._exists_filter(fexpr, flt, m.table, root_alias)
                    )
                    continue
                col = self._expr_sql(fexpr, scope)
                where_parts.append(
                    compile_filter_sql(col, fexpr.data_type, flt, self.bag)
                )
            if m.filter_expression:
                where_parts.append(
                    sandbox.compile_expression(
                        m.filter_expression, scope.field_sql, self._alloc
                    )
                )

            sub_from, sub_joins = self._render_tree(tree, aliases)
            inner = (
                "SELECT DISTINCT " + ", ".join(sub_select)
                + "\n" + sub_from
                + ("\n" + "\n".join(sub_joins) if sub_joins else "")
                + ("\nWHERE " + " AND ".join(f"({p})" for p in where_parts)
                   if where_parts else "")
            )
            agg = self._outer_agg(m)
            outer_cols = [f"{agg} AS {qident('mval')}"]
            group_sql = ""
            if groups:
                outer_cols += [qident(f"k{i}") for i in range(1, len(groups) + 1)]
                group_sql = "\nGROUP BY " + ", ".join(
                    qident(f"k{i}") for i in range(1, len(groups) + 1)
                )
            sub_sql = (
                "SELECT " + ", ".join(outer_cols)
                + "\nFROM (\n" + _indent(inner, "  ") + "\n) AS " + qident(alias + "_d")
                + group_sql
            )
            out.append((m, {"alias": alias, "sql": sub_sql, "nkeys": len(groups)}))
        return out

    def _render_scalar_subquery(self, measure: ResolvedMeasure) -> str:
        rendered = self._render_remote_subqueries([measure], [])
        return rendered[0][1]["sql"]

    def _outer_agg(self, m: ResolvedMeasure) -> str:
        raw = qident("raw_value")
        if m.agg is AggFunc.SUM:
            return f"COALESCE(SUM({raw}), 0)"
        if m.agg is AggFunc.COUNT:
            return f"COUNT({raw})"
        if m.agg is AggFunc.COUNT_DISTINCT:
            return f"COUNT(DISTINCT {raw})"
        if m.agg is AggFunc.AVG:
            return f"AVG({raw})"
        if m.agg is AggFunc.MIN:
            return f"MIN({raw})"
        if m.agg is AggFunc.MAX:
            return f"MAX({raw})"
        raise ValueError(f"不支持的聚合: {m.agg}")  # pragma: no cover

    # -- 排序 / 限制 / 结果列 -------------------------------------------------
    def _limit(self) -> int:
        limit = self.spec.limit if self.spec.limit is not None else self.default_limit
        return min(limit, self.max_limit)

    def _render_order(self, n_groups: int, n_measures: int) -> str:
        n_total = n_groups + n_measures
        parts = []
        for s in self.spec.sorts:
            if not 1 <= s.index <= n_total:
                raise ValueError(f"排序列序号超出范围: {s.index}")
            parts.append(f"{s.index} {s.dir.upper()}")
        return "ORDER BY " + ", ".join(parts) if parts else ""

    def _result_columns(self, groups, measures, group_aliases, measure_aliases):
        cols = []
        for g in groups:
            cols.append({
                "key": group_aliases[g.id],
                "field_id": g.id,
                "label": g.label,
                "kind": "group",
                "data_type": g.data_type,
            })
        for m in self.plan.measures:
            cols.append({
                "key": measure_aliases[m.key],
                "field_id": m.key,
                "label": m.label,
                "kind": "value",
                "agg": m.agg.value,
                "data_type": "numeric",
            })
        return cols


def translate(
    model: Model,
    spec: QuerySpec,
    *,
    default_limit: int = 1000,
    max_limit: int = 10000,
) -> TranslationResult:
    plan = build_plan(model, spec)
    return Renderer(plan, default_limit, max_limit).render()
