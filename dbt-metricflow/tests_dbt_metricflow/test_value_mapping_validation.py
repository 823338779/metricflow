from __future__ import annotations

import logging
from contextlib import nullcontext
from typing import cast

import duckdb
import pytest
from dbt.adapters.base import BaseAdapter
from dbt.adapters.duckdb.column import DuckDBColumn
from metricflow_semantics.errors.error_classes import DimensionValueMappingError
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

from dbt_metricflow.cli.dbt_connectors.value_mapping_validation import validate_value_mapping_data
from tests_metricflow.mapping_fixtures import make_mapping_manifest

logger = logging.getLogger(__name__)


class QueryAdapter:
    """Small adapter contract harness executing actual DuckDB SQL and schema discovery."""

    def __init__(self, key_type: str = "INTEGER", label_type: str = "VARCHAR") -> None:
        """Create real tables with independently configurable dictionary column types."""
        self.connection = duckdb.connect()
        self.statements: list[str] = []
        self.connection.execute("CREATE TABLE requests(id INT, status INT, target_status INT, channel INT)")
        self.connection.execute(f"CREATE TABLE status_codes(code {key_type}, label {label_type})")
        self.connection.execute("CREATE TABLE channels(code INT, label VARCHAR)")

    def type(self) -> str:  # noqa: D102
        return "duckdb"

    def connection_named(self, name: str) -> nullcontext[None]:  # noqa: D102
        return nullcontext()

    def get_column_schema_from_query(self, sql: str) -> list[DuckDBColumn]:  # noqa: D102
        self.statements.append(sql)
        return [
            DuckDBColumn(column=row[0], dtype=row[1])
            for row in self.connection.execute("DESCRIBE (" + sql + ")").fetchall()
        ]

    def execute(self, sql: str, fetch: bool) -> tuple[None, list[tuple[object, ...]]]:  # noqa: D102
        self.statements.append(sql)
        return None, self.connection.execute(sql).fetchall()


@pytest.mark.parametrize("key_type", ["INTEGER", "DECIMAL(12,2)"])
def test_empty_mapping_validates_without_unique_scans(key_type: str) -> None:
    """Empty dictionaries are valid and uniqueness is an external guarantee."""
    adapter = QueryAdapter(key_type)
    validate_value_mapping_data(SemanticManifestLookup(make_mapping_manifest()), cast(BaseAdapter, adapter))
    assert adapter.statements
    assert not any(word in sql.upper() for sql in adapter.statements for word in ("COUNT(", "DISTINCT", "GROUP BY"))


@pytest.mark.parametrize("key_type,label_type", [("VARCHAR", "VARCHAR"), ("INTEGER", "INTEGER")])
def test_incompatible_types_fail_before_publication(key_type: str, label_type: str) -> None:
    """Do not cast incompatible join keys to hide schema errors."""
    with pytest.raises(DimensionValueMappingError):
        validate_value_mapping_data(
            SemanticManifestLookup(make_mapping_manifest()), cast(BaseAdapter, QueryAdapter(key_type, label_type))
        )


def test_null_key_rejected_but_null_text_label_allowed() -> None:
    """Null labels fall back at query time; null dictionary keys are invalid."""
    adapter = QueryAdapter()
    adapter.connection.execute("INSERT INTO status_codes VALUES (200, NULL)")
    validate_value_mapping_data(SemanticManifestLookup(make_mapping_manifest()), cast(BaseAdapter, adapter))
    adapter.connection.execute("INSERT INTO status_codes VALUES (NULL, 'bad')")
    with pytest.raises(DimensionValueMappingError, match="NULL"):
        validate_value_mapping_data(SemanticManifestLookup(make_mapping_manifest()), cast(BaseAdapter, adapter))


def test_missing_physical_column_keeps_database_error() -> None:
    """Warehouse schema failures are not reclassified as mapping configuration errors."""
    adapter = QueryAdapter()
    adapter.connection.execute("ALTER TABLE status_codes DROP COLUMN label")
    with pytest.raises(duckdb.BinderException, match="label"):
        validate_value_mapping_data(SemanticManifestLookup(make_mapping_manifest()), cast(BaseAdapter, adapter))
