# MetricFlow explain 学习进度

- 已从第九个单测 `test_09_explain_fact_dimension_and_linked_filter` 的请求解析，读到 `DepthFirstSearchMetricEvaluationPlanner.build_plan` 返回指标依赖图。该用例查询 `gross_revenue`，按订单状态、用户 VIP 标记和门店类型分组，并筛选已支付且用户为 VIP 的明细。
- 已补充查询筛选作用域、指标规格、分组与下推状态，以及 DFS 的叶子分支、逐层展开、时间偏移与筛选、节点复用、父项回栈、直接依赖边方向和顶层透传的中文注释。
- 依赖示例使用 `signup_index -> orders_per_signup -> order_lines / user_signups`：前者是 `orders_per_signup * 100`，比率的分子是订单明细行数，分母是注册数。此示例用于理解展开过程，不是第九个单测实际请求的指标。

## 当前进度（2026-11-06）

已从 case09 切换到新增的 `test_10_explain_comprehensive_metric_query`，学习到数据流图构建与优化完成、准备调用 `convert_to_execution_plan()` 的边界。已解释 Combine 节点的 SQL 转换意图，但尚未逐步跟完完整 SQL 转换和渲染链路。

### case10 与实际拓扑

- 查询全部七个指标，按地区、日期、VIP 分组，筛选 east/west 地区 VIP 用户及 2024-01-01 至 2024-01-07 的数据；包含升降序排序、`LIMIT 20`、`INPUT_ORDER` 和 SQL 优化等级 O5。
- `signup_index -> orders_per_signup -> order_lines / user_signups` 现在是 case10 的真实依赖链。另有 `gross_revenue`、`units_sold`、`calendar_order_lines`；后者连接时间脊并补零。
- 实际读取过求值图：8 个节点、10 条依赖边。顶层直接依赖七个指标节点，派生链复用比率、订单明细数和注册数节点。
- 默认 DFS 规划器逐个创建简单指标节点，即使来自同一模型也不在此合并；PassThrough 规划器的 BaseMetricQueryNodeBuilder 才按模型、过滤、偏移和查询属性等分组。
- case10 不触发 `output_group_by_metric_instances`，普通维度分组不需要这个特殊输出模式。

### 已讲解的构建环节

- `MutableMetricEvaluationPath` 从顶层节点开始，原地追加依赖边、回退；`find_paths_dfs(target_nodes=None)` 先产出依赖路径，再产出当前路径。
- case10 的路径产出顺序为：order_lines、user_signups、orders_per_signup、signup_index、order_lines、user_signups、orders_per_signup、gross_revenue、units_sold、order_lines、user_signups、calendar_order_lines、顶层。13 次产出中实际转换 8 个节点，其余 5 次由外层字典跳过。
- 求值图的拓扑关系由 plan 的边保存，使用 `source_nodes()` 看依赖、`target_nodes()` 看使用者；MetricQueryElement 本身不保存父子节点关系。
- `accept(converter)` 按节点类型分派 Visitor。`visit_simple_metrics_query_node()` 遍历该节点的输出指标，以完整构建上下文查缓存，未命中则构建分支；单分支直接返回，多分支创建 Combine。
- 区分两层缓存：`query_node_to_dataflow_node` 按求值节点去重；`simple_metric_node_cache` 按单指标构建输入复用分支。缓存的是计划节点，不是数据库查询结果。
- `_build_simple_metric_recipe()` 整理分组、指标定义级/额外过滤、时间偏移和时间脊规则。普通 order_lines 使用聚合前过滤，无累计、偏移或时间脊；日期范围由下推状态单独传递。
- `_build_aggregated_simple_metric_input()` 已按断点讲到输入规格、必需维度、累计/偏移分支及 `_find_source_node_recipe()` 入口。选源搜索内部尚未逐步走完；后续维度 JOIN、两轮选列、过滤、聚合和 ComputeMetrics 的职责已概述。
- 数据流分支返回末端节点，通过 `parent_nodes` 连接完整上游。分组语义由保留的维度实例与聚合节点共同表达，不是每个节点都保存显式 `group_by` 字段。

### Combine 与 SQL 边界

- case10 优化后的顶层 Combine 有五条输入分支：signup_index；orders_per_signup；gross_revenue + units_sold + order_lines；user_signups；calendar_order_lines。五个分支提供七个指标，不等于五个 CTE。
- 节点类型声明组合操作，`parent_nodes` 指定输入；`description` / `graphviz_label` 仅用于展示。Combine 的 `aggregated_to_elements` 使用基类默认空集合，不能据此判断没有分组。
- SQL Visitor 读取各父分支的 InstanceSet，校验分组项一致；有分组项时按这些键做 FULL OUTER JOIN，无分组项时做 CROSS JOIN。通过 COALESCE 统一分组键，再聚合处理 NULL 键造成的重复分组。
- `build_plan()` 追加最终排序、限行、别名和输出节点，再进行数据流优化；随后 `convert_to_execution_plan()` 负责正式的 SQL 计划转换、优化、渲染和执行任务封装。构建阶段也可能通过数据集解析器推导 SQL 输出结构，不能理解为此前完全不涉及 SQL。
- `explain()` 只生成计划与 SQL，不执行查询或写表。

## 下一步断点

1. 若继续细读数据流构建：从 order_lines 的 `_find_source_node_recipe()` 进入，观察订单来源选择、用户维度关联方案，再回到 `_build_pre_aggregation_plan()` 和 `AggregateSimpleMetricInputsNode`。
2. 若沿当前主线进入 SQL：在 `MetricFlowEngine._create_execution_plan()` 的 `convert_to_execution_plan()` 调用处进入，跟踪输出节点如何递归消费父节点。
3. 重点观察 `visit_combine_aggregated_outputs_node()` 中的 `parent_data_sets`、各父分支 `instance_set.spec_set`、`linkable_specs`、`join_type` 和连接描述，再跟进 SQL 优化及 PostgreSQL renderer。

## 已完成修改与验证

- 新增 case10 和 README 学习路线；补充数据流构建主链的关键中文注释，保留已有业务行为及维值映射实现。
- 最近的代码注释补充已通过可执行 token 对比，确认只改变注释；整组 10 个用例通过，diff 检查通过。
- 环境缺少 `make`，`make lint` 和 `make test` 未能执行，不能视为全仓检查通过。本次进度文档更新仅做内容和 diff 检查。
