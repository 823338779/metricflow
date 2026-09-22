from __future__ import annotations

import logging
from functools import cached_property
from typing import Optional

from metricflow_semantics.semantic_graph.model_id import SemanticModelId
from metricflow_semantics.toolkit.dataclass_helpers import fast_frozen_dataclass
from metricflow_semantics.toolkit.mf_type_aliases import AnyLengthTuple

from metricflow_semantic_interfaces.protocols import (
    MeasureAggregationParameters,
    NonAdditiveDimensionParameters,
    WhereFilterIntersection,
)
from metricflow_semantic_interfaces.references import MetricReference
from metricflow_semantic_interfaces.type_enums import AggregationType, TimeGranularity

logger = logging.getLogger(__name__)


@fast_frozen_dataclass()
class SimpleMetricInputAggregation:
    """See `SimpleMetricInput`.

    百分位聚合的附加参数；只有相应聚合类型才会使用。
    """

    # 要计算的百分位数值。
    percentile: Optional[float] = None
    # 是否使用离散百分位计算。
    use_discrete_percentile: bool = False
    # 是否使用近似百分位计算。
    use_approximate_percentile: bool = False

    @staticmethod
    def create_from_pydantic(  # noqa: D102
        aggregation: Optional[MeasureAggregationParameters],
    ) -> Optional[SimpleMetricInputAggregation]:
        if aggregation is None:
            return None
        return SimpleMetricInputAggregation(
            percentile=aggregation.percentile,
            use_discrete_percentile=aggregation.use_discrete_percentile,
            use_approximate_percentile=aggregation.use_approximate_percentile,
        )


@fast_frozen_dataclass()
class SimpleMetricInputNonAdditiveDimension:
    """See `SimpleMetricInput`.

    描述不能跨指定维度直接求和的简单指标如何选取窗口值。
    """

    # 不允许直接跨其求和的维度名称。
    name: str
    # 在窗口内取最早或最晚等值的规则。
    window_choice: AggregationType
    # 计算窗口时还需保留的分组字段。
    window_groupings: AnyLengthTuple[str]

    @staticmethod
    def create_from_non_additive_dimension(  # noqa: D102
        non_additive_dimension: Optional[NonAdditiveDimensionParameters],
    ) -> Optional[SimpleMetricInputNonAdditiveDimension]:
        if non_additive_dimension is None:
            return None

        return SimpleMetricInputNonAdditiveDimension(
            name=non_additive_dimension.name,
            window_choice=non_additive_dimension.window_choice,
            window_groupings=tuple(non_additive_dimension.window_groupings),
        )


@fast_frozen_dataclass()
class SimpleMetricInput:
    """Represents the input arguments to construct a simple metric.

    This class should contain all relevant values from the semantic manifest for a simple metric. i.e. use this
    instead of fetching the `Metric` from the manifest.

    These fields are based on the current Pydantic classes, but they can be better organized / consolidated.

    它是从指标 YAML 解析出的计算输入，不是查询时的 MetricSpec。
    """

    # 定义侧的唯一名称：Builder 由 MetricSpec.reference 找到指标后，用它取得此输入配置。
    name: str
    # SQL 聚合函数的选择依据；同一个 expr 配 SUM、COUNT 或 COUNT_DISTINCT 会产生不同结果。
    agg: AggregationType
    # 聚合前逐行计算的值，如金额列或计数用的 1；SQL Visitor 把它放进源侧 SELECT。
    expr: str
    # 只有百分位类聚合需要的算法参数，决定精确/近似、离散/连续及百分位位置。
    agg_params: Optional[SimpleMetricInputAggregation]
    # 若数值不能沿某维度直接求和，Builder 据此先选窗口内的有效记录再聚合。
    non_additive_dimension: Optional[SimpleMetricInputNonAdditiveDimension]
    # 源模型中作为此指标时间轴的列名；映射为逻辑 metric_time 并用于时间过滤。
    agg_time_dimension_name: str
    # 该时间列的基础粒度；请求更粗粒度时由此生成时间变换及对应 GROUP BY。
    agg_time_dimension_grain: TimeGranularity
    # 选源时限定哪个语义模型能提供该输入；它表示语义来源，不是 SQL 表名。
    model_id: SemanticModelId
    # 有时间维度请求时，决定是否安排时间脊 JOIN 以补齐时间轴；无时间分组时不触发该步骤。
    join_to_timespine: bool
    # 聚合输出无值时的业务默认值，沿 SimpleMetricInputSpec/聚合节点传到最终 COALESCE。
    fill_nulls_with: Optional[int]
    # 指标定义内置条件；Builder 将它与查询 WHERE 合并，但仍需判断能否安全放到聚合前。
    filter: WhereFilterIntersection

    @cached_property
    def metric_reference(self) -> MetricReference:  # noqa: D102
        return MetricReference(self.name)
