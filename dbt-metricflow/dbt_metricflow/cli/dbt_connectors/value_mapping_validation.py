from __future__ import annotations

import logging
import re

from dbt.adapters.base import BaseAdapter, Column
from metricflow_semantics.errors.error_classes import DimensionValueMappingError
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup
from metricflow_semantics.specs.dunder_column_association_resolver import DunderColumnAssociationResolver
from metricflow_semantics.sql.sql_exprs import SqlIsNullExpression

from dbt_metricflow.cli.dbt_connectors.adapter_backed_client import SupportedAdapterTypes
from metricflow.dataset.convert_semantic_model import SemanticModelToDataSetConverter
from metricflow.dataset.sql_dataset import SqlDataSet
from metricflow.sql.render.duckdb_renderer import DuckDbSqlPlanRenderer
from metricflow.sql.sql_plan import SqlPlan, SqlSelectColumn
from metricflow.sql.sql_select_node import SqlSelectStatementNode
from metricflow_semantic_interfaces.references import SemanticModelElementReference

logger = logging.getLogger(__name__)


def _projection(data_set: SqlDataSet, dimension_name: str) -> SqlSelectStatementNode:
    """Project only the declared dimension expression, including model expr aliases."""
    instance = next(
        item
        for item in data_set.instance_set.dimension_instances
        if item.spec.element_name == dimension_name and not item.spec.entity_links
    )
    source = data_set.checked_sql_select_node
    return SqlSelectStatementNode.create(
        description="Validate dimension mapping type",
        select_columns=tuple(
            column for column in source.select_columns if column.column_alias == instance.associated_column.column_name
        ),
        from_source=source.from_source,
        from_source_alias=source.from_source_alias,
        limit=0,
    )


def _type_family(column: Column) -> str:
    """Normalize parameterized scalar types that adapters may return verbatim."""
    dtype = column.dtype.lower().strip()
    # DuckDB returns DECIMAL(12,2), while adapter is_number() recognizes DECIMAL.
    if column.is_number() or re.fullmatch(r"(decimal|numeric|number)\s*\(\s*\d+\s*(,\s*\d+\s*)?\)", dtype):
        return "number"
    if column.is_string() or re.fullmatch(r"(varchar|char|character varying)\s*\(\s*\d+\s*\)", dtype):
        return "text"
    return dtype


def _compatible(left: Column, right: Column) -> bool:
    """Compare adapter type families; never stringify join keys."""
    return _type_family(left) == _type_family(right)


def validate_value_mapping_data(semantic_manifest_lookup: SemanticManifestLookup, adapter: BaseAdapter) -> None:
    """Validate physical types and null keys without scanning for duplicate keys."""
    lookup = semantic_manifest_lookup.value_mapping_lookup
    if not lookup.mapping_model_references:
        return
    renderer = (
        DuckDbSqlPlanRenderer()
        if adapter.type() == "starrocks"
        else SupportedAdapterTypes(adapter.type()).sql_plan_renderer
    )
    converter = SemanticModelToDataSetConverter(
        column_association_resolver=DunderColumnAssociationResolver(),
        manifest_lookup=semantic_manifest_lookup,
    )
    checked_targets: set[tuple[str, str, str]] = set()
    with adapter.connection_named("validate_dimension_value_mapping"):
        for model in semantic_manifest_lookup.semantic_manifest.semantic_models:
            for dimension in model.dimensions:
                source_ref = SemanticModelElementReference(semantic_model_name=model.name, element_name=dimension.name)
                mapping = lookup.get(source_ref)
                if mapping is None:
                    continue
                context = {
                    "dimension": f"{model.name}.{dimension.name}",
                    "mapping_model": mapping.target_model.semantic_model_name,
                }
                source = converter.create_sql_source_data_set(model.reference)
                target = converter.create_sql_source_data_set(mapping.target_model)
                source_projection = _projection(source, dimension.name)
                key_projection = _projection(target, mapping.value_dimension.element_name)
                label_projection = _projection(target, mapping.display_dimension.element_name)
                source_type = adapter.get_column_schema_from_query(
                    renderer.render_sql_plan(SqlPlan(render_node=source_projection)).sql
                )[0]
                key_type = adapter.get_column_schema_from_query(
                    renderer.render_sql_plan(SqlPlan(render_node=key_projection)).sql
                )[0]
                label_type = adapter.get_column_schema_from_query(
                    renderer.render_sql_plan(SqlPlan(render_node=label_projection)).sql
                )[0]
                if not _compatible(source_type, key_type):
                    raise DimensionValueMappingError("Incompatible dimension mapping key types", context)
                if _type_family(label_type) != "text":
                    raise DimensionValueMappingError("Dimension mapping display value must be text", context)
                identity = (
                    mapping.target_model.semantic_model_name,
                    mapping.value_dimension.element_name,
                    mapping.display_dimension.element_name,
                )
                if identity in checked_targets:
                    continue
                checked_targets.add(identity)
                key = key_projection.select_columns[0]
                null_probe = SqlSelectStatementNode.create(
                    description="Validate dimension mapping non-null key",
                    select_columns=(SqlSelectColumn(expr=key.expr, column_alias=key.column_alias),),
                    from_source=key_projection.from_source,
                    from_source_alias=key_projection.from_source_alias,
                    where=SqlIsNullExpression.create(arg=key.expr),
                    limit=1,
                )
                _, rows = adapter.execute(renderer.render_sql_plan(SqlPlan(render_node=null_probe)).sql, fetch=True)
                if len(rows) > 0:
                    raise DimensionValueMappingError("Dimension mapping raw value contains NULL", context)
