from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Tuple

from metricflow_semantics.semantic_graph.attribute_resolution.group_by_item_set import (
    GroupByItemSet,
)
from metricflow_semantics.specs.instance_spec import LinkableInstanceSpec
from metricflow_semantics.specs.linkable_spec_set import LinkableSpecSet
from metricflow_semantics.specs.spec_set import InstanceSpecSet
from metricflow_semantics.sql.sql_bind_parameters import SqlBindParameterSet

from metricflow_semantic_interfaces.dataclass_serialization import SerializableDataclass


@dataclass(frozen=True)
class WhereFilterSpec(SerializableDataclass):
    """Similar to the WhereFilter, but with the where_sql_template rendered and used elements extracted.

    For example:

    WhereFilter(where_sql_template="{{ Dimension('listing__country') }} == 'US'"))

    ->

    WhereFilterSpec(
        where_sql="listing__country == 'US'",
        bind_parameter_set: SqlBindParameters(),
        linkable_specs: (
            DimensionSpec(
                element_name='country',
                entity_links=('listing',),
        ),
        linkable_elements: (
            LinkableDimension(
                semantic_model_origin=SemanticModelReference(semantic_model_name='listings_latest')
                element_name='country',
                ...
            )
        )
    )

    模板解析后的过滤条件；包含可用于 SQL 的表达式和它引用的维度。
    """

    # Debating whether where_sql / bind_parameter_set belongs here. where_sql may become dialect specific if we introduce
    # quoted identifiers later.
    # 已由语义模板解析出的条件；SQL Visitor 把它放进选定层的 WHERE。
    where_sql: str
    # WHERE 中占位符的实际值，最终与各子句参数合并到 SqlStatement。
    bind_parameters: SqlBindParameterSet
    # 条件依赖的语义列；Builder 即使不在最终输出这些列，也必须先选源或 JOIN 取得它们。
    element_set: GroupByItemSet

    @cached_property
    def linkable_spec_set(self) -> LinkableSpecSet:
        """Return the `LinkableSpecSet` of the group-by items referenced in this filter."""
        return LinkableSpecSet.create_from_specs(self.element_set.specs)

    @cached_property
    def linkable_specs(self) -> Tuple[LinkableInstanceSpec, ...]:  # noqa: D102
        return self.element_set.specs

    @cached_property
    def instance_spec_set(self) -> InstanceSpecSet:  # noqa: D102
        return InstanceSpecSet.create_from_specs(self.linkable_specs)
