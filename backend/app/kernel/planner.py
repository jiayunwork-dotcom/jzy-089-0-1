"""查询规划：连通性检查、根表选择、最小放大生成树、度量执行方案。

关键设计（对应自动化测试中守护的规则）：
- 单表：不生成任何 JOIN。
- 生成树用带优先级的 Dijkstra 构造，代价以"会放大行数的边（1:N 的 one->many、
  M:N）"为主、跳数为辅；同权时按关联 id 打破平手 —— 保证同输入稳定。
- 生成树覆盖所有被引用的表，且每张表只接入一次（不重复连接）。
- 度量分两种执行方式：
  * direct：主树无放大边且度量就在根表上 -> 直接聚合；
  * remote：其它所有情况 -> 先在"按度量粒度去重 (DISTINCT 主键)"的子查询里
    算出度量值，再按分组键 LEFT JOIN 回来，杜绝 JOIN 扇出导致的重复计数。
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from .query import QuerySpec, ValueItem
from .schema import (
    AggFunc,
    CalculatedField,
    Dimension,
    Join,
    Measure,
    Model,
)

FANOUT_WEIGHT = 100  # 一条放大边的代价远高于多跳


class PlanningError(Exception):
    pass


@dataclass
class FieldExpr:
    """可分组/可筛选的行级字段表达式（维度或计算字段）。"""

    id: str
    label: str
    table: str
    column: str | None
    expression: str | None  # 沙箱表达式；与 column 互斥为主
    data_type: str
    kind: str  # dimension / calculated
    hierarchy_id: str | None = None


@dataclass
class ResolvedMeasure:
    key: str
    label: str
    table: str
    agg: AggFunc
    column: str | None          # 直接列
    expression: str | None      # 或沙箱表达式（计算字段度量）
    filter_expression: str | None  # 度量自带的过滤
    source: ValueItem


@dataclass
class TreeEdge:
    join: Join
    parent: str
    node: str

    @property
    def fanout(self) -> bool:
        return self.join.is_fanout_from(self.parent)


@dataclass
class SpanningTree:
    root: str
    order: list[str]                       # root 在前的 BFS 接入顺序
    edges_by_node: dict[str, TreeEdge]     # node -> 接入边
    has_fanout: bool

    def path_to(self, node: str) -> list[TreeEdge]:
        out: list[TreeEdge] = []
        cur = node
        while cur != self.root:
            e = self.edges_by_node[cur]
            out.append(e)
            cur = e.parent
        out.reverse()
        return out


@dataclass
class MeasurePlan:
    resolved: ResolvedMeasure
    direct: bool


@dataclass
class Plan:
    model: Model
    spec: QuerySpec
    groups: list[FieldExpr]
    measures: list[ResolvedMeasure]
    where_filters: list  # list[tuple[FieldExpr, Filter]]
    having_filters: list  # list[tuple[ResolvedMeasure, Filter]]
    involved_tables: list[str]
    main_tree: SpanningTree | None      # None = 单表，不需要树
    direct_measures: list[ResolvedMeasure]
    remote_measures: list[ResolvedMeasure]
    # 主树是否含放大边：含则根表度量也不能直接聚合（JOIN 会复制事实行）
    fanout_present: bool = False


def _expr_identifiers(expr: str) -> list[str]:
    """提取沙箱表达式中的所有标识符（字段/计算字段名）。"""
    from .sandbox import ast_nodes as _ast
    from .sandbox.parser import parse

    root = parse(expr)
    out: list[str] = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n.kind == _ast.FIELD:
            out.append(n.name)
        elif n.kind == _ast.FUNC:
            stack.extend(n.args)
        elif n.kind == _ast.BINARY:
            stack.extend([n.left, n.right])
        elif n.kind == _ast.UNARY:
            stack.append(n.operand)
    return out


def _expression_tables(model: Model, owner: str, expr: str) -> set[str]:
    """表达式中引用的物理列/计算字段归属的表集合。

    计算字段递归展开（同表），物理列按全模型唯一归属解析；
    多表同名列在渲染阶段会报歧义错误。
    """
    tables: set[str] = {owner}
    expanding: set[str] = set()

    def visit(expression: str, current_owner: str) -> None:
        for ident in _expr_identifiers(expression):
            calc = model.calculated_fields.get(ident)
            if calc is not None:
                if calc.id in expanding:
                    return  # 循环引用由沙箱编译期报错，这里只需收集
                expanding.add(calc.id)
                tables.add(calc.table)
                visit(calc.expression, calc.table)
                expanding.discard(calc.id)
                continue
            owners = [
                t.id for t in model.tables.values() if t.has(ident)
            ]
            if len(owners) == 1:
                tables.add(owners[0])
            # 0 个：沙箱校验会报未知字段；多个：渲染期报歧义

    visit(expr, owner)
    return tables


def build_plan(model: Model, spec: QuerySpec) -> Plan:
    model.validate()
    groups = _resolve_groups(model, spec)
    measures = [_resolve_measure(model, v) for v in spec.values]

    where_filters = []
    having_by_key: dict[str, ResolvedMeasure] = {m.key: m for m in measures}
    having_filters = []
    for f in spec.filters:
        if f.field_id in model.measures:
            # 筛选区里引用的度量：必须也出现在数值区，针对聚合结果 HAVING
            if f.field_id not in having_by_key:
                raise PlanningError(f"筛选的度量 {f.field_id} 未放入数值区")
            having_filters.append((having_by_key[f.field_id], f))
        else:
            where_filters.append((_resolve_filter_field(model, f.field_id), f))

    # 去重检查（同一字段/度量重复投放是无意义的，也破坏稳定别名）
    _ensure_unique([g.id for g in groups], "行/列")
    _ensure_unique([m.key for m in measures], "数值")

    tables: set[str] = {g.table for g in groups}
    tables.update(m.table for m in measures)
    tables.update(f.table for f, _ in where_filters)
    # 表达式（维度/度量/计算字段/度量过滤）会隐式引用其它表，收集闭包
    expr_sources: list[tuple[str, str | None]] = (
        [(g.table, g.expression) for g in groups]
        + [(m.table, m.expression) for m in measures]
        + [(m.table, m.filter_expression) for m in measures]
        + [(f.table, f.expression) for f, _ in where_filters]
    )
    for owner, expr in expr_sources:
        if expr:
            tables.update(_expression_tables(model, owner, expr))
    if not tables:
        raise PlanningError("查询至少需要一个维度或度量")

    _ensure_connected(model, tables)

    group_tables = {g.table for g in groups}
    if len(tables) == 1:
        root = next(iter(tables))
        tree = None
        involved = set(tables)
        direct, remote = _classify_measures(measures, root, [])
        fanout = False
    else:
        # 先在"全部涉及表"上选根并探测放大边，决定度量的执行方式
        root = choose_root(model, tables, group_tables, measures)
        probe = spanning_tree(model, root, tables)
        fanout = probe.has_fanout
        if fanout:
            # 主树存在放大边：任何"裸 JOIN 后聚合"都会被复制行影响，
            # 包括根表自己的度量 —— 全部度量走去重子查询；内层主树只承载分组。
            direct, remote = [], list(measures)
            main_required = set(group_tables)
            main_required.add(sorted(group_tables)[0])
            for g in groups:
                if g.expression:
                    main_required.update(_expression_tables(model, g.table, g.expression))
            for fexpr, _ in where_filters:
                main_required.add(fexpr.table)
                if fexpr.expression:
                    main_required.update(
                        _expression_tables(model, fexpr.table, fexpr.expression)
                    )
            anchor = sorted(main_required)[0]
            tree = (
                spanning_tree(model, anchor, main_required)
                if len(main_required) > 1 else None
            )
            involved = set(tables)
        else:
            direct, remote = _classify_measures(measures, root, [])
            # 无放大边：主树只需覆盖 分组维度 + 直接度量 + 可下推筛选；
            # 远端度量（如维度表上的度量）在独立去重子查询中自行连接。
            main_required = {root}
            main_required.update(group_tables)
            main_required.update(m.table for m in direct)
            for fexpr, _ in where_filters:
                main_required.add(fexpr.table)
            for g in groups:
                if g.expression:
                    main_required.update(_expression_tables(model, g.table, g.expression))
            for m in direct:
                if m.expression:
                    main_required.update(_expression_tables(model, m.table, m.expression))
                if m.filter_expression:
                    main_required.update(
                        _expression_tables(model, m.table, m.filter_expression)
                    )
            tree = spanning_tree(model, root, main_required)
            involved = set(tree.order)
            for m in remote:
                involved.update(
                    spanning_tree(model, m.table,
                                  _sub_required(model, m, groups, where_filters)).order
                )

    return Plan(
        model=model,
        spec=spec,
        groups=groups,
        measures=measures,
        where_filters=where_filters,
        having_filters=having_filters,
        involved_tables=sorted(involved),
        main_tree=tree,
        direct_measures=direct,
        remote_measures=remote,
        fanout_present=fanout,
    )


# ---------------------------------------------------------------------------
# 字段解析
# ---------------------------------------------------------------------------

def _resolve_groups(model: Model, spec: QuerySpec) -> list[FieldExpr]:
    out: list[FieldExpr] = []
    for gid in spec.group_ids:
        d = model.dimensions.get(gid)
        if d is not None:
            out.append(
                FieldExpr(
                    id=d.id,
                    label=d.name,
                    table=d.table,
                    column=d.column if d.is_table_column else None,
                    expression=d.expression,
                    data_type=d.data_type.value,
                    kind="dimension",
                    hierarchy_id=d.hierarchy_id,
                )
            )
            continue
        c = model.calculated_fields.get(gid)
        if c is not None:
            out.append(
                FieldExpr(
                    id=c.id,
                    label=c.name,
                    table=c.table,
                    column=None,
                    expression=c.expression,
                    data_type=c.data_type.value,
                    kind="calculated",
                )
            )
            continue
        raise PlanningError(f"未知字段: {gid}")
    return out


def _resolve_measure(model: Model, item: ValueItem) -> ResolvedMeasure:
    if item.measure_id:
        m = model.measures.get(item.measure_id)
        if m is None:
            raise PlanningError(f"未知度量: {item.measure_id}")
        return ResolvedMeasure(
            key=m.id,
            label=m.name,
            table=m.table,
            agg=m.agg,
            column=m.column,
            expression=m.expression,
            filter_expression=m.filter_expression,
            source=item,
        )
    if item.field_id:
        c = model.calculated_fields.get(item.field_id)
        if c is None:
            raise PlanningError(f"未知计算字段: {item.field_id}")
        try:
            agg = AggFunc(item.agg)
        except ValueError:
            raise PlanningError(f"不支持的聚合: {item.agg}")
        return ResolvedMeasure(
            key=c.id,
            label=c.name,
            table=c.table,
            agg=agg,
            column=None,
            expression=c.expression,
            filter_expression=None,
            source=item,
        )
    raise PlanningError("数值条目必须引用一个度量或计算字段")


def _resolve_filter_field(model: Model, field_id: str) -> FieldExpr:
    d = model.dimensions.get(field_id)
    if d is not None:
        return FieldExpr(
            id=d.id,
            label=d.name,
            table=d.table,
            column=d.column if d.is_table_column else None,
            expression=d.expression,
            data_type=d.data_type.value,
            kind="dimension",
            hierarchy_id=d.hierarchy_id,
        )
    c = model.calculated_fields.get(field_id)
    if c is not None:
        return FieldExpr(
            id=c.id,
            label=c.name,
            table=c.table,
            column=None,
            expression=c.expression,
            data_type=c.data_type.value,
            kind="calculated",
        )
    raise PlanningError(f"筛选字段不存在: {field_id}")


def _ensure_unique(values: list[str], what: str) -> None:
    if len(set(values)) != len(values):
        dupes = sorted({v for v in values if values.count(v) > 1})
        raise PlanningError(f"{what}中存在重复字段: {', '.join(dupes)}")


# ---------------------------------------------------------------------------
# 图算法
# ---------------------------------------------------------------------------

def shortest_path(model: Model, start: str, target: str) -> list[TreeEdge]:
    """模型原图上 start -> target 的稳定最短路（允许经过任意中间表）。"""
    if start == target:
        return []
    prev: dict[str, tuple[Join, str]] = {start: None}
    queue = [start]
    head = 0
    # BFS；neighbors 已按 join id 排序，同层发现顺序确定
    while head < len(queue):
        cur = queue[head]
        head += 1
        for join, nxt in model.neighbors(cur):
            if nxt not in prev:
                prev[nxt] = (join, cur)
                if nxt == target:
                    queue = queue[:head]  # 提前结束
                    break
                queue.append(nxt)
        if target in prev:
            break
    if target not in prev:
        raise PlanningError(f"表 {start} 到 {target} 不存在连接路径")
    out: list[TreeEdge] = []
    cur = target
    while cur != start:
        join, parent = prev[cur]
        out.append(TreeEdge(join=join, parent=parent, node=cur))
        cur = parent
    out.reverse()
    return out


def _ensure_connected(model: Model, tables: set[str]) -> None:
    start = sorted(tables)[0]
    seen = {start}
    queue = [start]
    while queue:
        cur = queue.pop()
        for _, other in model.neighbors(cur):
            if other not in seen:
                seen.add(other)
                queue.append(other)
    missing = sorted(tables - seen)
    if missing:
        raise PlanningError(
            f"被引用的表无法通过已声明的关联连通：缺少到 {', '.join(missing)} 的连接路径"
        )


def spanning_tree(
    model: Model, root: str, required: set[str]
) -> SpanningTree:
    """从 root 出发的最小代价生成树（覆盖 required，允许经过中转表）。

    中转表（路径需要、但字段未被直接引用）也会进入树并被 JOIN，因为路径物理上
    必须经过它们；每张表仍然只接入一次。边代价 = (放大权重 or 0, 1, join id)，
    全图 Dijkstra 求最短路径，保证最小放大且结果稳定。
    """
    dist: dict[str, tuple] = {root: (0, 0, "")}
    prev: dict[str, tuple[Join, str]] = {}
    pq: list[tuple] = [(0, 0, "", root)]
    while pq:
        dcost, dhops, _, cur = heapq.heappop(pq)
        cur_dist = dist[cur]
        if (dcost, dhops) != (cur_dist[0], cur_dist[1]):
            continue
        for join, nxt in model.neighbors(cur):
            add = FANOUT_WEIGHT if join.is_fanout_from(cur) else 0
            cand = (dcost + add, dhops + 1, join.id)
            if nxt not in dist or cand < dist[nxt]:
                dist[nxt] = cand
                prev[nxt] = (join, cur)
                heapq.heappush(pq, (cand[0], cand[1], join.id, nxt))

    missing = sorted(required - set(dist))
    if missing:  # 理论不可达：调用前已做连通性检查
        raise PlanningError(f"无法生成连接路径: {missing}")  # pragma: no cover

    # 树的节点 = required + 根到 required 最短路径上经过的所有中转表
    nodes = {root}
    for t in required:
        cur = t
        while cur != root:
            nodes.add(cur)
            cur = prev[cur][1]
        nodes.add(root)

    # 按 (距根跳数, 放大代价, 节点 id) BFS 展开，接入顺序确定
    order = sorted(nodes, key=lambda t: (dist[t][1], dist[t][0], t))
    if order[0] != root:
        order.remove(root)
        order = [root] + order
    edges: dict[str, TreeEdge] = {}
    has_fanout = False
    for node in order[1:]:
        join, parent = prev[node]
        te = TreeEdge(join=join, parent=parent, node=node)
        edges[node] = te
        has_fanout = has_fanout or te.fanout
    return SpanningTree(root=root, order=order, edges_by_node=edges, has_fanout=has_fanout)


def choose_root(
    model: Model,
    tables: set[str],
    group_tables: set[str],
    measures: list[ResolvedMeasure],
) -> str:
    """选择最优根表。

    排序键（越小越优）：
    1. 根表承载的度量数（**多者优先**：让事实表做根，避免维度表做根后
       事实度量沿放大边被 JOIN 复制）；
    2. 生成树放大边数；
    3. 生成树总跳数；
    4. 根表 id（确定性平手裁决）。
    """
    measure_count: dict[str, int] = {t: 0 for t in tables}
    for m in measures:
        measure_count[m.table] = measure_count.get(m.table, 0) + 1

    best: tuple | None = None
    for cand in sorted(tables):
        tree = spanning_tree(model, cand, tables)
        fanouts = sum(1 for e in tree.edges_by_node.values() if e.fanout)
        hops = len(tree.edges_by_node)
        key = (-measure_count.get(cand, 0), fanouts, hops, cand)
        if best is None or key < best[0]:
            best = (key, cand)
    return best[1]


def _sub_required(model, measure, groups, where_filters) -> set[str]:
    need = {measure.table}
    for g in groups:
        need.add(g.table)
        if g.expression:
            need.update(_expression_tables(model, g.table, g.expression))
    for fexpr, _ in where_filters:
        need.add(fexpr.table)
        if fexpr.expression:
            need.update(_expression_tables(model, fexpr.table, fexpr.expression))
    if measure.expression:
        need.update(_expression_tables(model, measure.table, measure.expression))
    if measure.filter_expression:
        need.update(_expression_tables(model, measure.table, measure.filter_expression))
    return need


def _classify_measures(
    measures: list[ResolvedMeasure], root: str, _edges
) -> tuple[list[ResolvedMeasure], list[ResolvedMeasure]]:
    direct = [m for m in measures if m.table == root]
    remote = [m for m in measures if m.table != root]
    return direct, remote
