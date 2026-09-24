from __future__ import annotations

import typing
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

from metricflow_semantics.dag.id_prefix import IdPrefix, StaticIdPrefix
from metricflow_semantics.dag.mf_dag import DisplayedProperty
from metricflow_semantics.sql.sql_exprs import SqlExpressionNode
from metricflow_semantics.sql.sql_join_type import SqlJoinType
from metricflow_semantics.toolkit.visitor import VisitorOutputT
from typing_extensions import override

from metricflow.sql.sql_plan import SqlPlanNode, SqlPlanNodeVisitor, SqlSelectColumn
from metricflow.sql.sql_table_node import SqlTableNode

if typing.TYPE_CHECKING:
    from metricflow.sql.sql_cte_node import SqlCteAliasMapping, SqlCteNode


@dataclass(frozen=True)
class SqlJoinDescription:
    """Describes how sources should be joined together.

    已确定 SQL JOIN 类型与 ON 条件；区别于数据流层只描述合并意图的 CombineAggregatedOutputsNode。
    """

    # 被并入当前 SELECT 的右侧计算结果；Renderer 会递归渲染它，而不把它当作已存在的表。
    right_source: SqlPlanNode
    # 本层 ON/SELECT 引用右侧列时使用的表别名，须与右侧来源引用保持一致。
    right_source_alias: str
    # 决定未匹配行的保留方式；维度补齐通常 LEFT，指标分支对齐通常 FULL OUTER。
    join_type: SqlJoinType
    # 从实体键或共同分组项构建的匹配条件；CROSS JOIN 无匹配键时为空。
    on_condition: Optional[SqlExpressionNode] = None

    def with_right_source(self, new_right_source: SqlPlanNode) -> SqlJoinDescription:
        """Return a copy of this but with the right source replaced."""
        return SqlJoinDescription(
            right_source=new_right_source,
            right_source_alias=self.right_source_alias,
            join_type=self.join_type,
            on_condition=self.on_condition,
        )


@dataclass(frozen=True)
class SqlOrderByDescription:  # noqa: D101
    # ORDER BY 表达式及是否降序。
    expr: SqlExpressionNode
    desc: bool


