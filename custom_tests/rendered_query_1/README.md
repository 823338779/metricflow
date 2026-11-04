# MetricFlow SQL 生成调试用例

这一组用例从 `MetricFlowQueryRequest.create()` 和 `MetricFlowEngine.explain()` 开始，
依次观察语义解析、数据流计划、SQL 计划与 PostgreSQL SQL 文本。

## 来源与重建范围

四个 semantic model 和七个 metric YAML 来自共享对话中记录的新增补丁。
共享记录没有包含重写后的完整测试体、完整 fixture 或时间脊项目配置，因此这些部分按
对话描述的九个场景和当前仓库接口重建，不是原文件的逐字恢复。
保留了第 01、08、09 个调试入口名，第 09 个请求的指标、分组及过滤条件与对话一致。

版本差异：原同步分支基于 `main`，当时没有 `output_value_mapping_specs` 字段及对应的
`query_spec = replace(...)` 操作。合并到 `develop` 后保留了该分支已有的原生维值映射实现
与测试；本组 SQL walkthrough 不单独验证维值映射行为。

按整体重新建模的要求，原有的 purchase / buyer 场景及其 YAML 已由下述四模型、
七指标和九个场景替换；`develop` 独立的维值映射实现及其回归测试保留不变。

`conftest.py` 复用仓库的 `IntegrationTestHelpers` 和 `SqlAlchemyBasedSqlClient`。
与对话中的方式相同，SQLite 内存 engine 仅作为 client 的本地后端，实际 SQL 使用
PostgreSQL renderer；不连接外部数据库，不创建源表，也不执行 SQL。
YAML 直接加载成内存 Semantic Manifest，不生成 dbt 的 `target/manifest.json`。
为了结果可复现，schema 固定为 `rendered_query_1`，当前时间固定为 2024-01-01。
项目配置只声明这组用例需要的日粒度时间脊 `mf_time_spine.ds`。

## 模型与指标

- `order_detail`：主键 `order_line_id`，经 `user_id`、`product_id`、`store_id` 关联维度表。
  时间列 `ordered_at`；事实维度 `status`、`channel`；指标输入 `line_amount`、`quantity`。
- `user`：主键 `user_id`；时间列 `created_at`；维度 `region`、`is_vip`。
- `product`：主键 `product_id`；时间列 `created_at`；维度 `category`、`brand`。
- `store`：主键 `store_id`；时间列 `opened_at`；维度 `city`、`store_type`。

指标：`order_lines`、`gross_revenue`、`units_sold`、`user_signups`、
`orders_per_signup`、`signup_index`、`calendar_order_lines`。
`order_lines` 统计订单明细行，不是去重订单数；`orders_per_signup` 是明细行数 / 注册数，
不代表用户转化率或注册 cohort 的后续购买率。

## 九个递进场景

1. 单指标：订单明细数，`SUM(1)`。
2. 按天分组：使用订单时间，并添加日期范围。
3. 关联维度：按用户地区分组。
4. 跨源比率：订单明细数与用户注册数按日期、地区对齐后相除。
5. 派生指标与过滤：比率乘以 100，两个来源都应用地区过滤。
6. 三表关联：订单明细、用户、商品。
7. 四表关联：加入门店，同时查询明细数、收入和销量。
8. 时间脊：对比普通指标和日粒度补零指标。
9. 联合过滤：已支付且用户为 VIP，按订单状态、VIP 标记、门店类型汇总收入。

时间脊用例只验证生成的日期连接与补零 SQL，不承诺每个地区每天都有一行零值。
这些是 SQL 生成测试，不是数据库执行或数据正确性测试。

## 运行与断点

从仓库根目录执行：

```bash
hatch run dev-env:pytest custom_tests/rendered_query_1/test_rendered_query_1.py
hatch run dev-env:pytest custom_tests/rendered_query_1/test_rendered_query_1.py::test_09_explain_fact_dimension_and_linked_filter
```

PyCharm 选择 Hatch `dev-env` 的 Python 解释器，工作目录设为仓库根目录，
pytest target 指向上面的文件或具体函数。不要复用原电脑的绝对路径。

在测试中的 `result = it_helpers.mf_engine.explain(...)` 设置首个断点，依次查看：

1. `MetricFlowQueryRequest.create()` 返回的请求。
2. `MetricFlowEngine._create_execution_plan()` 中的 `query_spec`。
3. `DataflowPlanBuilder.build_plan()` 返回的数据流图。
4. SQL 转换与 renderer 输出。
5. `result.sql_statement.sql` 中的最终文本。

测试会打印 SQL，并检查实际来源表、最终输出列、聚合、关联及过滤结构。
