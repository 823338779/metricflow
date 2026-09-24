from __future__ import annotations

import itertools
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Set
from functools import cached_property
from typing import Generic, Iterable, Optional

from metricflow_semantics.dag.id_prefix import StaticIdPrefix
from metricflow_semantics.dag.sequential_id import SequentialId, SequentialIdGenerator
from metricflow_semantics.semantic_graph.model_id import SemanticModelId
from metricflow_semantics.specs.metric_spec import MetricModifier, MetricSpec
from metricflow_semantics.toolkit.collections.ordered_set import FrozenOrderedSet, OrderedSet
from metricflow_semantics.toolkit.dataclass_helpers import fast_frozen_dataclass
from metricflow_semantics.toolkit.mf_graph.comparable import ComparisonKey
from metricflow_semantics.toolkit.mf_graph.graph_labeling import MetricFlowGraphLabel
from metricflow_semantics.toolkit.mf_graph.mf_graph import MetricFlowGraphNode
from metricflow_semantics.toolkit.mf_graph.node_descriptor import MetricFlowGraphNodeDescriptor
from metricflow_semantics.toolkit.mf_logging.lazy_formattable import LazyFormat
from metricflow_semantics.toolkit.mf_logging.pretty_formatter import PrettyFormatContext
from metricflow_semantics.toolkit.visitor import Visitable, VisitorOutputT
from typing_extensions import Self, override

from metricflow.metric_evaluation.plan.me_labels import BaseMetricQueryLabel, TopLevelQueryLabel
from metricflow.metric_evaluation.plan.query_element import MetricQueryElement, MetricQueryPropertySet

logger = logging.getLogger(__name__)


