from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, Sequence

from metricflow_semantics.model.semantics.simple_metric_input import SimpleMetricInput
from metricflow_semantics.semantic_graph.model_id import SemanticModelId
from metricflow_semantics.specs.non_additive_dimension_spec import NonAdditiveDimensionSpec
from metricflow_semantics.specs.simple_metric_input_spec import SimpleMetricInputSpec
from metricflow_semantics.toolkit.dataclass_helpers import fast_frozen_dataclass
from metricflow_semantics.toolkit.mf_logging.lazy_formattable import LazyFormat
from metricflow_semantics.toolkit.mf_type_aliases import AnyLengthTuple
from metricflow_semantics.toolkit.syntactic_sugar import mf_first_item

from metricflow_semantic_interfaces.references import TimeDimensionReference
from metricflow_semantic_interfaces.type_enums import TimeGranularity


@dataclass(frozen=True)
class SimpleMetricInputSpecProperties:
    """Input dataclass for grouping properties of a sequence of `SimpleMetricInputSpec`s.

    聚合前把同一组简单指标输入的公共来源、时间列和非可加设置放在一起。
    """

    # 本组要一起从源模型读取的聚合前输入；选源器必须找到能同时提供它们的节点。
    simple_metric_input_specs: AnyLengthTuple[SimpleMetricInputSpec]
    # 限定输入来自哪个语义模型，防止把同名 measure 错配到其他模型的源节点。
    semantic_model_name: str
    # 这一组输入共享的时间轴；决定要把哪列转换为 metric_time 并应用时间范围。
    agg_time_dimension: TimeDimensionReference
    # 时间轴的基础粒度；请求更粗粒度时以此确定可用的时间变换。
    agg_time_dimension_grain: TimeGranularity
    # 若有库存类非可加限制，必须先按该规则挑选记录，不能与普通求和分支随意合并。
    non_additive_dimension_spec: Optional[NonAdditiveDimensionSpec] = None

    @staticmethod
    def create_from_simple_metric_inputs(  # noqa: D102
        simple_metric_inputs: Sequence[SimpleMetricInput],
    ) -> SimpleMetricInputSpecProperties:
        if len(simple_metric_inputs) == 0:
            raise ValueError("No simple-metric inputs provided")

        key_to_inputs: defaultdict[_SimpleMetricInputGroupingKey, list[SimpleMetricInput]] = defaultdict(list)
        for simple_metric_input in simple_metric_inputs:
            key_to_inputs[_SimpleMetricInputGroupingKey.create_from_simple_metric_input(simple_metric_input)].append(
                simple_metric_input
            )
        if len(key_to_inputs) > 1:
            raise ValueError(
                LazyFormat(
                    "The given simple-metric inputs do not have the same grouping key", key_to_inputs=key_to_inputs
                )
            )
        common_key = mf_first_item(key_to_inputs)

        return SimpleMetricInputSpecProperties(
            simple_metric_input_specs=tuple(
                SimpleMetricInputSpec(
                    element_name=simple_metric_input.name,
                )
                for simple_metric_input in simple_metric_inputs
            ),
            semantic_model_name=common_key.model_id.model_name,
            agg_time_dimension=TimeDimensionReference(common_key.agg_time_dimension_name),
            agg_time_dimension_grain=common_key.agg_time_dimension_grain,
            non_additive_dimension_spec=common_key.non_additive_dimension_spec,
        )


@fast_frozen_dataclass()
class _SimpleMetricInputGroupingKey:
    # 只有来源、聚合时间及非可加设置一致的输入才能归为同一组。
    model_id: SemanticModelId
    agg_time_dimension_name: str
    agg_time_dimension_grain: TimeGranularity
    non_additive_dimension_spec: Optional[NonAdditiveDimensionSpec]

    @staticmethod
    def create_from_simple_metric_input(  # noqa: D102
        simple_metric_input: SimpleMetricInput,
    ) -> _SimpleMetricInputGroupingKey:
        return _SimpleMetricInputGroupingKey(
            model_id=simple_metric_input.model_id,
            agg_time_dimension_name=simple_metric_input.agg_time_dimension_name,
            agg_time_dimension_grain=simple_metric_input.agg_time_dimension_grain,
            non_additive_dimension_spec=NonAdditiveDimensionSpec.create_from_simple_metric_input(simple_metric_input),
        )
