from __future__ import annotations

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
    result = it_helpers.mf_engine.explain(MetricFlowQueryRequest.create(metric_names=["bookings"]))
    with capsys.disabled():
        print("\n[01] simple metric")
        print(result.sql_statement)

    rendered_sql = result.sql_statement.sql
    assert "SUM(1) AS bookings" in rendered_sql


def test_02_explain_simple_metric_by_day(
    it_helpers: IntegrationTestHelpers, capsys: pytest.CaptureFixture[str]
) -> None:
    """Add a day grain to the same metric through metric_time."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(metric_names=["bookings"], group_by_names=["metric_time__day"])
    )
    with capsys.disabled():
        print("\n[02] metric grouped by day")
        print(result.sql_statement)

    rendered_sql = result.sql_statement.sql
    assert "DATE_TRUNC('day', ds)" in rendered_sql


def test_03_explain_simple_metric_with_joined_dimension(
    it_helpers: IntegrationTestHelpers, capsys: pytest.CaptureFixture[str]
) -> None:
    """Group bookings by a dimension provided by the listing model."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(metric_names=["bookings"], group_by_names=["listing__country_latest"])
    )
    with capsys.disabled():
        print("\n[03] joined dimension")
        print(result.sql_statement)

    rendered_sql = result.sql_statement.sql
    assert "LEFT OUTER JOIN" in rendered_sql
    assert "listing__country_latest" in rendered_sql


def test_04_explain_ratio_metric(it_helpers: IntegrationTestHelpers, capsys: pytest.CaptureFixture[str]) -> None:
    """Combine two simple metrics into bookings per listing."""
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["bookings_per_listing"],
            group_by_names=["listing__country_latest", "metric_time__day"],
        )
    )
    with capsys.disabled():
        print("\n[04] ratio metric")
        print(result.sql_statement)

    rendered_sql = result.sql_statement.sql
    assert "NULLIF(listings, 0)" in rendered_sql


def test_05_explain_multiple_metric_types_with_filter_and_dimensions(
    it_helpers: IntegrationTestHelpers, capsys: pytest.CaptureFixture[str]
) -> None:
    """Query simple, ratio, and derived metrics with dimensions and a filter."""
    where_constraint = "{{ Dimension('listing__is_lux_latest') }}"
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["bookings", "listings", "bookings_per_listing", "bookings_per_listing_pct"],
            group_by_names=["listing__country_latest", "listing__is_lux_latest", "metric_time__day"],
            where_constraints=[where_constraint],
            dataflow_plan_optimizations=set(),
        )
    )
    with capsys.disabled():
        print("\n[05] multiple metric types")
        print(result.sql_statement)

    rendered_sql = result.sql_statement.sql
    assert "WHERE listing__is_lux_latest" in rendered_sql
    assert "bookings_per_listing * 100" in rendered_sql
