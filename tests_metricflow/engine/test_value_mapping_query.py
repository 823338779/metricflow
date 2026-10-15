from __future__ import annotations

import logging

import pytest
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup
from metricflow_semantics.specs.query_param_implementations import DimensionOrEntityParameter, OrderByParameter

from metricflow.dataflow.nodes.apply_dimension_value_mappings import ApplyDimensionValueMappingsNode
from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest, MetricFlowQueryType
from metricflow.sql.optimizer.optimization_levels import SqlOptimizationLevel
from metricflow_semantic_interfaces.implementations.metric import PydanticMetric
from tests_metricflow.fixtures.sql_client_fixtures import make_test_sql_client
from tests_metricflow.mapping_fixtures import make_mapping_manifest

logger = logging.getLogger(__name__)


@pytest.fixture
def mapping_engine() -> MetricFlowEngine:
    """Exercise warehouse SQL against real numeric keys and text labels."""
    client = make_test_sql_client("duckdb:///:memory:", "", "main")
    client.execute(
        "CREATE TABLE requests(id INT, status INT, target_status INT, channel INT, raw_value INT, ds DATE, amount INT)"
    )
    client.execute(
        "INSERT INTO requests VALUES (1,200,404,1,1,'2026-01-01',100), (2,404,502,1,2,'2026-01-01',20), (3,502,200,2,3,'2026-01-01',10), (4,503,200,2,4,'2026-01-01',4), (5,504,200,2,5,'2026-01-01',5), (6,NULL,200,2,6,'2026-01-01',6)"
    )
    client.execute("CREATE TABLE status_codes(code INT, label VARCHAR)")
    client.execute("INSERT INTO status_codes VALUES (200,'成功'),(404,'失败'),(502,'失败'),(503,NULL),(504,'')")
    client.execute("CREATE TABLE channels(code INT, label VARCHAR)")
    client.execute("INSERT INTO channels VALUES (1,'web'), (2,'mobile')")
    return MetricFlowEngine(semantic_manifest_lookup=SemanticManifestLookup(make_mapping_manifest()), sql_client=client)


def test_execution_preserves_grain_raw_order_limit_and_filter(mapping_engine: MetricFlowEngine) -> None:
    """Two identical labels remain separate; the database replaces values."""
    request = MetricFlowQueryRequest.create(
        metric_names=["requests_count"],
        group_by_names=["request__status"],
        order_by_names=["request__status"],
        where_constraints=["{{ Dimension('request__status') }} IS NOT NULL"],
        limit=3,
    )
    explanation = mapping_engine.explain(request)
    result = mapping_engine.query(request)
    assert result.sql == explanation.sql_statement.sql
    assert result.result_df is not None
    assert result.result_df.rows == (("成功", 100), ("失败", 20), ("失败", 10))
    fallback = mapping_engine.query(
        MetricFlowQueryRequest.create(
            metric_names=["requests_count"],
            group_by_names=["request__status"],
            where_constraints=["{{ Dimension('request__status') }} >= 503"],
            order_by_names=["request__status"],
        )
    )
    assert fallback.result_df is not None
    assert fallback.result_df.rows == (("503", 4), ("", 5))


@pytest.mark.parametrize("predicate", ["IS NULL", "= 999"])
def test_null_and_empty_display_columns_are_text(mapping_engine: MetricFlowEngine, predicate: str) -> None:
    """Type metadata remains text even when value inference has no non-null sample."""
    result = mapping_engine.query(
        MetricFlowQueryRequest.create(
            metric_names=["requests_count"],
            group_by_names=["request__status"],
            where_constraints=["{{ Dimension('request__status') }} " + predicate],
        )
    )
    assert result.result_df is not None
    assert result.result_df.column_descriptions[0].column_type is str
    assert result.result_df is not None
    assert result.result_df.rows == ((None, 6),) if predicate == "IS NULL" else result.result_df.rows == ()


def test_mapping_is_explicit_and_only_applies_to_metric_output() -> None:
    """Keep calculation and distinct-value queries on raw codes."""
    engine = MetricFlowEngine(
        semantic_manifest_lookup=SemanticManifestLookup(make_mapping_manifest()),
        sql_client=make_test_sql_client("duckdb:///:memory:", "", "main"),
    )
    request = MetricFlowQueryRequest.create(
        metric_names=["requests_count"],
        group_by_names=["request__status", "request__target_status"],
        order_by_names=["request__status"],
        limit=2,
    )
    explanation = engine.explain(request)
    assert len(explanation.query_spec.output_value_mapping_specs) == 2
    assert (
        explanation.query_spec.with_time_range_constraint(None).output_value_mapping_specs
        == explanation.query_spec.output_value_mapping_specs
    )
    mapping_node = explanation.dataflow_plan.sink_nodes[0].parent_nodes[0]
    assert mapping_node.description == "Apply Dimension Value Mappings"
    assert type(mapping_node) is ApplyDimensionValueMappingsNode
    assert len(mapping_node.mappings) == 2
    assert not mapping_node.aggregated_to_elements
    assert mapping_node.with_new_parents(mapping_node.parent_nodes).functionally_identical(mapping_node)
    sql = explanation.sql_statement.sql
    assert sql.count("LEFT OUTER JOIN") == 2
    assert "COALESCE" in sql
    raw = engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["requests_count"],
            group_by_names=["request__status"],
            query_type=MetricFlowQueryType.DIMENSION_VALUES,
        )
    )
    assert not raw.query_spec.output_value_mapping_specs
    assert "status_codes" not in raw.sql_statement.sql


