from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Optional, Tuple

from metricflow_semantics.model.semantics.simple_metric_input import SimpleMetricInput
from metricflow_semantics.specs.instance_spec import InstanceSpec, InstanceSpecVisitor
from metricflow_semantics.specs.linkable_spec_set import LinkableSpecSet
from metricflow_semantics.specs.time_dimension_spec import TimeDimensionSpec
from metricflow_semantics.specs.time_window import TimeWindow
from metricflow_semantics.specs.where_filter.where_filter_spec import WhereFilterSpec
from metricflow_semantics.sql.sql_join_type import SqlJoinType
from metricflow_semantics.toolkit.collections.ordered_set import FrozenOrderedSet
from metricflow_semantics.toolkit.dataclass_helpers import fast_frozen_dataclass
from metricflow_semantics.toolkit.visitor import VisitorOutputT

from metricflow_semantic_interfaces.type_enums import TimeGranularity


@dataclass(frozen=True)
class SimpleMetricInputSpec(InstanceSpec):  # noqa: D101
    # 指向源侧 SELECT 产出的聚合前输入列；AggregateSimpleMetricInputsNode 按此列聚合，
    # 它与最终对外的 MetricSpec 分属两个计算阶段。
    element_name: str
    # 复制定义侧的空值策略；聚合节点保存映射，ComputeMetricsNode 才决定是否渲染 COALESCE。
    fill_nulls_with: Optional[int] = None

    @property
    def dunder_name(self) -> str:  # noqa: D102
        return self.element_name

    def accept(self, visitor: InstanceSpecVisitor[VisitorOutputT]) -> VisitorOutputT:  # noqa: D102
        return visitor.visit_simple_metric_input_spec(self)


@dataclass(frozen=True)
class CumulativeDescription:
    """If a simple metric is a part of a cumulative metric, this represents the associated parameters."""

    # 累计窗口大小，例如过去 7 天；普通简单指标为 None。
    cumulative_window: Optional[TimeWindow]
    # 从某时间粒度起始点累计；普通简单指标为 None。
    cumulative_grain_to_date: Optional[TimeGranularity]


@fast_frozen_dataclass()
class SimpleMetricRecipe:
    """Describes how to build a simple metric but with modifications.

    _build_simple_metric_recipe() 的结果：把简单指标定义、分组、过滤和时间偏移整理为构建步骤。
    """

    # 定义侧的原始聚合信息；Builder 读它决定源模型、聚合函数与源表达式。
    simple_metric_input: SimpleMetricInput

    # 查询侧要求的输出粒度；源选择与聚合必须满足，缺失项可经实体 JOIN 补齐。
    queried_linkable_specs: LinkableSpecSet
    # 请求中与此指标时间轴对应的时间项；累计/偏移计算据此决定时间脊连接键与粒度。
    queried_agg_time_dimension_specs: FrozenOrderedSet[TimeDimensionSpec]

    # 允许源侧提前执行的条件，减少进入聚合的行；不能包含会截断累计窗口的条件。
    pre_aggregation_filter_specs: Tuple[WhereFilterSpec, ...]
    # 累计指标所需的窗口说明；普通简单指标为 None。
    cumulative_description: Optional[CumulativeDescription]
    # For metrics with a time offset or with `join_to_timespine`, descriptions of how the time-spin join should
    # be applied.
    # 按配置决定是在聚合前还是聚合后连接时间脊；一般查询中都为 None。
    before_aggregation_time_spine_join_description: Optional[JoinToTimeSpineDescription]
    # 聚合后连接时间脊的方案；未配置时为 None。
    after_aggregation_time_spine_join_description: Optional[JoinToTimeSpineDescription]

    # Filters intentionally deferred from pre-aggregation application. They are applied after aggregation and after
    # an optional post-aggregation time-spine join.
    # 被刻意延迟的条件；若提前过滤，累计窗口或补齐日期可能被错误截断。
    deferred_filter_specs: Tuple[WhereFilterSpec, ...]

    @cached_property
    def combined_filter_specs(self) -> Sequence[WhereFilterSpec]:
        """All filters that are referenced in the recipe."""
        combined_filter_specs = list(self.pre_aggregation_filter_specs)

        for time_spine_join_description in (
            self.before_aggregation_time_spine_join_description,
            self.after_aggregation_time_spine_join_description,
        ):
            if time_spine_join_description is not None:
                combined_filter_specs.extend(time_spine_join_description.time_spine_filter_specs)

        combined_filter_specs.extend(self.deferred_filter_specs)
        return tuple(combined_filter_specs)


@dataclass(frozen=True)
class JoinToTimeSpineDescription:
    """Describes how a time spine join should be performed.

    只描述时间脊 JOIN 的方式和偏移；真正的 Dataflow 节点随后才会创建。
    """

    # INNER 用于偏移，LEFT OUTER 可用于补齐时间脊日期。
    join_type: SqlJoinType
    # 偏移的窗口，如前 7 天；两种偏移配置通常只会用到其中一种。
    offset_window: Optional[TimeWindow]
    # 偏移至指定时间粒度的边界，例如上月同期。
    offset_to_grain: Optional[TimeGranularity]
    # 应作用于时间脊而非原始事实表的过滤条件。
    time_spine_filter_specs: Tuple[WhereFilterSpec, ...] = ()

    @property
    def standard_offset_window(self) -> Optional[TimeWindow]:
        """Return the offset window if it uses a standard granularity."""
        if self.offset_window and self.offset_window.is_standard_granularity:
            return self.offset_window
        return None

    @property
    def custom_offset_window(self) -> Optional[TimeWindow]:
        """Return the offset window if it uses a custom granularity."""
        if self.offset_window and not self.offset_window.is_standard_granularity:
            return self.offset_window
        return None

    @property
    def uses_offset(self) -> bool:
        """Return True if the simple-metric input uses an offset."""
        return self.offset_window is not None or self.offset_to_grain is not None
