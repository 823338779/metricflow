from __future__ import annotations

import logging
from datetime import datetime

import pytest
from sqlglot import exp, parse_one

from metricflow.engine.metricflow_engine import MetricFlowExplainResult, MetricFlowQueryRequest, OutputColumnOrderMode
from metricflow.sql.optimizer.optimization_levels import SqlOptimizationLevel
from tests_metricflow.integration.conftest import IntegrationTestHelpers

logger = logging.getLogger(__name__)


def _show_sql(result: MetricFlowExplainResult, capsys: pytest.CaptureFixture[str]) -> str:
    """Print the SQL for debugger walkthroughs and omit plan comments from assertions."""
    with capsys.disabled():
        print(f"\n{result.sql_statement.sql}\n")
    return result.sql_statement.without_descriptions.sql


def _source_tables(sql: str, source_schema: str) -> set[str]:
    """Find physical source tables, excluding generated CTE references."""
    return {table.name for table in parse_one(sql, read="postgres").find_all(exp.Table) if table.db == source_schema}


def _output_columns(sql: str) -> set[str]:
    """Read the final projection without depending on generated aliases."""
    select = parse_one(sql, read="postgres").find(exp.Select)
    assert select is not None
    return set(select.named_selects)


def test_01_explain_simple_metric(
    it_helpers: IntegrationTestHelpers,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """单指标：订单明细数应从 order_detail 读取并对常量 1 求和."""
    result = it_helpers.mf_engine.explain(MetricFlowQueryRequest.create(metric_names=["order_lines"]))
    sql = _show_sql(result, capsys)

    assert _source_tables(sql, it_helpers.source_schema) == {"order_detail"}
    assert _output_columns(sql) == {"order_lines"}
    assert "SUM(1) AS order_lines" in sql
    assert "GROUP BY" not in sql
    assert "JOIN" not in sql


def test_02_explain_daily_metric(
    it_helpers: IntegrationTestHelpers,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """按天分组：metric_time 使用事实表的 ordered_at，并限制查询时间范围."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["order_lines"],
            group_by_names=["metric_time__day"],
            time_constraint_start=datetime(2024, 1, 1),
            time_constraint_end=datetime(2024, 1, 7),
        )
    )
    sql = _show_sql(result, capsys)

    assert _source_tables(sql, it_helpers.source_schema) == {"order_detail"}
    assert _output_columns(sql) == {"metric_time__day", "order_lines"}
    assert "ordered_at" in sql
    assert "DATE_TRUNC('day'," in sql
    assert "GROUP BY" in sql
    assert "2024-01-01" in sql
    assert "2024-01-07" in sql


def test_03_explain_linked_dimension(
    it_helpers: IntegrationTestHelpers,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """关联维度：按用户地区汇总订单明细，需要通过 user_id 连接 user."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(metric_names=["order_lines"], group_by_names=["user__region"])
    )
    sql = _show_sql(result, capsys)

    assert _source_tables(sql, it_helpers.source_schema) == {"order_detail", "user"}
    assert _output_columns(sql) == {"user__region", "order_lines"}
    assert "LEFT OUTER JOIN" in sql
    assert "user_id" in sql
    assert "GROUP BY" in sql


def test_04_explain_cross_source_ratio(
    it_helpers: IntegrationTestHelpers,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """跨源比率：分别聚合订单明细数和注册数，再按日期与用户地区对齐."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["orders_per_signup"],
            group_by_names=["metric_time__day", "user__region"],
        )
    )
    sql = _show_sql(result, capsys)

    assert _source_tables(sql, it_helpers.source_schema) == {"order_detail", "user"}
    assert _output_columns(sql) == {"metric_time__day", "user__region", "orders_per_signup"}
    assert "ordered_at" in sql
    assert "created_at" in sql
    assert "FULL OUTER JOIN" in sql
    assert "order_lines" in sql
    assert "user_signups" in sql
    assert "/" in sql
    assert "NULLIF(user_signups, 0)" in sql


def test_05_explain_derived_metric_with_filter(
    it_helpers: IntegrationTestHelpers,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """派生指标：比率乘以 100，且两个基础指标都应用用户地区过滤."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["signup_index"],
            group_by_names=["user__region"],
            where_constraints=["{{ Dimension('user__region') }} = 'east'"],
        )
    )
    sql = _show_sql(result, capsys)

    assert _source_tables(sql, it_helpers.source_schema) == {"order_detail", "user"}
    assert _output_columns(sql) == {"user__region", "signup_index"}
    assert "orders_per_signup * 100" in sql
    assert "NULLIF" in sql
    # 两条聚合分支必须各自过滤，不能只筛分子或分母。
    assert sql.count("user__region = 'east'") == 2


