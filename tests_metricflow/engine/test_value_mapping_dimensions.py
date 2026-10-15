from __future__ import annotations

import logging

import pytest
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from tests_metricflow.fixtures.sql_client_fixtures import make_test_sql_client
from tests_metricflow.mapping_fixtures import make_mapping_manifest

logger = logging.getLogger(__name__)


@pytest.mark.parametrize("metric_names", [None, ["requests_count"]])
def test_dictionary_fields_are_not_queryable_business_dimensions(metric_names: list[str] | None) -> None:
    """Even joinable dictionaries must not expose their fields in discovery or queries."""
    engine = MetricFlowEngine(
        semantic_manifest_lookup=SemanticManifestLookup(make_mapping_manifest()),
        sql_client=make_test_sql_client("duckdb:///:memory:", "", "main"),
    )
    dimensions = engine.list_dimensions(metric_names=metric_names)
    assert {dimension.name for dimension in dimensions} >= {"status", "raw_value"}
    assert "display_value" not in {dimension.name for dimension in dimensions}
    assert {
        dimension.semantic_model_reference.semantic_model_name
        for dimension in dimensions
        if dimension.semantic_model_reference is not None
    } == {"requests"}
    status = next(dimension for dimension in dimensions if dimension.name == "status")
    assert status.value_mapping is not None
    assert status.value_mapping.semantic_model == "status_codes"
    with pytest.raises(Exception):
        engine.explain(
            MetricFlowQueryRequest.create(metric_names=["requests_count"], group_by_names=["request__display_value"])
        )
