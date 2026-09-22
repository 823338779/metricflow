from __future__ import annotations

import logging
from typing import Optional

from metricflow_semantics.toolkit.mf_logging.lazy_formattable import LazyFormat
from typing_extensions import override

from metricflow.dataflow.dataflow_plan import (
    DataflowPlan,
    DataflowPlanNode,
)
from metricflow.dataflow.dataflow_plan_visitor import DataflowPlanNodeVisitor
from metricflow.dataflow.nodes.add_generated_uuid import AddGeneratedUuidColumnNode
from metricflow.dataflow.nodes.aggregate_simple_metric_inputs import AggregateSimpleMetricInputsNode
from metricflow.dataflow.nodes.alias_specs import AliasSpecsNode
from metricflow.dataflow.nodes.combine_aggregated_outputs import CombineAggregatedOutputsNode
from metricflow.dataflow.nodes.compute_metrics import ComputeMetricsNode
from metricflow.dataflow.nodes.constrain_time import ConstrainTimeRangeNode
from metricflow.dataflow.nodes.filter_elements import SelectorNode
from metricflow.dataflow.nodes.join_conversion_events import JoinConversionEventsNode
from metricflow.dataflow.nodes.join_over_time import JoinOverTimeRangeNode
from metricflow.dataflow.nodes.join_to_base import JoinOnEntitiesNode
from metricflow.dataflow.nodes.join_to_custom_granularity import JoinToCustomGranularityNode
from metricflow.dataflow.nodes.join_to_time_spine import JoinToTimeSpineNode
from metricflow.dataflow.nodes.metric_time_transform import MetricTimeDimensionTransformNode
from metricflow.dataflow.nodes.min_max import MinMaxNode
from metricflow.dataflow.nodes.offset_base_grain_by_custom_grain import OffsetBaseGrainByCustomGrainNode
from metricflow.dataflow.nodes.offset_custom_granularity import OffsetCustomGranularityNode
from metricflow.dataflow.nodes.order_by_limit import OrderByLimitNode
from metricflow.dataflow.nodes.read_sql_source import ReadSqlSourceNode
from metricflow.dataflow.nodes.semi_additive_join import SemiAdditiveJoinNode
from metricflow.dataflow.nodes.where_filter import WhereFilterNode
from metricflow.dataflow.nodes.window_reaggregation_node import WindowReaggregationNode
from metricflow.dataflow.nodes.write_to_data_table import WriteToResultDataTableNode
from metricflow.dataflow.nodes.write_to_table import WriteToResultTableNode
from metricflow.execution.convert_to_execution_plan import ConvertToExecutionPlanResult
from metricflow.execution.execution_plan import (
    ExecutionPlan,
    SelectSqlQueryToDataTableTask,
    SelectSqlQueryToTableTask,
    SqlStatement,
)
from metricflow.plan_conversion.convert_to_sql_plan import ConvertToSqlPlanResult
from metricflow.plan_conversion.to_sql_plan.dataflow_to_sql import DataflowToSqlPlanConverter
from metricflow.plan_conversion.to_sql_plan.output_column_orderer import OutputColumnOrderer
from metricflow.protocols.sql_client import SqlClient
from metricflow.sql.optimizer.optimization_levels import SqlOptimizationLevel
from metricflow.sql.render.sql_plan_renderer import SqlPlanRenderer, SqlPlanRenderResult

logger = logging.getLogger(__name__)


