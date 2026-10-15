from __future__ import annotations

import logging

from metricflow_semantics.sql.sql_exprs import (
    SqlAggregateFunctionExpression,
    SqlCastToStringExpression,
    SqlColumnReferenceExpression,
    SqlComparison,
    SqlComparisonExpression,
    SqlExpressionNode,
    SqlFunction,
)
from metricflow_semantics.sql.sql_join_type import SqlJoinType

from metricflow.dataset.output_value_mapping import PendingOutputValueMapping
from metricflow.sql.sql_plan import SqlPlanNode, SqlSelectColumn
from metricflow.sql.sql_select_node import SqlJoinDescription, SqlOrderByDescription, SqlSelectStatementNode

logger = logging.getLogger(__name__)


def apply_output_value_mapping(sql_node: SqlPlanNode, pending: PendingOutputValueMapping) -> SqlPlanNode:
    """Wrap the optimized raw SELECT without changing row grain or limit semantics."""
    raw_select = sql_node.as_select_node
    assert raw_select is not None
    raw_alias = "mapping_raw"
    replacements: dict[str, SqlExpressionNode] = {}
    joins = []
    for index, mapping in enumerate(pending.mappings):
        alias = f"value_mapping_{index}"
        raw = SqlColumnReferenceExpression.from_column_reference(raw_alias, mapping.source_column_name)
        key = SqlColumnReferenceExpression.from_column_reference(alias, mapping.value_column_name)
        label = SqlColumnReferenceExpression.from_column_reference(alias, mapping.display_column_name)
        joins.append(
            SqlJoinDescription(
                right_source=mapping.mapping_select,
                right_source_alias=alias,
                join_type=SqlJoinType.LEFT_OUTER,
                on_condition=SqlComparisonExpression.create(
                    left_expr=raw, comparison=SqlComparison.EQUALS, right_expr=key
                ),
            )
        )
        replacements[mapping.source_column_name] = SqlAggregateFunctionExpression.create(
            sql_function=SqlFunction.COALESCE,
            sql_function_args=[SqlCastToStringExpression.create(label), SqlCastToStringExpression.create(raw)],
        )
    return SqlSelectStatementNode.create(
        description="Apply Dimension Value Mappings",
        select_columns=tuple(
            SqlSelectColumn(
                expr=replacements.get(column.column_alias)
                or SqlColumnReferenceExpression.from_column_reference(raw_alias, column.column_alias),
                column_alias=column.column_alias,
            )
            for column in raw_select.select_columns
        ),
        from_source=raw_select,
        from_source_alias=raw_alias,
        join_descs=tuple(joins),
        order_bys=tuple(
            SqlOrderByDescription(
                expr=SqlColumnReferenceExpression.from_column_reference(raw_alias, column), desc=descending
            )
            for column, descending in pending.order_by_columns
        ),
    )
