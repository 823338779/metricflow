from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Optional

from metricflow_semantics.semantic_graph.model_id import SemanticModelId
from metricflow_semantics.specs.instance_spec import LinkableInstanceSpec
from metricflow_semantics.specs.metric_spec import MetricSpec
from metricflow_semantics.specs.where_filter.where_filter_spec_factory import WhereFilterSpecFactory
from metricflow_semantics.toolkit.mf_logging.lazy_formattable import LazyFormat
from typing_extensions import override

from metricflow.metric_evaluation.metric_query_planner import MetricEvaluationPlanner
from metricflow.metric_evaluation.plan.me_edges import MetricQueryDependencyEdge
from metricflow.metric_evaluation.plan.me_nodes import (
    ConversionMetricQueryNode,
    CumulativeMetricQueryNode,
    DerivedMetricsQueryNode,
    MetricQueryNode,
    SimpleMetricsQueryNode,
    TopLevelQueryNode,
)
from metricflow.metric_evaluation.plan.me_plan import (
    MetricEvaluationPlan,
    MutableMetricEvaluationPlan,
)
from metricflow.metric_evaluation.plan.query_element import MetricQueryElement, MetricQueryPropertySet
from metricflow.plan_conversion.node_processor import PredicatePushdownState
from metricflow_semantic_interfaces.enum_extension import assert_values_exhausted
from metricflow_semantic_interfaces.protocols import Metric
from metricflow_semantic_interfaces.type_enums import MetricType

logger = logging.getLogger(__name__)


