from __future__ import annotations

import logging
from pathlib import Path
from typing import Generator

import pytest
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup
from metricflow_semantics.test_helpers.manifest_helpers import mf_load_manifest_from_yaml_directory
from metricflow_semantics.test_helpers.time_helpers import ConfigurableTimeSource
from sqlalchemy import create_engine

from metricflow.engine.metricflow_engine import MetricFlowEngine
from metricflow.protocols.sql_client import SqlClient, SqlEngine
from metricflow.sql.render.postgres import PostgresSQLSqlPlanRenderer
from metricflow_semantic_interfaces.test_utils import as_datetime
from tests_metricflow.fixtures.sql_clients.sqlalchemy_client import SqlAlchemyBasedSqlClient
from tests_metricflow.integration.conftest import IntegrationTestHelpers

logger = logging.getLogger(__name__)

SOURCE_SCHEMA = "rendered_query_1"


@pytest.fixture(scope="module")
def simple_semantic_manifest_lookup() -> SemanticManifestLookup:
    """Build and validate the four-model manifest entirely in memory."""
    yaml_directory = Path(__file__).parent / "yaml"
    return SemanticManifestLookup(
        mf_load_manifest_from_yaml_directory(yaml_directory, {"source_schema": SOURCE_SCHEMA})
    )


@pytest.fixture(scope="module")
def sql_client() -> Generator[SqlClient, None, None]:
    """Supply PostgreSQL rendering without requiring a database server.

    SQLite is only an inert, in-memory backing engine for the existing test client.
    These tests call explain(), never query(), so no tables are created or read.
    """
    engine = create_engine("sqlite:///:memory:")
    client = SqlAlchemyBasedSqlClient(
        engine=engine,
        sql_engine_type=SqlEngine.POSTGRES,
        sql_plan_renderer=PostgresSQLSqlPlanRenderer(),
        dry_run_engine=engine,
    )
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def it_helpers(
    simple_semantic_manifest_lookup: SemanticManifestLookup,
    sql_client: SqlClient,
) -> IntegrationTestHelpers:
    """Expose the familiar walkthrough entry point without warehouse setup fixtures."""
    return IntegrationTestHelpers(
        mf_engine=MetricFlowEngine(
            semantic_manifest_lookup=simple_semantic_manifest_lookup,
            sql_client=sql_client,
            time_source=ConfigurableTimeSource(as_datetime("2024-01-01")),
        ),
        mf_system_schema=SOURCE_SCHEMA,
        source_schema=SOURCE_SCHEMA,
        sql_client=sql_client,
    )