@fast_frozen_dataclass(order=False)
class MetricQueryNode(MetricFlowGraphNode, Visitable, ABC):
    """Represents a query for a specific set of metrics.

    This maps to a SQL query. The inputs to the node represent the dependencies (e.g. the subqueries that compute the
    input metrics for a derived metric) and the outputs of the node represent the metrics that are computed or passed
    through from the dependencies.

    指标计算计划中的一个查询节点；依赖节点提供输入，本节点计算或透传指标。
    """

    # 图遍历用它区分计算节点；日志与计划表也用它定位分支，但它不决定指标值或 SQL 列名。
    node_id: SequentialId
    # The query properties that are associated with the outputs of this node. This is later used to generate the
    # appropriate dataflow nodes. This is needed on a per-query basis as some modifiers for input metrics of a
    # derived metric (e.g. filters and time offsets) can require different query properties.
    # 从查询元素带来的分组和过滤上下文。下游 Visitor 用它决定聚合粒度与过滤下推；
    # 即使 metric_specs 相同，上下文不同也不能复用同一计算分支。
    query_properties: MetricQueryPropertySet

    @abstractmethod
    def pruned(self, allowed_specs: Set[MetricSpec]) -> Self:
        """Create a copy of this node where outputs that are not in the provided set are removed."""
        raise NotImplementedError

    @property
    @abstractmethod
    def output_metric_specs(self) -> OrderedSet[MetricSpec]:
        """Return the specs for the metrics output by this node (both computed and passthrough).

        本节点承诺向后续节点提供的指标集合，含新计算与透传项。规划器用它校验依赖边；
        简单指标 Visitor 按它逐一建分支。它只描述输出契约，不携带 SQL 行数据。
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def output_query_elements(self) -> OrderedSet[MetricQueryElement]:
        """Similar to `output_metric_specs`, but as `MetricQueryElement`s.

        除指标引用外，还带上每个输出指标的分组和过滤上下文。
        """
        raise NotImplementedError

    @abstractmethod
    def accept(self, visitor: MetricQueryNodeVisitor[VisitorOutputT]) -> VisitorOutputT:
        """Helps implement visitors for type-specific handling."""
        raise NotImplementedError

    @cached_property
    @override
    def comparison_key(self) -> ComparisonKey:
        return (self.node_id, self.query_properties)

    def _create_output_query_element(self, metric_spec: MetricSpec) -> MetricQueryElement:
        """Create a query element for one output metric from this node."""
        return MetricQueryElement.create(
            metric_spec=metric_spec,
            group_by_item_specs=self.query_properties.group_by_item_specs,
            predicate_pushdown_state=self.query_properties.predicate_pushdown_state,
        )


class MetricQueryNodeVisitor(Generic[VisitorOutputT], ABC):
    """A visitor interface for type-safe handling of different node types.

    节点通过 accept() 分派到对应的 visit_* 方法；具体 Visitor 决定做校验、格式化还是计划转换。
    """

    @abstractmethod
    def visit_simple_metrics_query_node(self, node: SimpleMetricsQueryNode) -> VisitorOutputT:  # noqa: D102
        raise NotImplementedError

    @abstractmethod
    def visit_cumulative_metric_query_node(self, node: CumulativeMetricQueryNode) -> VisitorOutputT:  # noqa: D102
        raise NotImplementedError

    @abstractmethod
    def visit_conversion_metric_query_node(self, node: ConversionMetricQueryNode) -> VisitorOutputT:  # noqa: D102
        raise NotImplementedError

    @abstractmethod
    def visit_derived_metrics_query_node(self, node: DerivedMetricsQueryNode) -> VisitorOutputT:  # noqa: D102
        raise NotImplementedError

    @abstractmethod
    def visit_top_level_query_node(self, node: TopLevelQueryNode) -> VisitorOutputT:  # noqa: D102
        raise NotImplementedError


@fast_frozen_dataclass(order=False)
class BaseMetricQueryNode(MetricQueryNode, ABC):
    """Represents a query containing metrics that do not depend on other metric queries."""

    @cached_property
    @override
    def labels(self) -> OrderedSet[MetricFlowGraphLabel]:
        return super().labels.union((BaseMetricQueryLabel.get_instance(),))


@fast_frozen_dataclass(order=False)
class SimpleMetricsQueryNode(BaseMetricQueryNode):
    """Represents a query for simple metrics.

    This represents a SQL query that reads from a single semantic model. In many cases, it's possible to compute
    multiple simple metrics. However, modifiers such as filters can require separate queries.
    """

    # 上游按 semantic model 将可共用来源的简单指标组织到同一求值节点；
    # 此字段保留该来源身份供裁剪、计划展示等使用。本 Visitor 并不直接凭它生成 FROM 表。
    model_id: SemanticModelId
    # 这一组待计算的指标引用。它决定 output_metric_specs，Visitor 对每项构建独立分支；
    # 优化器随后才判断能否共享源扫描。过滤修饰不同的指标不能随意合成同一查询。
    metric_specs: FrozenOrderedSet[MetricSpec]

    @staticmethod
    def create(
        model_id: SemanticModelId, metric_specs: Iterable[MetricSpec], query_properties: MetricQueryPropertySet
    ) -> SimpleMetricsQueryNode:
        """Create a node that computes one or more simple metrics from the same semantic model."""
        return SimpleMetricsQueryNode(
            model_id=model_id,
            node_id=SequentialIdGenerator.create_next_id(StaticIdPrefix.METRIC_EVALUATION_NODE__SIMPLE_METRICS_QUERY),
            metric_specs=FrozenOrderedSet.from_iterable(metric_specs),
            query_properties=query_properties,
        )

    def __post_init__(self) -> None:  # noqa: D105
        if not __debug__:
            return

        modifier_to_specs: defaultdict[MetricModifier, list[MetricSpec]] = defaultdict(list)
        for metric_spec in self.metric_specs:
            modifier_to_specs[metric_spec.metric_modifier].append(metric_spec)

        assert len(modifier_to_specs) == 1, LazyFormat(
            "All metric specs should map to exactly one modifier due to SQL query limitations (e.g. each "
            "unique filter requires a separate SQL query with the appropriate `WHERE` clause).",
            modifier_to_specs=modifier_to_specs,
        )

    @override
    def pruned(self, allowed_specs: Set[MetricSpec]) -> SimpleMetricsQueryNode:
        filtered_metric_specs = FrozenOrderedSet(
            (metric_spec for metric_spec in self.metric_specs if metric_spec in allowed_specs)
        )
        if len(filtered_metric_specs) == 0:
            raise RuntimeError(
                LazyFormat(
                    "Can't return a copy if all metric specs are filtered out",
                    metric_specs=self.metric_specs,
                    allowed_specs=allowed_specs,
                )
            )
        if self.metric_specs == filtered_metric_specs:
            return self

        return SimpleMetricsQueryNode.create(self.model_id, filtered_metric_specs, self.query_properties)

    @cached_property
    @override
    def node_descriptor(self) -> MetricFlowGraphNodeDescriptor:
        return MetricFlowGraphNodeDescriptor(node_name=self.node_id.str_value, cluster_name=None)

    @cached_property
    @override
    def comparison_key(self) -> ComparisonKey:
        return super().comparison_key + (self.metric_specs,)

    @cached_property
    @override
    def output_metric_specs(self) -> OrderedSet[MetricSpec]:
        """简单指标节点的输出，就是本节点计算的 metric_specs。"""
        return self.metric_specs

    @override
    def accept(self, visitor: MetricQueryNodeVisitor[VisitorOutputT]) -> VisitorOutputT:
        return visitor.visit_simple_metrics_query_node(self)

    @override
    def pretty_format(self, format_context: PrettyFormatContext) -> Optional[str]:
        return format_context.formatter.pretty_format_object_by_parts(
            class_name=self.__class__.__name__,
            field_mapping={
                "node_name": self.node_descriptor.node_name,
                "model_id": self.model_id,
                "metric_specs": self.metric_specs,
                "query_properties": self.query_properties,
            },
        )

    @cached_property
    @override
    def output_query_elements(self) -> OrderedSet[MetricQueryElement]:
        return FrozenOrderedSet(self._create_output_query_element(metric_spec) for metric_spec in self.metric_specs)


@fast_frozen_dataclass(order=False)
class CumulativeMetricQueryNode(BaseMetricQueryNode):
    """Represents a query for a cumulative metric.

    Currently, each cumulative metric is computed in a separate SQL query for simplicity.
    """

    metric_spec: MetricSpec

    @staticmethod
    def create(metric_spec: MetricSpec, query_properties: MetricQueryPropertySet) -> CumulativeMetricQueryNode:
        """Create a node that computes one cumulative metric."""
        return CumulativeMetricQueryNode(
            node_id=SequentialIdGenerator.create_next_id(
                StaticIdPrefix.METRIC_EVALUATION_NODE__CUMULATIVE_METRIC_QUERY
            ),
            metric_spec=metric_spec,
            query_properties=query_properties,
        )

    @override
    def pruned(self, allowed_specs: Set[MetricSpec]) -> CumulativeMetricQueryNode:
        if self.metric_spec not in allowed_specs:
            raise RuntimeError(
                LazyFormat(
                    "Can't return a copy if all metric specs are filtered out",
                    metric_spec=self.metric_spec,
                    allowed_specs=allowed_specs,
                )
            )
        return self

    @cached_property
    @override
    def node_descriptor(self) -> MetricFlowGraphNodeDescriptor:
        return MetricFlowGraphNodeDescriptor(node_name=self.node_id.str_value, cluster_name=None)

    @cached_property
    @override
    def comparison_key(self) -> ComparisonKey:
        return super().comparison_key + (self.metric_spec,)

    @cached_property
    @override
    def output_metric_specs(self) -> OrderedSet[MetricSpec]:
        """累计指标节点只输出自身的一个 metric_spec。"""
        return FrozenOrderedSet((self.metric_spec,))

    @override
    def accept(self, visitor: MetricQueryNodeVisitor[VisitorOutputT]) -> VisitorOutputT:
        return visitor.visit_cumulative_metric_query_node(self)

    @override
    def pretty_format(self, format_context: PrettyFormatContext) -> Optional[str]:
        return format_context.formatter.pretty_format_object_by_parts(
            class_name=self.__class__.__name__,
            field_mapping={
                "node_name": self.node_descriptor.node_name,
                "metric_spec": self.metric_spec,
                "query_properties": self.query_properties,
            },
        )

    @cached_property
    @override
    def output_query_elements(self) -> OrderedSet[MetricQueryElement]:
        return FrozenOrderedSet((self._create_output_query_element(self.metric_spec),))


@fast_frozen_dataclass(order=False)
class ConversionMetricQueryNode(BaseMetricQueryNode):
    """Represents a query for a conversion metric.

    Currently, each conversion metric is computed in a separate SQL query for simplicity.
    """

    metric_spec: MetricSpec

    @staticmethod
    def create(metric_spec: MetricSpec, query_properties: MetricQueryPropertySet) -> ConversionMetricQueryNode:
        """Create a node that computes one conversion metric."""
        return ConversionMetricQueryNode(
            node_id=SequentialIdGenerator.create_next_id(
                StaticIdPrefix.METRIC_EVALUATION_NODE__CONVERSION_METRIC_QUERY
            ),
            metric_spec=metric_spec,
            query_properties=query_properties,
        )

    @override
    def pruned(self, allowed_specs: Set[MetricSpec]) -> ConversionMetricQueryNode:
        if self.metric_spec not in allowed_specs:
            raise RuntimeError(
                LazyFormat(
                    "Can't return a copy if all metric specs are filtered out",
                    metric_spec=self.metric_spec,
                    allowed_specs=allowed_specs,
                )
            )
        return self

    @cached_property
    @override
    def node_descriptor(self) -> MetricFlowGraphNodeDescriptor:
        return MetricFlowGraphNodeDescriptor(node_name=self.node_id.str_value, cluster_name=None)

    @cached_property
    @override
    def comparison_key(self) -> ComparisonKey:
        return super().comparison_key + (self.metric_spec,)

    @cached_property
    @override
    def output_metric_specs(self) -> OrderedSet[MetricSpec]:
        """转换指标节点只输出自身的一个 metric_spec。"""
        return FrozenOrderedSet((self.metric_spec,))

    @override
    def accept(self, visitor: MetricQueryNodeVisitor[VisitorOutputT]) -> VisitorOutputT:
        return visitor.visit_conversion_metric_query_node(self)

    @override
    def pretty_format(self, format_context: PrettyFormatContext) -> Optional[str]:
        return format_context.formatter.pretty_format_object_by_parts(
            class_name=self.__class__.__name__,
            field_mapping={
                "node_name": self.node_descriptor.node_name,
                "metric_spec": self.metric_spec,
                "query_properties": self.query_properties,
            },
        )

    @cached_property
    @override
    def output_query_elements(self) -> OrderedSet[MetricQueryElement]:
        return FrozenOrderedSet((self._create_output_query_element(self.metric_spec),))


@fast_frozen_dataclass(order=False)
class DerivedMetricsQueryNode(MetricQueryNode):
    """Represents a query for derived metric.

    ratio 和 derived 指标在此层都可能表现为依赖其他指标的查询节点。
    """

    # 本节点实际计算出的指标；与下面原样透传的指标共同组成 output_metric_specs。
    computed_metric_specs: FrozenOrderedSet[MetricSpec]
    # 从输入查询原样带到输出的指标；它们也属于 output_metric_specs。
    passthrough_metric_specs: FrozenOrderedSet[MetricSpec]

    def __post_init__(self) -> None:  # noqa: D105
        if not __debug__:
            return
        assert len(self.computed_metric_specs) > 0
        for passthrough_metric_spec in self.passthrough_metric_specs:
            assert passthrough_metric_spec.metric_modifier.alias is None, LazyFormat(
                "Passthrough metrics with an alias are not supported to simplify alias-collision handling",
                passthrough_metric_spec=passthrough_metric_spec,
            )

    @staticmethod
    def create(
        computed_metric_specs: Iterable[MetricSpec],
        passthrough_metric_specs: Iterable[MetricSpec],
        query_properties: MetricQueryPropertySet,
    ) -> DerivedMetricsQueryNode:
        """Create a node that computes selected derived metrics plus passthrough metrics."""
        return DerivedMetricsQueryNode(
            node_id=SequentialIdGenerator.create_next_id(StaticIdPrefix.METRIC_EVALUATION_NODE__DERIVED_METRIC_QUERY),
            computed_metric_specs=FrozenOrderedSet.from_iterable(computed_metric_specs),
            passthrough_metric_specs=FrozenOrderedSet.from_iterable(passthrough_metric_specs),
            query_properties=query_properties,
        )

    @override
    def pruned(self, allowed_specs: Set[MetricSpec]) -> DerivedMetricsQueryNode:
        filtered_computed_metric_specs = self.computed_metric_specs.intersection(allowed_specs)
        if not filtered_computed_metric_specs:
            raise RuntimeError(
                LazyFormat(
                    "Can't return a copy if all computed metric specs are filtered out",
                    computed_metric_specs=self.computed_metric_specs,
                    allowed_specs=allowed_specs,
                )
            )

        filtered_passthrough_metric_specs = FrozenOrderedSet(
            passthrough_metric_spec
            for passthrough_metric_spec in self.passthrough_metric_specs
            if passthrough_metric_spec in allowed_specs
        )

        if self.passthrough_metric_specs == filtered_passthrough_metric_specs:
            return self

        return DerivedMetricsQueryNode.create(
            computed_metric_specs=filtered_computed_metric_specs,
            passthrough_metric_specs=filtered_passthrough_metric_specs,
            query_properties=self.query_properties,
        )

    @cached_property
    @override
    def node_descriptor(self) -> MetricFlowGraphNodeDescriptor:
        return MetricFlowGraphNodeDescriptor(node_name=self.node_id.str_value, cluster_name=None)

    @cached_property
    @override
    def comparison_key(self) -> ComparisonKey:
        return super().comparison_key + (
            self.computed_metric_specs,
            self.passthrough_metric_specs,
        )

    @cached_property
    @override
    def output_metric_specs(self) -> OrderedSet[MetricSpec]:
        """派生指标节点同时输出新计算的指标和从输入节点透传的指标。"""
        return FrozenOrderedSet(itertools.chain(self.computed_metric_specs, self.passthrough_metric_specs))

    @override
    def accept(self, visitor: MetricQueryNodeVisitor[VisitorOutputT]) -> VisitorOutputT:
        return visitor.visit_derived_metrics_query_node(self)

    @override
    def pretty_format(self, format_context: PrettyFormatContext) -> Optional[str]:
        return format_context.formatter.pretty_format_object_by_parts(
            class_name=self.__class__.__name__,
            field_mapping={
                "node_name": self.node_descriptor.node_name,
                "computed_metric_specs": self.computed_metric_specs,
                "passthrough_metric_specs": self.passthrough_metric_specs,
                "query_properties": self.query_properties,
            },
        )

    @cached_property
    @override
    def output_query_elements(self) -> OrderedSet[MetricQueryElement]:
        return FrozenOrderedSet(
            (
                self._create_output_query_element(metric_spec)
                for metric_spec in itertools.chain(self.computed_metric_specs, self.passthrough_metric_specs)
            )
        )


@fast_frozen_dataclass(order=False)
class TopLevelQueryNode(MetricQueryNode):
    """Describes the metrics queried at the top-level.

    The top-level generally represents the metrics that are queried by the user. This node provides a single
    entry point for dependency traversal.

    代表用户此次请求的最终指标集合，本身不计算指标，只接收依赖节点的结果。
    """

    # The actual computation of the metrics is modeled through the dependencies, so this node can be modeled as only
    # passing through metrics computed in subqueries.
    # 用户最终请求的指标，由依赖节点计算后在这里汇总输出。
    passthrough_metric_specs: FrozenOrderedSet[MetricSpec]

    def __post_init__(self) -> None:  # noqa: D105
        assert len(self.passthrough_metric_specs) > 0, "A top-level query must have at least one metric"

    @staticmethod
    def create(
        passthrough_metric_specs: Iterable[MetricSpec], query_properties: MetricQueryPropertySet
    ) -> TopLevelQueryNode:
        """Create the top-level query node that exposes requested metrics."""
        return TopLevelQueryNode(
            node_id=SequentialIdGenerator.create_next_id(StaticIdPrefix.METRIC_EVALUATION_NODE__TOP_LEVEL_QUERY),
            passthrough_metric_specs=FrozenOrderedSet.from_iterable(passthrough_metric_specs),
            query_properties=query_properties,
        )

    @override
    def pruned(self, allowed_specs: Set[MetricSpec]) -> TopLevelQueryNode:
        filtered_passthrough_metric_specs = FrozenOrderedSet(
            (metric_spec for metric_spec in self.passthrough_metric_specs if metric_spec in allowed_specs)
        )
        if len(filtered_passthrough_metric_specs) == 0:
            raise RuntimeError(
                LazyFormat(
                    "Can't return a copy if all metric specs are filtered out",
                    passthrough_metric_specs=self.passthrough_metric_specs,
                    allowed_specs=allowed_specs,
                )
            )
        if self.passthrough_metric_specs == filtered_passthrough_metric_specs:
            return self

        return TopLevelQueryNode.create(
            passthrough_metric_specs=filtered_passthrough_metric_specs,
            query_properties=self.query_properties,
        )

    @cached_property
    @override
    def node_descriptor(self) -> MetricFlowGraphNodeDescriptor:
        return MetricFlowGraphNodeDescriptor(node_name=self.node_id.str_value, cluster_name=None)

    @cached_property
    @override
    def comparison_key(self) -> ComparisonKey:
        return super().comparison_key + (self.passthrough_metric_specs,)

    @cached_property
    @override
    def output_metric_specs(self) -> OrderedSet[MetricSpec]:
        """顶层节点只透传用户请求的指标，不在此节点重新计算。"""
        return self.passthrough_metric_specs

    @cached_property
    @override
    def labels(self) -> OrderedSet[MetricFlowGraphLabel]:
        return super().labels.union((TopLevelQueryLabel.get_instance(),))

    @override
    def accept(self, visitor: MetricQueryNodeVisitor[VisitorOutputT]) -> VisitorOutputT:
        return visitor.visit_top_level_query_node(self)

    @override
    def pretty_format(self, format_context: PrettyFormatContext) -> Optional[str]:
        return format_context.formatter.pretty_format_object_by_parts(
            class_name=self.__class__.__name__,
            field_mapping={
                "node_name": self.node_descriptor.node_name,
                "passthrough_metric_specs": self.passthrough_metric_specs,
                "query_properties": self.query_properties,
            },
        )

    @cached_property
    @override
    def output_query_elements(self) -> OrderedSet[MetricQueryElement]:
        return FrozenOrderedSet(
            self._create_output_query_element(metric_spec) for metric_spec in self.passthrough_metric_specs
        )
