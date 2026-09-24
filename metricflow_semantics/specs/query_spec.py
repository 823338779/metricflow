from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, Tuple

from metricflow_semantics.errors.error_classes import MetricFlowInternalError
from metricflow_semantics.filters.time_constraint import TimeRangeConstraint
from metricflow_semantics.query.group_by_item.filter_spec_resolution.filter_spec_lookup import (
    FilterSpecResolutionLookUp,
)
from metricflow_semantics.specs.dimension_spec import DimensionSpec
from metricflow_semantics.specs.entity_spec import EntitySpec
from metricflow_semantics.specs.group_by_metric_spec import GroupByMetricSpec
from metricflow_semantics.specs.instance_spec import InstanceSpec
from metricflow_semantics.specs.linkable_spec_set import LinkableSpecSet
from metricflow_semantics.specs.metric_spec import MetricSpec
from metricflow_semantics.specs.order_by_spec import OrderBySpec
from metricflow_semantics.specs.time_dimension_spec import TimeDimensionSpec
from metricflow_semantics.toolkit.dataclass_helpers import fast_frozen_dataclass
from metricflow_semantics.toolkit.mf_logging.lazy_formattable import LazyFormat

from metricflow_semantic_interfaces.dataclass_serialization import SerializableDataclass
from metricflow_semantic_interfaces.implementations.filters.where_filter import PydanticWhereFilterIntersection
from metricflow_semantic_interfaces.protocols import WhereFilterIntersection


@fast_frozen_dataclass()
class InputSpecOrder:
    """Represents the order of specs in the query input.

    保留用户输入顺序，供最终 SELECT 列排序使用。
    """

    # 解析为 Spec 后仍保留用户填写的分组项顺序；OutputColumnOrderer 据此排列最终 SELECT，
    # 与实际 GROUP BY 的集合及计算粒度分开保存。
    group_by_item_specs: tuple[InstanceSpec, ...]
    # 最终结果的指标列按此顺序呈现；即使优化器重排了计算分支也不应改变用户看到的列序。
    metric_specs: tuple[MetricSpec, ...]

    def __post_init__(self) -> None:  # noqa: D105
        spec_to_count: defaultdict[InstanceSpec, int] = defaultdict(int)

        for spec in self.group_by_item_specs + self.metric_specs:
            spec_to_count[spec] += 1

        duplicate_specs = {spec: count for spec, count in spec_to_count.items() if count > 1}

        if duplicate_specs:
            raise MetricFlowInternalError(
                LazyFormat(
                    "Duplicate specs found in the order",
                    duplicate_specs=duplicate_specs,
                    input_spec_order=self,
                )
            )


@dataclass(frozen=True)
class MetricFlowQuerySpec(SerializableDataclass):
    """Specs needed for running a query.

    解析和校验后的查询规格；Spec 是查询中引用的指标或分组项，并非 YAML 定义本身。
    """

    # 查询真正要求的指标 Spec；Builder 由此展开简单/派生依赖、建计算分支，
    # 还用于判断输出是否满足请求。它不是指标 YAML 的完整定义。
    metric_specs: Tuple[MetricSpec, ...] = ()
    # 普通维度需求；源选择器据此判断本地可得还是必须通过实体关联取得。
    dimension_specs: Tuple[DimensionSpec, ...] = ()
    # 请求作为结果列的实体；与隐含的 JOIN 键不同，必须出现在最终输出粒度中。
    entity_specs: Tuple[EntitySpec, ...] = ()
    # 例如 metric_time__day；决定时间变换及聚合粒度，也可能影响时间脊选择。
    time_dimension_specs: Tuple[TimeDimensionSpec, ...] = ()
    # 特殊的“按指标结果分组”需求；构建时会要求对应分支输出 GroupByMetricInstance。
    group_by_metric_specs: Tuple[GroupByMetricSpec, ...] = ()
    # 结果行的排序要求；数据流尾部转成 OrderByLimitNode，不改变指标的计算公式。
    order_by_specs: Tuple[OrderBySpec, ...] = ()
    # 限制参与计算的时间数据；能否下推到源层由 PredicatePushdownState 决定。
    time_range_constraint: Optional[TimeRangeConstraint] = None
    # 结果最多返回的行数。
    limit: Optional[int] = None
    # 用户过滤条件的组合语义；Builder 会结合解析结果决定在哪个分支、聚合前后执行。
    filter_intersection: WhereFilterIntersection = field(
        default_factory=lambda: PydanticWhereFilterIntersection(where_filters=[])
    )
    # 将 WHERE 中的语义名称对应到具体 Spec；构造过滤节点时不需再猜该名称指向哪个模型字段。
    filter_spec_resolution_lookup: FilterSpecResolutionLookUp = FilterSpecResolutionLookUp.empty_instance()
    # 仅查询最小/最大值的特殊模式。
    min_max_only: bool = False
    # 是否对分组项执行 GROUP BY；带指标的常规查询为 True。
    apply_group_by: bool = True

    # Use the following to track the order in which specs were provided in the query input.
    input_spec_order: InputSpecOrder = field(
        default_factory=lambda: InputSpecOrder(group_by_item_specs=(), metric_specs=())
    )

    @property
    def linkable_specs(self) -> LinkableSpecSet:  # noqa: D102
        """汇总所有可关联的分组项，包括普通维度、时间维度和实体。"""
        return LinkableSpecSet(
            dimension_specs=self.dimension_specs,
            time_dimension_specs=self.time_dimension_specs,
            entity_specs=self.entity_specs,
            group_by_metric_specs=self.group_by_metric_specs,
        )

    def with_time_range_constraint(self, time_range_constraint: Optional[TimeRangeConstraint]) -> MetricFlowQuerySpec:
        """Return a query spec that's the same as self but with a different time_range_constraint."""
        return MetricFlowQuerySpec(
            metric_specs=self.metric_specs,
            dimension_specs=self.dimension_specs,
            entity_specs=self.entity_specs,
            time_dimension_specs=self.time_dimension_specs,
            group_by_metric_specs=self.group_by_metric_specs,
            order_by_specs=self.order_by_specs,
            time_range_constraint=time_range_constraint,
            limit=self.limit,
            filter_intersection=self.filter_intersection,
            filter_spec_resolution_lookup=self.filter_spec_resolution_lookup,
            min_max_only=self.min_max_only,
            apply_group_by=self.apply_group_by,
            input_spec_order=self.input_spec_order,
        )
