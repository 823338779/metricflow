from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

from metricflow_semantics.specs.instance_spec import LinkableInstanceSpec
from metricflow_semantics.specs.linkable_spec_set import LinkableSpecSet
from metricflow_semantics.specs.metric_spec import MetricSpec
from metricflow_semantics.specs.simple_metric_input_spec import SimpleMetricInputSpec
from metricflow_semantics.toolkit.cache.lru_cache import LruCache
from metricflow_semantics.toolkit.collections.ordered_set import FrozenOrderedSet
from metricflow_semantics.toolkit.dataclass_helpers import fast_frozen_dataclass
from metricflow_semantics.toolkit.mf_type_aliases import AnyLengthTuple

from metricflow.dataflow.builder.source_node_recipe import SourceNodeRecipe
from metricflow.dataflow.dataflow_plan import DataflowPlanNode
from metricflow.dataflow.optimizer.dataflow_optimizer_factory import DataflowPlanOptimization
from metricflow.plan_conversion.node_processor import PredicatePushdownState

logger = logging.getLogger(__name__)


@fast_frozen_dataclass()
class DataflowPlanOptionSet:
    """Options that affect how a dataflow plan is built.

    构建数据流计划时传递的选项，同一个查询的不同分支可以使用不同选项。
    """

    # 决定建图后启用哪些重写（如 SOURCE_SCAN 合并分支）；也参与缓存键，
    # 因为优化选项不同，构建出的节点图不一定能安全共用。
    optimizations: frozenset[DataflowPlanOptimization]
    # 控制 ComputeMetricsNode 输出 MetricInstance 还是 GroupByMetricInstance；
    # 只传给需要作为“按指标分组”来源的直接分支，改变下游如何使用这一列。
    output_group_by_metric_instances: bool

    def with_output_group_by_metric_instances(self, output_group_by_metric_instances: bool) -> DataflowPlanOptionSet:
        """Return a copy with an updated `output_group_by_metric_instances` value."""
        return DataflowPlanOptionSet(
            optimizations=self.optimizations,
            output_group_by_metric_instances=output_group_by_metric_instances,
        )


@dataclass(frozen=True)
class FindSourceNodeRecipeInput:
    """Parameters for `DataflowPlanBuilder._find_source_node_recipe()`.

    查找“从哪个源节点读取、需要关联哪些维度”时使用的缓存键。
    """

    # 限定候选左侧源必须提供哪些聚合前输入；None 是仅寻找分组项来源的查询。
    simple_metric_input_specs: Optional[AnyLengthTuple[SimpleMetricInputSpec]]
    # 源选择器必须满足的可关联项；它判断哪些可从左侧直接取得、哪些需 JOIN、哪些不可查询。
    linkable_spec_set: LinkableSpecSet
    # 影响候选源可否安全提前过滤；同样的指标和维度在不同下推状态下可能选出不同方案。
    predicate_pushdown_state: PredicatePushdownState
    # 源方案也依赖优化配置，因此它与输入和过滤状态共同构成缓存键。
    optimizations: frozenset[DataflowPlanOptimization]


@dataclass(frozen=True)
class FindSourceNodeRecipeResult:
    """Result for `DataflowPlanBuilder._find_source_node_recipe()`.

    缓存的源节点方案；None 表示找不到满足所需指标和维度的关联路径。
    """

    source_node_recipe: Optional[SourceNodeRecipe]  # 命中后直接复用选源/JOIN 方案；None 也是已确认无解的结果。


@dataclass(frozen=True)
class BuildAnyMetricOutputNodeInput:
    """Parameters for `DataflowPlanBuilder._build_any_metric_output_node()`.

    visit_simple_metrics_query_node() 也用它缓存单个简单指标的数据流分支。
    """

    # 缓存键的语义部分：不仅区分指标名，还区分透传项、粒度和过滤状态。
    metric_query_descriptor: MetricQueryDescriptor
    # 结构重写选项改变可复用性，不能用另一套选项下构建的指标分支。
    optimizations: frozenset[DataflowPlanOptimization]