class DataflowToExecutionPlanConverter(DataflowPlanNodeVisitor[ConvertToExecutionPlanResult]):
    """Converts a dataflow plan to an execution plan.

    explain() 的最后一段：DataflowPlan -> SqlPlan -> 方言 SQL -> 带 SQL 的执行任务。
    """

    def __init__(
        self,
        sql_plan_converter: DataflowToSqlPlanConverter,
        sql_plan_renderer: SqlPlanRenderer,
        sql_client: SqlClient,
        sql_optimization_level: SqlOptimizationLevel,
    ) -> None:
        """Initializer.

        Args:
            sql_plan_converter: Converts a dataflow plan node to a SQL query plan
            sql_plan_renderer: Converts a SQL query plan to SQL text
            sql_client: The client to use for running queries.
            sql_optimization_level: The optimization level to use for generating the SQL.
        """
        # 消费数据流终点，递归生成可优化的 SQL 节点树；此时还没有方言 SQL 字符串。
        self._sql_plan_converter = sql_plan_converter
        # 消费 SQL 节点树，决定函数/类型等方言写法并产出 SQL 文本与绑定参数。
        self._sql_plan_renderer = sql_plan_renderer
        # sql_engine_type 参与 SQL 生成选项；同时放进最终执行任务，供 query() 真正执行。
        # explain() 只构造任务，不通过此客户端发起查询。
        self._sql_client = sql_client
        # 决定是否启用列裁剪、CTE 等 SQL 层优化；区别于先前合并数据流分支的优化选项。
        self._optimization_level = sql_optimization_level
        # 从 QuerySpec 的原始输入顺序构建，并传给 SQL Visitor 排列用户可见的 SELECT 列。
        self._output_column_orderer: Optional[OutputColumnOrderer] = None

    def _convert_to_sql_plan(self, node: DataflowPlanNode) -> ConvertToSqlPlanResult:
        logger.debug(LazyFormat("Generating SQL plan", node_id=node.node_id))
        result = self._sql_plan_converter.convert_to_sql_plan(
            sql_engine_type=self._sql_client.sql_engine_type,
            optimization_level=self._optimization_level,
            dataflow_plan_node=node,
            output_column_orderer=self._output_column_orderer,
        )
        logger.debug(LazyFormat("Generated SQL plan", sql_plan=lambda: result.sql_plan.structure_text()))
        return result

    def _render_sql(self, convert_to_sql_plan_result: ConvertToSqlPlanResult) -> SqlPlanRenderResult:
        return self._sql_plan_renderer.render_sql_plan(convert_to_sql_plan_result.sql_plan)

    @override
    def visit_write_to_result_data_table_node(self, node: WriteToResultDataTableNode) -> ConvertToExecutionPlanResult:
        """把终点节点转换成 SQL 计划、SQL 文本和 SELECT 执行任务。"""
        convert_to_sql_plan_result = self._convert_to_sql_plan(node)
        render_sql_result = self._render_sql(convert_to_sql_plan_result)
        execution_plan = ExecutionPlan(
            leaf_tasks=(
                SelectSqlQueryToDataTableTask.create(
                    sql_client=self._sql_client,
                    sql_statement=SqlStatement(render_sql_result.sql, render_sql_result.bind_parameter_set),
                ),
            )
        )
        return ConvertToExecutionPlanResult(
            convert_to_sql_plan_result=convert_to_sql_plan_result,
            render_sql_result=render_sql_result,
            execution_plan=execution_plan,
        )

    @override
    def visit_write_to_result_table_node(self, node: WriteToResultTableNode) -> ConvertToExecutionPlanResult:
        convert_to_sql_plan_result = self._convert_to_sql_plan(node)
        render_sql_result = self._render_sql(convert_to_sql_plan_result)
        execution_plan = ExecutionPlan(
            leaf_tasks=(
                SelectSqlQueryToTableTask.create(
                    sql_client=self._sql_client,
                    sql_statement=SqlStatement(
                        sql=render_sql_result.sql,
                        bind_parameter_set=render_sql_result.bind_parameter_set,
                    ),
                    output_table=node.output_sql_table,
                ),
            ),
        )
        return ConvertToExecutionPlanResult(
            convert_to_sql_plan_result=convert_to_sql_plan_result,
            render_sql_result=render_sql_result,
            execution_plan=execution_plan,
        )

    def convert_to_execution_plan(
        self,
        dataflow_plan: DataflowPlan,
        output_column_orderer: Optional[OutputColumnOrderer] = None,
    ) -> ConvertToExecutionPlanResult:
        """Convert the dataflow plan to an execution plan.

        从 sink_node 开始分派；普通 explain() 最终走到 visit_write_to_result_data_table_node()。
        """
        self._output_column_orderer = output_column_orderer
        return dataflow_plan.sink_node.accept(self)

    @override
    def visit_source_node(self, node: ReadSqlSourceNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_join_on_entities_node(self, node: JoinOnEntitiesNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_aggregate_simple_metric_inputs_node(
        self, node: AggregateSimpleMetricInputsNode
    ) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_compute_metrics_node(self, node: ComputeMetricsNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_window_reaggregation_node(self, node: WindowReaggregationNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_order_by_limit_node(self, node: OrderByLimitNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_where_constraint_node(self, node: WhereFilterNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_selector_node(self, node: SelectorNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_combine_aggregated_outputs_node(self, node: CombineAggregatedOutputsNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_constrain_time_range_node(self, node: ConstrainTimeRangeNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_join_over_time_range_node(self, node: JoinOverTimeRangeNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_semi_additive_join_node(self, node: SemiAdditiveJoinNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_metric_time_dimension_transform_node(
        self, node: MetricTimeDimensionTransformNode
    ) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_join_to_time_spine_node(self, node: JoinToTimeSpineNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_min_max_node(self, node: MinMaxNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_add_generated_uuid_column_node(self, node: AddGeneratedUuidColumnNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_join_conversion_events_node(self, node: JoinConversionEventsNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_join_to_custom_granularity_node(self, node: JoinToCustomGranularityNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_alias_specs_node(self, node: AliasSpecsNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_offset_base_grain_by_custom_grain_node(
        self, node: OffsetBaseGrainByCustomGrainNode
    ) -> ConvertToExecutionPlanResult:
        raise NotImplementedError

    @override
    def visit_offset_custom_granularity_node(self, node: OffsetCustomGranularityNode) -> ConvertToExecutionPlanResult:
        raise NotImplementedError