@dataclass(frozen=True, eq=False)
class SqlSelectStatementNode(SqlPlanNode):
    """Represents an SQL Select statement.

    Attributes:
        select_columns: The columns to select.
        from_source: The source of the data for the select statement.
        from_source_alias: Alias for the from source.
        join_descs: Descriptions of the joins to perform.
        group_bys: The columns to group by.
        order_bys: The columns to order by.
        where: The where clause expression.
        limit: The limit of the number of rows to return.
        distinct: Whether the select statement should return distinct rows.

    表示一层完整 SELECT；子查询、JOIN、WHERE、GROUP BY 等均以字段保存，等待 Renderer 拼接文本。
    """

    # 用于计划阅读和 SQL 中的说明注释，帮助把最终 SQL 片段追溯回数据流节点。
    _description: str
    # 本层对外提供的列及别名；外层子查询只能通过这些别名继续引用值。
    select_columns: Tuple[SqlSelectColumn, ...]
    # 本层计算的左侧输入；若是子查询，Renderer 先递归渲染它。
    from_source: SqlPlanNode
    # select_columns、JOIN 条件与其他表达式引用左侧列时采用的别名。
    from_source_alias: str
    # 共享分支生成的 WITH 定义；主查询及 JOIN 可按别名多次读取它们。
    cte_sources: Tuple[SqlCteNode, ...]
    # 各右侧分支的来源、类型和 ON 条件；Renderer 按序接在 FROM 之后。
    join_descs: Tuple[SqlJoinDescription, ...]
    # 决定聚合行粒度；合并指标分支后也用它对共同维度重新分组。
    group_bys: Tuple[SqlSelectColumn, ...]
    # 用户请求的结果排序，渲染在聚合完成之后，不改变指标计算粒度。
    order_bys: Tuple[SqlOrderByDescription, ...]
    # 当前层执行的过滤表达式；它筛选进入本层 GROUP BY 的输入行。
    where: Optional[SqlExpressionNode]
    # 最终可见行数上限；Renderer 在本层其他子句之后输出它。
    limit: Optional[int]
    # 要求本层去重；与 GROUP BY 的聚合粒度含义不同。
    distinct: bool

    @staticmethod
    def create(  # noqa: D102
        description: str,
        select_columns: Iterable[SqlSelectColumn],
        from_source: SqlPlanNode,
        from_source_alias: str,
        cte_sources: Tuple[SqlCteNode, ...] = (),
        join_descs: Tuple[SqlJoinDescription, ...] = (),
        group_bys: Tuple[SqlSelectColumn, ...] = (),
        order_bys: Tuple[SqlOrderByDescription, ...] = (),
        where: Optional[SqlExpressionNode] = None,
        limit: Optional[int] = None,
        distinct: bool = False,
    ) -> SqlSelectStatementNode:
        parent_nodes = (from_source,) + tuple(x.right_source for x in join_descs) + cte_sources
        return SqlSelectStatementNode(
            parent_nodes=parent_nodes,
            _description=description,
            select_columns=tuple(select_columns),
            from_source=from_source,
            from_source_alias=from_source_alias,
            cte_sources=cte_sources,
            join_descs=join_descs,
            group_bys=group_bys,
            order_bys=order_bys,
            where=where,
            limit=limit,
            distinct=distinct,
        )

    @classmethod
    def id_prefix(cls) -> IdPrefix:  # noqa: D102
        return StaticIdPrefix.SQL_PLAN_SELECT_STATEMENT_ID_PREFIX

    @property
    def displayed_properties(self) -> Sequence[DisplayedProperty]:  # noqa: D102
        return (
            tuple(super().displayed_properties)
            + tuple(DisplayedProperty(f"col{i}", column) for i, column in enumerate(self.select_columns))
            + (DisplayedProperty("from_source", self.from_source),)
            + tuple(DisplayedProperty(f"join_{i}", join_desc) for i, join_desc in enumerate(self.join_descs))
            + tuple(DisplayedProperty(f"group_by{i}", group_by) for i, group_by in enumerate(self.group_bys))
            + (DisplayedProperty("where", self.where),)
            + tuple(DisplayedProperty(f"order_by{i}", order_by) for i, order_by in enumerate(self.order_bys))
            + (DisplayedProperty("distinct", self.distinct),)
        )

    def accept(self, visitor: SqlPlanNodeVisitor[VisitorOutputT]) -> VisitorOutputT:  # noqa: D102
        return visitor.visit_select_statement_node(self)

    @property
    def as_select_node(self) -> Optional[SqlSelectStatementNode]:  # noqa: D102
        return self

    @property
    @override
    def as_sql_table_node(self) -> Optional[SqlTableNode]:
        return None

    @property
    @override
    def description(self) -> str:
        return self._description

    @override
    def nearest_select_columns(self, cte_source_mapping: SqlCteAliasMapping) -> Optional[Sequence[SqlSelectColumn]]:
        return self.select_columns

    @override
    def copy(self) -> SqlSelectStatementNode:
        return SqlSelectStatementNode.create(
            description=self._description,
            select_columns=self.select_columns,
            from_source=self.from_source.copy(),
            from_source_alias=self.from_source_alias,
            cte_sources=tuple(node.copy() for node in self.cte_sources),
            join_descs=tuple(
                join_desc.with_right_source(join_desc.right_source.copy()) for join_desc in self.join_descs
            ),
            group_bys=self.group_bys,
            order_bys=self.order_bys,
            where=self.where,
            limit=self.limit,
            distinct=self.distinct,
        )

    def with_select_columns(self, select_columns: Iterable[SqlSelectColumn]) -> SqlSelectStatementNode:
        """Return a copy with the select columns replaced."""
        return SqlSelectStatementNode.create(
            description=self.description,
            select_columns=tuple(select_columns),
            from_source=self.from_source,
            from_source_alias=self.from_source_alias,
            cte_sources=self.cte_sources,
            join_descs=self.join_descs,
            group_bys=self.group_bys,
            order_bys=self.order_bys,
            where=self.where,
            limit=self.limit,
            distinct=self.distinct,
        )

    def with_where_clause(self, where: Optional[SqlExpressionNode]) -> SqlSelectStatementNode:
        """Return a copy with the `WHERE` clause replaced."""
        return SqlSelectStatementNode.create(
            description=self.description,
            select_columns=self.select_columns,
            from_source=self.from_source,
            from_source_alias=self.from_source_alias,
            cte_sources=self.cte_sources,
            join_descs=self.join_descs,
            group_bys=self.group_bys,
            order_bys=self.order_bys,
            where=where,
            limit=self.limit,
            distinct=self.distinct,
        )
