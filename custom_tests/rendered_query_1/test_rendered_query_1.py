from __future__ import annotations

import logging
from datetime import datetime

import pytest
from sqlglot import exp, parse_one

from metricflow.engine.metricflow_engine import MetricFlowExplainResult, MetricFlowQueryRequest
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