@pytest.mark.parametrize("level", list(SqlOptimizationLevel))
def test_multiple_aliased_mappings_at_each_optimization_level(
    mapping_engine: MetricFlowEngine, level: SqlOptimizationLevel
) -> None:
    """All optimizer levels preserve aliases, raw ordering and independent joins."""
    status = DimensionOrEntityParameter("request__status", alias="status_label")
    target = DimensionOrEntityParameter("request__target_status", alias="target_label")
    result = mapping_engine.query(
        MetricFlowQueryRequest.create(
            metric_names=["requests_count"],
            group_by=[status, target],
            order_by=[OrderByParameter(status)],
            limit=2,
            where_constraints=["{{ Dimension('request__status') }} IS NOT NULL"],
            sql_optimization_level=level,
        )
    )
    assert result.result_df is not None
    assert result.result_df.column_names == ("status_label", "target_label", "requests_count")
    assert result.result_df is not None
    assert result.result_df.rows == (("成功", "失败", 100), ("失败", "失败", 20))
    assert result.sql.count("LEFT OUTER JOIN") == 2


def test_filter_only_dimension_is_not_mapped(mapping_engine: MetricFlowEngine) -> None:
    """Filtering a mapped dimension must not introduce a display join."""
    request = MetricFlowQueryRequest.create(
        metric_names=["requests_count"],
        where_constraints=["{{ Dimension('request__status') }} = 404"],
    )
    result = mapping_engine.query(request)
    assert result.result_df is not None
    assert result.result_df.rows == ((20,),)
    assert "status_codes" not in result.sql


def test_ratio_and_distinct_users_keep_raw_groups(mapping_engine: MetricFlowEngine) -> None:
    """A shared user in two failed codes must not merge their distinct counts or ratios."""
    manifest = make_mapping_manifest()
    manifest.metrics.extend(
        [
            PydanticMetric.parse_obj(
                {
                    "name": "users",
                    "type": "simple",
                    "type_params": {
                        "expr": "channel",
                        "metric_aggregation_params": {
                            "semantic_model": "requests",
                            "agg": "count_distinct",
                            "agg_time_dimension": "ds",
                        },
                    },
                }
            ),
            PydanticMetric.parse_obj(
                {
                    "name": "amount_per_user",
                    "type": "ratio",
                    "type_params": {"numerator": {"name": "requests_count"}, "denominator": {"name": "users"}},
                }
            ),
        ]
    )
    mapping_engine._sql_client.execute("UPDATE requests SET channel = 1 WHERE status = 502")
    engine = MetricFlowEngine(
        semantic_manifest_lookup=SemanticManifestLookup(manifest), sql_client=mapping_engine._sql_client
    )
    result = engine.query(
        MetricFlowQueryRequest.create(
            metric_names=["users", "amount_per_user"],
            group_by_names=["request__status"],
            order_by_names=["request__status"],
            where_constraints=["{{ Dimension('request__status') }} IN (404, 502)"],
        )
    )
    assert result.result_df is not None
    assert result.result_df.rows == (("失败", 1, 20.0), ("失败", 1, 10.0))


def test_dictionary_text_is_read_at_query_time(mapping_engine: MetricFlowEngine) -> None:
    """Changing dictionary contents needs no manifest rebuild and missing keys fall back."""
    request = MetricFlowQueryRequest.create(
        metric_names=["requests_count"],
        group_by_names=["request__status"],
        order_by_names=["request__status"],
        where_constraints=["{{ Dimension('request__status') }} IN (404, 502)"],
    )
    initial = mapping_engine.query(request)
    assert initial.result_df is not None
    assert initial.result_df.rows == (("失败", 20), ("失败", 10))
    mapping_engine._sql_client.execute("UPDATE status_codes SET label = 'updated' WHERE code = 404")
    mapping_engine._sql_client.execute("DELETE FROM status_codes WHERE code = 502")
    changed = mapping_engine.query(request)
    assert changed.result_df is not None
    assert changed.result_df.rows == (("updated", 20), ("502", 10))
