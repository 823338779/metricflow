# MetricFlow explain 学习进度

- 已从第九个单测 `test_09_explain_fact_dimension_and_linked_filter` 的请求解析，读到 `DepthFirstSearchMetricEvaluationPlanner.build_plan` 返回指标依赖图。该用例查询 `gross_revenue`，按订单状态、用户 VIP 标记和门店类型分组，并筛选已支付且用户为 VIP 的明细。
- 已补充查询筛选作用域、指标规格、分组与下推状态，以及 DFS 的叶子分支、逐层展开、时间偏移与筛选、节点复用、父项回栈、直接依赖边方向和顶层透传的中文注释。
- 依赖示例使用 `signup_index -> orders_per_signup -> order_lines / user_signups`：前者是 `orders_per_signup * 100`，比率的分子是订单明细行数，分母是注册数。此示例用于理解展开过程，不是第九个单测实际请求的指标。
- 下一步跟踪 `_build_query_output_node` 如何消费 `me_plan_override`，把指标依赖图转换为数据流计划，再转换为 SQL。
- 本次仅修改中文代码注释和本进度文档，保留 `develop` 已有行为及维值映射；仅做静态 diff 检查，不编译、不构建、不运行测试。
