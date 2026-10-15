from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from metricflow_semantics.dag.id_prefix import IdPrefix, StaticIdPrefix
from metricflow_semantics.dag.mf_dag import DisplayedProperty
from metricflow_semantics.model.semantics.value_mapping_lookup import ResolvedValueMapping
from metricflow_semantics.specs.dimension_spec import DimensionSpec
from metricflow_semantics.specs.order_by_spec import OrderBySpec
from metricflow_semantics.toolkit.visitor import VisitorOutputT

from metricflow.dataflow.dataflow_plan import DataflowPlanNode
from metricflow.dataflow.dataflow_plan_visitor import DataflowPlanNodeVisitor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DimensionValueMappingBinding:
    """Bind an output spec, including its alias, to its resolved dictionary."""

    output_spec: DimensionSpec
    mapping: ResolvedValueMapping


@dataclass(frozen=True, eq=False)
class ApplyDimensionValueMappingsNode(DataflowPlanNode):
    """Final display operation; labels do not establish output uniqueness."""

    mappings: tuple[DimensionValueMappingBinding, ...]
    order_by_specs: tuple[OrderBySpec, ...] = ()

    @staticmethod
    def create(
        parent_node: DataflowPlanNode,
        mappings: tuple[DimensionValueMappingBinding, ...],
        order_by_specs: tuple[OrderBySpec, ...] = (),
    ) -> ApplyDimensionValueMappingsNode:
        """Create one display boundary for all mapped output dimensions."""
        assert mappings
        return ApplyDimensionValueMappingsNode(
            parent_nodes=(parent_node,), mappings=mappings, order_by_specs=order_by_specs
        )

    @classmethod
    def id_prefix(cls) -> IdPrefix:  # noqa: D102
        return StaticIdPrefix.DATAFLOW_NODE_VALUE_MAPPING_ID_PREFIX

    def accept(self, visitor: DataflowPlanNodeVisitor[VisitorOutputT]) -> VisitorOutputT:  # noqa: D102
        return visitor.visit_apply_dimension_value_mappings_node(self)

    @property
    def description(self) -> str:  # noqa: D102
        return "Apply Dimension Value Mappings"

    @property
    def parent_node(self) -> DataflowPlanNode:  # noqa: D102
        return self.parent_nodes[0]

    @property
    def displayed_properties(self) -> Sequence[DisplayedProperty]:  # noqa: D102
        return tuple(super().displayed_properties) + (DisplayedProperty("mappings", self.mappings),)

    def functionally_identical(self, other_node: DataflowPlanNode) -> bool:  # noqa: D102
        if type(other_node) is not ApplyDimensionValueMappingsNode:
            return False
        return other_node.mappings == self.mappings and other_node.order_by_specs == self.order_by_specs

    def with_new_parents(self, new_parent_nodes: Sequence[DataflowPlanNode]) -> ApplyDimensionValueMappingsNode:
        """Preserve bound mappings when an optimizer replaces the calculation branch."""
        assert len(new_parent_nodes) == 1
        return self.create(new_parent_nodes[0], self.mappings, self.order_by_specs)
