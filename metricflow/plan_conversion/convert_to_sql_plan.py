from __future__ import annotations

from dataclasses import dataclass

from metricflow_semantics.instances import InstanceSet

from metricflow.sql.sql_plan import SqlPlan


@dataclass(frozen=True)
class ConvertToSqlPlanResult:
    """Result object for returning the results of converting to a `SqlQueryPlan`.

    DataflowToSqlPlanConverter 的结果：仍是结构化 SQL 计划，尚未渲染为字符串。
    """

    # SQL 层对外暴露的语义列与列名关联；检查输出是否满足查询时要读它，不能只解析 SQL 字符串。
    instance_set: InstanceSet
    # SQL 优化完成后的结构化 SELECT/JOIN 图；Renderer 消费它才生成方言 SQL。
    sql_plan: SqlPlan
