from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup
from metricflow_semantics.test_helpers.manifest_helpers import mf_load_manifest_from_yaml_directory

from metricflow.engine.metricflow_engine import MetricFlowQueryRequest
from tests_metricflow.integration.conftest import IntegrationTestHelpers


@pytest.fixture(scope="session")
def simple_semantic_manifest_lookup(template_mapping: dict[str, str]) -> SemanticManifestLookup:
    """Load only the definitions needed by this query test."""
    yaml_directory = Path(__file__).parent / "yaml"
    return SemanticManifestLookup(mf_load_manifest_from_yaml_directory(yaml_directory, template_mapping))


def test_01_explain_simple_metric(
    it_helpers: IntegrationTestHelpers,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render one simple metric without grouping."""
    result = it_helpers.mf_engine.explain(MetricFlowQueryRequest.create(metric_names=["completed_purchases"]))
    with capsys.disabled():
        print("\n[01] simple metric")
        print(result.sql_statement)

    rendered_sql = result.sql_statement.sql
    assert "SUM(1) AS completed_purchases" in rendered_sql


def test_02_explain_simple_metric_by_day(
    it_helpers: IntegrationTestHelpers, capsys: pytest.CaptureFixture[str]
) -> None:
    """Add a day grain to the same metric through metric_time."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(metric_names=["completed_purchases"], group_by_names=["metric_time__day"])
    )
    with capsys.disabled():
        print("\n[02] metric grouped by day")
        print(result.sql_statement)

    rendered_sql = result.sql_statement.sql
    assert "DATE_TRUNC('day', ordered_at)" in rendered_sql
    assert ".mf_time_spine" not in rendered_sql


def test_03_explain_simple_metric_with_joined_dimension(
    it_helpers: IntegrationTestHelpers, capsys: pytest.CaptureFixture[str]
) -> None:
    """Group orders by a dimension provided by the customer model."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(metric_names=["completed_purchases"], group_by_names=["buyer__home_market"])
    )
    with capsys.disabled():
        print("\n[03] joined dimension")
        print(result.sql_statement)

    rendered_sql = result.sql_statement.sql
    assert "LEFT OUTER JOIN" in rendered_sql
    assert "buyer__home_market" in rendered_sql


def test_04_explain_ratio_metric(it_helpers: IntegrationTestHelpers, capsys: pytest.CaptureFixture[str]) -> None:
    """Query order count, customer count, and their rate by region and day."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["completed_purchases", "buyer_signups", "purchase_signup_ratio"],
            group_by_names=["buyer__home_market", "metric_time__day"],
        )
    )
    with capsys.disabled():
        print("\n[04] ratio metric")
        print(result.sql_statement)

    rendered_sql = result.sql_statement.sql
    assert "NULLIF(" in rendered_sql
    assert "AS purchase_signup_ratio" in rendered_sql
    assert "AS completed_purchases" in rendered_sql
    assert "AS buyer_signups" in rendered_sql
    assert "buyer__home_market" in rendered_sql
    assert "metric_time__day" in rendered_sql


def test_05_explain_multiple_metric_types_with_filter_and_dimensions(
    it_helpers: IntegrationTestHelpers, capsys: pytest.CaptureFixture[str]
) -> None:
    """Query simple, ratio, and derived metrics with dimensions and a filter."""
    where_constraint = "{{ Dimension('buyer__vip_flag') }}"
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["completed_purchases", "buyer_signups", "purchase_signup_ratio", "purchase_index"],
            group_by_names=["buyer__home_market", "buyer__vip_flag", "metric_time__day"],
            where_constraints=[where_constraint],
            dataflow_plan_optimizations=set(),
        )
    )
    with capsys.disabled():
        print("\n[05] multiple metric types")
        print(result.sql_statement)

    rendered_sql = result.sql_statement.sql
    assert "WHERE buyer__vip_flag" in rendered_sql
    assert "purchase_signup_ratio * 100" in rendered_sql


def test_06_explain_three_table_join(it_helpers: IntegrationTestHelpers) -> None:
    """Query item revenue and count by customer region and VIP status through orders."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["gross_sales", "line_entries"],
            group_by_names=["purchase__buyer__home_market", "purchase__buyer__vip_flag"],
        )
    )

    rendered_sql = result.sql_statement.sql
    assert rendered_sql.count("LEFT OUTER JOIN") == 2
    assert "fct_order_items" in rendered_sql
    assert "fct_orders" in rendered_sql
    assert "dim_customers" in rendered_sql
    assert "purchase__buyer__home_market" in rendered_sql
    assert "purchase__buyer__vip_flag" in rendered_sql
    assert "AS gross_sales" in rendered_sql
    assert "AS line_entries" in rendered_sql


def test_07_explain_four_table_join(it_helpers: IntegrationTestHelpers) -> None:
    """Query item revenue and count by customer region and product category."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["gross_sales", "line_entries"],
            group_by_names=["purchase__buyer__home_market", "sku__merchandise_class"],
        )
    )

    rendered_sql = result.sql_statement.sql
    assert rendered_sql.count("LEFT OUTER JOIN") == 3
    assert "fct_order_items" in rendered_sql
    assert "fct_orders" in rendered_sql
    assert "dim_customers" in rendered_sql
    assert "dim_products" in rendered_sql
    assert "purchase__buyer__home_market" in rendered_sql
    assert "sku__merchandise_class" in rendered_sql
    assert "AS gross_sales" in rendered_sql
    assert "AS line_entries" in rendered_sql


def test_08_explain_daily_orders_with_time_spine(it_helpers: IntegrationTestHelpers) -> None:
    """Compare daily filled and raw order counts by day and customer region."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["calendar_purchases", "completed_purchases"],
            group_by_names=["metric_time__day", "buyer__home_market"],
            time_constraint_start=datetime(2024, 1, 1),
            time_constraint_end=datetime(2024, 1, 7),
        )
    )

    rendered_sql = result.sql_statement.sql
    assert ".mf_time_spine" in rendered_sql
    assert "fct_orders" in rendered_sql
    assert "LEFT OUTER JOIN" in rendered_sql
    assert "COALESCE" in rendered_sql
    assert "AS calendar_purchases" in rendered_sql
    assert "AS completed_purchases" in rendered_sql
    assert "buyer__home_market" in rendered_sql
