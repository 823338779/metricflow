from __future__ import annotations

from pathlib import Path

import pytest
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup
from metricflow_semantics.test_helpers.manifest_helpers import mf_load_manifest_from_yaml_directory

from metricflow.dataflow.nodes.aggregate_simple_metric_inputs import AggregateSimpleMetricInputsNode
from metricflow.dataflow.nodes.combine_aggregated_outputs import CombineAggregatedOutputsNode
from metricflow.dataflow.nodes.compute_metrics import ComputeMetricsNode
from metricflow.dataflow.nodes.join_to_base import JoinOnEntitiesNode
from metricflow.engine.metricflow_engine import MetricFlowQueryRequest
from metricflow.protocols.sql_client import SqlEngine
from metricflow.sql.render.postgres import PostgresSQLSqlPlanRenderer
from metricflow_semantic_interfaces.type_enums.metric_type import MetricType
from tests_metricflow.integration.conftest import IntegrationTestHelpers


@pytest.fixture(scope="session")
def simple_semantic_manifest_lookup(template_mapping: dict[str, str]) -> SemanticManifestLookup:
    """Load only the definitions needed by this query test."""
    yaml_directory = Path(__file__).parent / "yaml"
    return SemanticManifestLookup(mf_load_manifest_from_yaml_directory(yaml_directory, template_mapping))


def test_01_explain_simple_metric(
    it_helpers: IntegrationTestHelpers,
    simple_semantic_manifest_lookup: SemanticManifestLookup,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Load the definitions and render one metric without grouping."""
    assert it_helpers.sql_client.sql_engine_type is SqlEngine.POSTGRES
    assert type(it_helpers.sql_client.sql_plan_renderer) is PostgresSQLSqlPlanRenderer
    assert {model.name for model in simple_semantic_manifest_lookup.semantic_manifest.semantic_models} == {
        "bookings_source",
        "listings_latest",
    }
    assert {metric.name: metric.type for metric in simple_semantic_manifest_lookup.semantic_manifest.metrics} == {
        "bookings": MetricType.SIMPLE,
        "listings": MetricType.SIMPLE,
        "bookings_per_listing": MetricType.RATIO,
        "bookings_per_listing_pct": MetricType.DERIVED,
    }

    result = it_helpers.mf_engine.explain(MetricFlowQueryRequest.create(metric_names=["bookings"]))
    with capsys.disabled():
        print("\n[01] simple metric")
        print(result.sql_statement)

    assert {spec.element_name for spec in result.query_spec.metric_specs} == {"bookings"}
    assert {model.semantic_model_name for model in result.dataflow_plan.source_semantic_models} == {"bookings_source"}
    rendered_sql = result.sql_statement.sql
    assert "fct_bookings" in rendered_sql
    assert "SUM(" in rendered_sql


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

    assert {spec.dunder_name for spec in result.query_spec.linkable_specs.as_tuple} == {"metric_time__day"}
    rendered_sql = result.sql_statement.sql
    assert "DATE_TRUNC('day', ds)" in rendered_sql
    assert "metric_time__day" in rendered_sql


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

    assert {model.semantic_model_name for model in result.dataflow_plan.source_semantic_models} == {
        "bookings_source",
        "listings_latest",
    }
    rendered_sql = result.sql_statement.sql
    assert "fct_bookings" in rendered_sql
    assert "dim_listings_latest" in rendered_sql
    assert "JOIN" in rendered_sql
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

    assert {spec.element_name for spec in result.query_spec.metric_specs} == {"bookings_per_listing"}
    rendered_sql = result.sql_statement.sql
    assert "NULLIF(listings, 0)" in rendered_sql
    assert "DOUBLE PRECISION" in rendered_sql
    assert "bookings_per_listing" in rendered_sql


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

    assert {metric_spec.element_name for metric_spec in result.query_spec.metric_specs} == {
        "bookings",
        "listings",
        "bookings_per_listing",
        "bookings_per_listing_pct",
    }
    assert {spec.dunder_name for spec in result.query_spec.linkable_specs.as_tuple} == {
        "listing__country_latest",
        "listing__is_lux_latest",
        "metric_time__day",
    }
    assert len(result.query_spec.filter_intersection.where_filters) == 1
    assert result.query_spec.filter_intersection.where_filters[0].where_sql_template == where_constraint

    assert {
        semantic_model_reference.semantic_model_name
        for semantic_model_reference in result.dataflow_plan.source_semantic_models
    } == {"bookings_source", "listings_latest"}

    nodes_to_visit = [result.dataflow_plan.sink_node]
    dataflow_node_types = set()
    while nodes_to_visit:
        node = nodes_to_visit.pop()
        dataflow_node_types.add(type(node))
        nodes_to_visit.extend(node.parent_nodes)

    assert JoinOnEntitiesNode in dataflow_node_types
    assert AggregateSimpleMetricInputsNode in dataflow_node_types
    assert CombineAggregatedOutputsNode in dataflow_node_types
    assert ComputeMetricsNode in dataflow_node_types

    rendered_sql = result.sql_statement.sql
    assert "fct_bookings" in rendered_sql
    assert "dim_listings_latest" in rendered_sql
    assert "listing__country_latest" in rendered_sql
    assert "listing__is_lux_latest" in rendered_sql
    assert "bookings_per_listing" in rendered_sql
    assert "bookings_per_listing_pct" in rendered_sql
    assert "* 100" in rendered_sql
