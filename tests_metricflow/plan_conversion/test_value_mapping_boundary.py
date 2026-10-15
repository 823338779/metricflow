from __future__ import annotations

import logging
from dataclasses import replace

import pytest
from metricflow_semantics.errors.error_classes import DimensionValueMappingError
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup
from metricflow_semantics.specs.dunder_column_association_resolver import DunderColumnAssociationResolver
from metricflow_semantics.sql.sql_table import SqlTable

from metricflow.dataflow.nodes.apply_dimension_value_mappings import ApplyDimensionValueMappingsNode
from metricflow.dataflow.nodes.order_by_limit import OrderByLimitNode
from metricflow.dataflow.nodes.write_to_data_table import WriteToResultDataTableNode
from metricflow.dataflow.nodes.write_to_table import WriteToResultTableNode
from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow.plan_conversion.to_sql_plan.dataflow_to_sql import DataflowToSqlPlanConverter
from metricflow.sql.optimizer.optimization_levels import SqlGenerationOptionSet, SqlOptimizationLevel
from metricflow.sql.render.duckdb_renderer import DuckDbSqlPlanRenderer
from metricflow_semantic_interfaces.references import SemanticModelElementReference, SemanticModelReference
from tests_metricflow.engine import test_value_mapping_query
from tests_metricflow.mapping_fixtures import make_mapping_manifest

logger = logging.getLogger(__name__)
mapping_engine = test_value_mapping_query.mapping_engine


@pytest.mark.parametrize("ctas", [False, True])
def test_pending_mapping_survives_cte_and_both_sinks(mapping_engine: MetricFlowEngine, ctas: bool) -> None:
    """CTEs optimize the raw branch; the final projection is consumed exactly once."""
    explained = mapping_engine.explain(
        MetricFlowQueryRequest.create(
            metric_names=["requests_count"],
            group_by_names=["request__status"],
            limit=2,
            order_by_names=["request__status"],
            where_constraints=["{{ Dimension('request__status') }} IS NOT NULL"],
        )
    )
    sink = explained.dataflow_plan.sink_nodes[0]
    mapping = sink.parent_nodes[0]
    if ctas:
        sink = WriteToResultTableNode.create(
            parent_node=mapping, output_sql_table=SqlTable(schema_name="main", table_name="mapped_output")
        )
    converter = DataflowToSqlPlanConverter(
        DunderColumnAssociationResolver(), SemanticManifestLookup(make_mapping_manifest())
    )
    result = converter.convert_using_specifics(
        dataflow_plan_node=sink,
        sql_query_plan_id=None,
        nodes_to_convert_to_cte=frozenset((mapping, mapping.parent_nodes[0])),
        optimizers=SqlGenerationOptionSet.options_for_level(SqlOptimizationLevel.O5, False).optimizers,
    )
    sql = DuckDbSqlPlanRenderer().render_sql_plan(result.sql_plan).sql
    assert sql.count("LEFT OUTER JOIN") == 1
    assert result.display_columns == ("request__status",)
    if ctas:
        mapping_engine._sql_client.execute(sql)
        table = mapping_engine._sql_client.query("SELECT * FROM main.mapped_output ORDER BY requests_count DESC")
    else:
        table = mapping_engine._sql_client.query(sql)
    assert table.rows == (("成功", 100), ("失败", 20))


@pytest.mark.parametrize("use_cte", [False, True])
def test_calculation_after_mapping_is_rejected(mapping_engine: MetricFlowEngine, use_cte: bool) -> None:
    """A computational consumer cannot silently discard pending display mappings."""
    explained = mapping_engine.explain(
        MetricFlowQueryRequest.create(metric_names=["requests_count"], group_by_names=["request__status"])
    )
    mapping = explained.dataflow_plan.sink_nodes[0].parent_nodes[0]
    calculation = OrderByLimitNode.create(parent_node=mapping, order_by_specs=(), limit=1)
    sink = WriteToResultDataTableNode.create(parent_node=calculation)
    converter = DataflowToSqlPlanConverter(
        DunderColumnAssociationResolver(), SemanticManifestLookup(make_mapping_manifest())
    )
    with pytest.raises(DimensionValueMappingError, match="final output"):
        converter.convert_using_specifics(sink, None, frozenset((mapping,)) if use_cte else frozenset(), ())


@pytest.mark.parametrize("invalid_binding", ["other_source", "stale_target"])
def test_mapping_binding_must_match_output_origin(mapping_engine: MetricFlowEngine, invalid_binding: str) -> None:
    """Reject cross-source and stale explicit bindings before generating a dictionary join."""
    lookup = SemanticManifestLookup(make_mapping_manifest())
    explained = mapping_engine.explain(
        MetricFlowQueryRequest.create(metric_names=["requests_count"], group_by_names=["request__status"])
    )
    mapping = explained.dataflow_plan.sink_nodes[0].parent_nodes[0]
    assert type(mapping) is ApplyDimensionValueMappingsNode
    binding = mapping.mappings[0]
    if invalid_binding == "other_source":
        wrong_mapping = lookup.value_mapping_lookup.get(
            SemanticModelElementReference(semantic_model_name="requests", element_name="channel")
        )
        assert wrong_mapping is not None
    else:
        wrong_mapping = replace(binding.mapping, target_model=SemanticModelReference(semantic_model_name="channels"))
    invalid = ApplyDimensionValueMappingsNode.create(
        parent_node=mapping.parent_node, mappings=(replace(binding, mapping=wrong_mapping),)
    )
    converter = DataflowToSqlPlanConverter(DunderColumnAssociationResolver(), lookup)
    with pytest.raises(DimensionValueMappingError, match="source"):
        converter.convert_using_specifics(WriteToResultDataTableNode.create(parent_node=invalid), None, frozenset(), ())
