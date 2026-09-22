from __future__ import annotations

from dataclasses import dataclass

from metricflow.execution.execution_plan import ExecutionPlan
from metricflow.plan_conversion.convert_to_sql_plan import ConvertToSqlPlanResult
from metricflow.sql.render.sql_plan_renderer import SqlPlanRenderResult


@dataclass(frozen=True)
class ConvertToExecutionPlanResult:
    """A result object for returning the results of converting a dataflow plan into an execution plan."""

    # 保留可检查的 SQL 结构与输出语义，供 explain() 展示计划或定位哪一层生成了列。
    convert_to_sql_plan_result: ConvertToSqlPlanResult
    # 最终 SQL 文本及绑定参数；生成执行任务时复制这两项。
    render_sql_result: SqlPlanRenderResult
    # explain() 从任务中取 sql_statement，query() 则执行这些任务。
    execution_plan: ExecutionPlan