class DepthFirstSearchMetricEvaluationPlanner(MetricEvaluationPlanner):
    """Builds a metric evaluation plan using a depth-first traversal of the metric dependency graph.

    For example, the metric evaluation plan for the query [`bookings_per_listing`, `bookings`] results in a plan
    that has the following edges:

        MetricQuery([`bookings_per_listing`]) -> MetricQuery([`bookings`])
        MetricQuery([`bookings_per_listing`]) -> MetricQuery([`listings`])
        Top Level Query -> MetricQuery([`bookings_per_listing`])
        Top Level Query -> MetricQuery([`bookings`])

    This mirrors the original approach to compute metrics in the `DataflowPlanBuilder`.
    """

    @override
    def build_plan(
        self,
        metric_specs: Sequence[MetricSpec],
        group_by_item_specs: Sequence[LinkableInstanceSpec],
        predicate_pushdown_state: PredicatePushdownState,
        filter_spec_factory: WhereFilterSpecFactory,
    ) -> MetricEvaluationPlan:
        """Build a metric evaluation plan using iterative depth-first traversal.

        This resolves each requested metric into metric-query nodes and dependency edges, then attaches a top-level
        query node that references all requested metrics.
        """
        # 入口只接收规划规格：MetricSpec 表示本次指标引用及其筛选、别名和时间偏移；
        # 分组项和下推状态组成查询属性，筛选工厂在展开依赖时解析定义中的筛选作用域。
        # 一个请求指标包装为一个查询元素，尚未创建计算节点，也不在这里生成 SQL。
        top_level_query_elements = tuple(
            MetricQueryElement.create(
                metric_spec=metric_spec,
                group_by_item_specs=group_by_item_specs,
                predicate_pushdown_state=predicate_pushdown_state,
            )
            for metric_spec in metric_specs
        )

        evaluation_plan = MutableMetricEvaluationPlan.create()

        # The query elements to process in the iterative DFS traversal loop. The next element is popped from the right
        # so elements are added in reverse to preserve order.
        # 右端出栈，因此反向入栈后仍按用户请求的指标顺序遍历。
        query_elements_to_process: list[MetricQueryElement] = list(reversed(top_level_query_elements))
        # Keeps track of the query elements that have been processed into a node in the evaluation plan.
        # 仅记录已建好的节点；复用键是完整查询元素，不能只凭指标名合并不同筛选、偏移或粒度。
        query_element_to_node: dict[MetricQueryElement, MetricQueryNode] = {}

        while query_elements_to_process:
            current_query_element = query_elements_to_process.pop()
            logger.debug(LazyFormat("Handling query element", current_query_element=current_query_element))
            # 同一依赖可能由多个父项或顶层请求入栈，已完成的元素直接复用，避免重复建节点。
            if current_query_element in query_element_to_node:
                continue

            # 指标规格决定“算哪个引用”，查询属性决定“按什么粒度、带什么下推上下文算”。
            current_metric_spec = current_query_element.metric_spec
            current_query_properties = current_query_element.query_properties
            current_predicate_pushdown_state = current_query_element.predicate_pushdown_state

            metric_name = current_metric_spec.element_name
            metric = self._manifest_object_lookup.get_metric(metric_name)
            metric_type = metric.type

            # 简单、累计和转化指标在本层作为叶子节点处理；“叶子”不表示没有底层数据流操作，
            # 只表示此 DFS 不再展开它们。简单指标还通过聚合参数绑定语义模型。
            metric_query_node = self._create_base_metric_query_node(
                metric=metric,
                metric_type=metric_type,
                metric_spec=current_metric_spec,
                query_properties=current_query_properties,
            )
            if metric_query_node is not None:
                evaluation_plan.add_node(metric_query_node)
                query_element_to_node[current_query_element] = metric_query_node
                continue

            # 比率和派生指标需要展开直接输入。例如 signup_index 只直接依赖 orders_per_signup，
            # 后者再展开 order_lines 和 user_signups，而非一次拉平全部后代。
            # 输入通常继承分组和筛选；当前引用有时间偏移时，由辅助方法保留筛选所需分组并禁用下推。
            input_query_elements = self._get_input_metric_query_elements_for_derived_metric(
                metric_spec=current_metric_spec,
                group_by_item_specs=current_query_element.group_by_item_specs,
                predicate_pushdown_state=current_predicate_pushdown_state,
                filter_spec_factory=filter_spec_factory,
            )
            assert len(input_query_elements) > 0, LazyFormat(
                "Expected a ratio or derived metric to have input query elements",
                current_metric_spec=current_metric_spec,
                metric=metric,
            )

            # 已在相同规格和查询属性下完成的输入不再入栈，未完成的输入先于当前父项求值。
            inputs_that_need_processing = tuple(
                input_query_element
                for input_query_element in input_query_elements
                if input_query_element not in query_element_to_node
            )
            # 用显式栈模拟递归返回：先把当前父项放回栈，再压入未完成的输入。
            if len(inputs_that_need_processing) > 0:
                # 例如 signup_index 先回栈，等 orders_per_signup 及其输入建好后再处理自身。
                query_elements_to_process.append(current_query_element)
                # 输入逆序入栈，使实际处理顺序与定义顺序一致：分子 order_lines 先于分母 user_signups。
                query_elements_to_process.extend(reversed(inputs_that_need_processing))
                continue

            # 所有直接输入都已建好，才创建计算当前指标的节点；ratio 和 derived 共用此节点类型。
            # 本节点只计算当前规格，不额外透传输入指标；具体除法或表达式留给后续数据流构建。
            derived_metric_query_node = DerivedMetricsQueryNode.create(
                computed_metric_specs=[current_metric_spec],
                passthrough_metric_specs=(),
                query_properties=current_query_properties,
            )
            evaluation_plan.add_node(derived_metric_query_node)

            # 每个直接输入生成一条依赖边：target（使用者）指向 source（提供者），与数据流方向相反。
            # signup_index -> orders_per_signup；orders_per_signup -> order_lines / user_signups。
            # 边同时标记两端的具体输出规格，不给 signup_index 添加直达孙级简单指标的边。
            for input_query_element in input_query_elements:
                input_query_node = query_element_to_node[input_query_element]
                evaluation_plan.add_edge(
                    MetricQueryDependencyEdge.create(
                        target_node=derived_metric_query_node,
                        target_node_output_spec=current_metric_spec,
                        source_node=input_query_node,
                        source_node_output_spec=input_query_element.metric_spec,
                    )
                )

            # 节点及直接依赖边均已登记，此后其他父项可复用这个完整查询元素的求值节点。
            query_element_to_node[current_query_element] = derived_metric_query_node

        # 所有请求指标完成后添加唯一顶层入口；它按原查询属性透传请求指标，不重新计算指标，
        # 也不会自动把 order_lines 等仅用于中间计算的指标加入最终输出。
        top_level_query_node = TopLevelQueryNode.create(
            passthrough_metric_specs=metric_specs,
            query_properties=MetricQueryPropertySet.create(group_by_item_specs, predicate_pushdown_state),
        )
        evaluation_plan.add_node(top_level_query_node)

        # 顶层依赖边两端使用同一指标规格，表示透传；若还显式请求 orders_per_signup，
        # 且规格、分组和下推状态相同，可复用 signup_index 分支中已生成的该节点。
        for top_level_query_element in top_level_query_elements:
            evaluation_plan.add_edge(
                MetricQueryDependencyEdge.create(
                    target_node=top_level_query_node,
                    target_node_output_spec=top_level_query_element.metric_spec,
                    source_node=query_element_to_node[top_level_query_element],
                    source_node_output_spec=top_level_query_element.metric_spec,
                )
            )

        # 返回包含计算节点、直接依赖边和顶层入口的指标依赖图；调用方再将其转换为数据流计划。
        return evaluation_plan

    def _create_base_metric_query_node(
        self,
        metric: Metric,
        metric_type: MetricType,
        metric_spec: MetricSpec,
        query_properties: MetricQueryPropertySet,
    ) -> Optional[MetricQueryNode]:
        """Return a node for base metric types or `None` for metrics that require dependency expansion."""
        # 此处分流决定 DFS 的叶子边界，累计与转化指标的内部处理留给各自的数据流构建路径。
        if metric_type is MetricType.SIMPLE:
            metric_aggregation_params = metric.type_params.metric_aggregation_params
            if metric_aggregation_params is None:
                raise ValueError(
                    LazyFormat(
                        "Simple metric is missing metric aggregation parameters",
                        metric_spec=metric_spec,
                        metric=metric,
                    )
                )
            return SimpleMetricsQueryNode.create(
                model_id=SemanticModelId.get_instance(metric_aggregation_params.semantic_model),
                metric_specs=(metric_spec,),
                query_properties=query_properties,
            )

        if metric_type is MetricType.CUMULATIVE:
            return CumulativeMetricQueryNode.create(metric_spec=metric_spec, query_properties=query_properties)
        elif metric_type is MetricType.CONVERSION:
            return ConversionMetricQueryNode.create(metric_spec=metric_spec, query_properties=query_properties)
        elif metric_type is MetricType.RATIO or metric_type is MetricType.DERIVED:
            return None
        else:
            assert_values_exhausted(metric_type)

    def _get_input_metric_query_elements_for_derived_metric(
        self,
        metric_spec: MetricSpec,
        group_by_item_specs: Iterable[LinkableInstanceSpec],
        predicate_pushdown_state: PredicatePushdownState,
        filter_spec_factory: WhereFilterSpecFactory,
    ) -> Sequence[MetricQueryElement]:
        """Return input query elements for a ratio / derived metric.

        Input query elements generally inherit group-by and predicate settings from the metric being expanded.
        Time-offset metrics are handled differently - see appropriate section in the `DataflowPlanBuilder`.
        """
        # 默认把当前引用携带的筛选继续交给直接输入，并继承分组粒度及下推状态。
        additional_filter_specs = metric_spec.where_filter_specs
        group_by_item_specs_for_inputs = group_by_item_specs
        predicate_pushdown_state_for_inputs = predicate_pushdown_state

        if metric_spec.has_time_offset:
            # 偏移后再筛选可能需要原查询未输出的维度；先让输入保留这些分组项及自定义粒度的基础粒度。
            group_by_item_specs_for_inputs = self._query_helper.resolve_group_by_specs_for_time_offset_metric_input(
                queried_group_by_specs=group_by_item_specs,
                filter_specs=metric_spec.where_filter_specs,
            )
            # 使用全新禁用状态，不把父项时间范围或 WHERE 下推给偏移前的输入，避免提前截掉所需数据。
            predicate_pushdown_state_for_inputs = PredicatePushdownState.with_pushdown_disabled()
            # If metric is offset, we'll apply where constraint after offset to avoid removing values
            # unexpectedly. Time constraint will be applied by INNER JOINing to time spine.
            # We may consider encapsulating this in pushdown state later, but as of this moment pushdown
            # is about post-join to pre-join for dimension access, and relies on the builder to collect
            # predicates from query and metric specs and make them available at simple-metric-input level.
            # 这里只停止向输入传播当前引用上的筛选，原 MetricSpec 仍保留它，供偏移后应用；
            # 指标定义和输入引用各自的筛选仍会由下面的规格构造方法收集。
            additional_filter_specs = ()

        # 只构造一层输入规格，保留各输入自己的别名和偏移；更深层依赖交给主循环继续展开。
        input_metric_specs = self._build_input_metric_specs_for_derived_metric(
            metric_name=metric_spec.element_name,
            filter_spec_factory=filter_spec_factory,
            additional_filter_specs=additional_filter_specs,
        )
        return tuple(
            MetricQueryElement.create(
                metric_spec=input_metric_spec,
                group_by_item_specs=group_by_item_specs_for_inputs,
                predicate_pushdown_state=predicate_pushdown_state_for_inputs,
            )
            for input_metric_spec in input_metric_specs
        )