def test_06_explain_three_table_join(
    it_helpers: IntegrationTestHelpers,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """三表关联：按用户地区和商品类别汇总订单收入."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["gross_revenue"],
            group_by_names=["user__region", "product__category"],
        )
    )
    sql = _show_sql(result, capsys)

    assert _source_tables(sql, it_helpers.source_schema) == {"order_detail", "user", "product"}
    assert _output_columns(sql) == {"user__region", "product__category", "gross_revenue"}
    assert sql.count("LEFT OUTER JOIN") == 2
    assert "line_amount" in sql
    assert "user_id" in sql
    assert "product_id" in sql
    aggregates = tuple(parse_one(sql, read="postgres").find_all(exp.Sum))
    assert len(aggregates) == 1
    assert aggregates[0].this.name == "line_amount"


def test_07_explain_four_table_join(
    it_helpers: IntegrationTestHelpers,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """四表关联：按用户、商品和门店维度同时汇总明细数、收入与销量."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["order_lines", "gross_revenue", "units_sold"],
            group_by_names=["user__region", "product__category", "store__city"],
        )
    )
    sql = _show_sql(result, capsys)

    assert _source_tables(sql, it_helpers.source_schema) == {"order_detail", "user", "product", "store"}
    assert _output_columns(sql) == {
        "user__region",
        "product__category",
        "store__city",
        "order_lines",
        "gross_revenue",
        "units_sold",
    }
    assert sql.count("LEFT OUTER JOIN") == 3
    assert "line_amount" in sql
    assert "quantity" in sql
    assert "store_id" in sql
    assert {aggregate.this.name for aggregate in parse_one(sql, read="postgres").find_all(exp.Sum)} == {
        "__order_lines",
        "__gross_revenue",
        "__units_sold",
    }


def test_08_explain_daily_orders_with_time_spine(
    it_helpers: IntegrationTestHelpers,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """时间脊：对比普通订单明细数和按天连接时间脊、将空值补零的指标."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["order_lines", "calendar_order_lines"],
            group_by_names=["metric_time__day", "user__region"],
            time_constraint_start=datetime(2024, 1, 1),
            time_constraint_end=datetime(2024, 1, 7),
        )
    )
    sql = _show_sql(result, capsys)

    assert _source_tables(sql, it_helpers.source_schema) == {"order_detail", "user", "mf_time_spine"}
    assert _output_columns(sql) == {"metric_time__day", "user__region", "order_lines", "calendar_order_lines"}
    assert "LEFT OUTER JOIN" in sql
    assert any(
        coalesce.this.name == "__calendar_order_lines" and coalesce.expressions == [exp.Literal.number(0)]
        for coalesce in parse_one(sql, read="postgres").find_all(exp.Coalesce)
    )
    assert "2024-01-01" in sql
    assert "2024-01-07" in sql


def test_09_explain_fact_dimension_and_linked_filter(
    it_helpers: IntegrationTestHelpers,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """联合过滤：筛选已支付且用户为 VIP 的明细，按状态、VIP 与门店类型汇总收入."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["gross_revenue"],
            group_by_names=[
                "order_line__order_status",
                "user__vip_flag",
                "store__store_type",
            ],
            where_constraints=[
                "{{ Dimension('order_line__order_status') }} = 'paid'",
                "{{ Dimension('user__vip_flag') }}",
            ],
        )
    )
    sql = _show_sql(result, capsys)

    assert _source_tables(sql, it_helpers.source_schema) == {"order_detail", "user", "store"}
    assert _output_columns(sql) == {
        "order_line__order_status",
        "user__vip_flag",
        "store__store_type",
        "gross_revenue",
    }
    assert sql.count("LEFT OUTER JOIN") == 2
    assert "line_amount" in sql
    assert "is_vip" in sql
    where = parse_one(sql, read="postgres").find(exp.Where)
    assert where is not None
    assert where.this == parse_one("(order_line__order_status = 'paid') AND (user__vip_flag)", read="postgres")