class DataflowPlanBuilderCache:
    """Cache for internal methods in `DataflowPlanBuilder`.

    缓存源节点选择和指标分支构建；与 Visitor 内按单指标建立的临时缓存用途不同。
    """

    def __init__(  # noqa: D107
        self, find_source_node_recipe_cache_size: int = 1000, build_any_metric_output_node_cache_size: int = 1000
    ) -> None:
        # 复用代价较高的候选源评估及 JOIN 规划；缓存无解结果也避免重复搜索。
        self._find_source_node_recipe_cache = LruCache[FindSourceNodeRecipeInput, FindSourceNodeRecipeResult](
            find_source_node_recipe_cache_size
        )
        # 复用已建好的指标分支；查找键必须覆盖会改变计算结果或节点结构的上下文。
        self._build_any_metric_output_node_cache = LruCache[BuildAnyMetricOutputNodeInput, DataflowPlanNode](
            build_any_metric_output_node_cache_size
        )

        assert find_source_node_recipe_cache_size > 0
        assert build_any_metric_output_node_cache_size > 0

    def get_find_source_node_recipe_result(  # noqa: D102
        self, cache_key: FindSourceNodeRecipeInput
    ) -> Optional[FindSourceNodeRecipeResult]:
        return self._find_source_node_recipe_cache.get(cache_key)

    def set_find_source_node_recipe_result(  # noqa: D102
        self, cache_key: FindSourceNodeRecipeInput, source_node_recipe: FindSourceNodeRecipeResult
    ) -> None:
        self._find_source_node_recipe_cache.set(cache_key, source_node_recipe)

    def get_build_any_metric_output_node_result(  # noqa: D102
        self, cache_key: BuildAnyMetricOutputNodeInput
    ) -> Optional[DataflowPlanNode]:
        return self._build_any_metric_output_node_cache.get(cache_key)

    def set_build_any_metric_output_node_result(  # noqa: D102
        self, cache_key: BuildAnyMetricOutputNodeInput, dataflow_plan_node: DataflowPlanNode
    ) -> None:
        self._build_any_metric_output_node_cache.set(cache_key, dataflow_plan_node)


@fast_frozen_dataclass()
class MetricQueryDescriptor:
    """Describes a metric query to use as a cache key.

    用四组属性区分指标计算请求；在简单指标 Visitor 中每次只放一个 computed_metric_spec。
    """

    # 需要在此分支生成表达式的指标；简单指标 Visitor 每次只放一个，便于按指标缓存与后续重组。
    computed_metric_specs: FrozenOrderedSet[MetricSpec]
    # 必须从依赖分支保留的现有指标；派生计算会消费它们，遗漏会让上游列被过早裁剪。
    passthrough_metric_specs: FrozenOrderedSet[MetricSpec]
    # 输出行的粒度，决定源选择与聚合 GROUP BY；不同粒度不能复用同一缓存分支。
    group_by_item_specs: FrozenOrderedSet[LinkableInstanceSpec]
    # 记录过滤是否已下推及还能否下推；进入缓存键，避免复用过滤语义不同的计算结果。
    predicate_pushdown_state: PredicatePushdownState

    @staticmethod
    def create(  # noqa: D102
        computed_metric_specs: Iterable[MetricSpec],
        passthrough_metric_specs: Iterable[MetricSpec],
        group_by_item_specs: Iterable[LinkableInstanceSpec],
        predicate_pushdown_state: PredicatePushdownState,
    ) -> MetricQueryDescriptor:
        return MetricQueryDescriptor(
            computed_metric_specs=FrozenOrderedSet.from_iterable(computed_metric_specs),
            passthrough_metric_specs=FrozenOrderedSet.from_iterable(passthrough_metric_specs),
            group_by_item_specs=FrozenOrderedSet.from_iterable(group_by_item_specs),
            predicate_pushdown_state=predicate_pushdown_state,
        )
