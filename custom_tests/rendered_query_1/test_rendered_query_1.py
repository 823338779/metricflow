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
from tests_metricflow.integration.conftest import IntegrationTestHelpers


@pytest.fixture(scope="session")
def simple_semantic_manifest_lookup(template_mapping: dict[str, str]) -> SemanticManifestLookup:
    """Load only the definitions needed by this query test."""
    yaml_directory = Path(__file__).parent / "yaml"
    return SemanticManifestLookup(mf_load_manifest_from_yaml_directory(yaml_directory, template_mapping))


def test_explain_derived_metric_with_filter_and_dimensions(
    it_helpers: IntegrationTestHelpers, simple_semantic_manifest_lookup: SemanticManifestLookup
) -> None:
    """Exercise metric dependencies, filtering, dimensions, joins, aggregation, and SQL rendering through explain()."""
    assert {model.name for model in simple_semantic_manifest_lookup.semantic_manifest.semantic_models} == {
        "bookings_source",
        "listings_latest",
    }
    assert {metric.name for metric in simple_semantic_manifest_lookup.semantic_manifest.metrics} == {
        "bookings",
        "listings",
        "bookings_per_listing",
    }

    where_constraint = "{{ Dimension('listing__is_lux_latest') }}"
    result = it_helpers.mf_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["bookings_per_listing"],
            group_by_names=["listing__country_latest", "metric_time__day"],
            where_constraints=[where_constraint],
            dataflow_plan_optimizations=set(),
        )
    )

    assert {metric_spec.element_name for metric_spec in result.query_spec.metric_specs} == {"bookings_per_listing"}
    assert {spec.dunder_name for spec in result.query_spec.linkable_specs.as_tuple} == {
        "listing__country_latest",
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