def test_10_explain_comprehensive_metric_query(
    it_helpers: IntegrationTestHelpers,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """综合查询：每日地区 VIP 报表，覆盖多指标依赖、时间脊、过滤、排序和限行."""
    # signup_index -> orders_per_signup -> order_lines / user_signups。
    # 同时直接查询依赖指标，便于观察求值图中的共享依赖和数据流分支复用。
    metric_names = (
        "signup_index",
        "orders_per_signup",
        "gross_revenue",
        "units_sold",
        "order_lines",
        "user_signups",
        "calendar_order_lines",
    )
    # 订单和注册分支都能访问这些维度；订单状态、商品和门店维度不适用于注册分支。
    # 故意把时间维度放在中间，验证 INPUT_ORDER 保留请求顺序。
    group_by_names = ("user__region", "metric_time__day", "user__vip_flag")
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=metric_names,
            group_by_names=group_by_names,
            time_constraint_start=datetime(2024, 1, 1),
            time_constraint_end=datetime(2024, 1, 7),
            where_constraints=[
                "{{ Dimension('user__region') }} IN ('east', 'west')",
                "{{ Dimension('user__vip_flag') }}",
            ],
            order_by_names=["metric_time__day", "-gross_revenue", "user__region"],
            limit=20,
            output_column_order_mode=OutputColumnOrderMode.INPUT_ORDER,
            sql_optimization_level=SqlOptimizationLevel.O5,
        )
    )
    sql = _show_sql(result, capsys)
    query = parse_one(sql, read="postgres")

    assert _source_tables(sql, it_helpers.source_schema) == {"order_detail", "user", "mf_time_spine"}
    assert tuple(query.named_selects) == group_by_names + metric_names
    assert "ordered_at" in sql
    assert "created_at" in sql
    assert "2024-01-01" in sql
    assert "2024-01-07" in sql
    assert "DATE_TRUNC('day'," in sql
    assert query.find(exp.Group) is not None
    assert "LEFT OUTER JOIN" in sql
    assert "FULL OUTER JOIN" in sql
    assert "line_amount" in sql
    assert "quantity" in sql
    assert "orders_per_signup * 100" in sql
    assert "NULLIF(user_signups, 0)" in sql
    assert any(
        coalesce.this.name == "__calendar_order_lines" and coalesce.expressions == [exp.Literal.number(0)]
        for coalesce in query.find_all(exp.Coalesce)
    )

    # 查询级过滤需要进入订单和注册分支；不绑定优化器合并分支后的精确出现次数。
    filtered_branches = tuple(where for where in query.find_all(exp.Where) if where.find(exp.In) is not None)
    assert len(filtered_branches) >= 2
    for where in filtered_branches:
        region_filter = where.find(exp.In)
        assert region_filter is not None
        assert region_filter.this.name == "user__region"
        assert region_filter.expressions == [exp.Literal.string("east"), exp.Literal.string("west")]
        assert "user__vip_flag" in {column.name for column in where.find_all(exp.Column)}

    # 排序与 LIMIT 必须作用于最终报表，而不是任意一个内部子查询。
    order = query.args.get("order")
    assert order is not None
    assert [(item.this.name, bool(item.args.get("desc"))) for item in order.expressions] == [
        ("metric_time__day", False),
        ("gross_revenue", True),
        ("user__region", False),
    ]
    limit = query.args.get("limit")
    assert limit is not None
    assert limit.expression == exp.Literal.number(20)
