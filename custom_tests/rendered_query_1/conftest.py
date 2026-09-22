"""Fixtures required by the standalone rendered-query test."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from metricflow_semantics.test_helpers.config_helpers import MetricFlowTestConfiguration
from metricflow_semantics.test_helpers.id_helpers import setup_id_generators  # noqa: F401

from tests_metricflow.fixtures.dataflow_fixtures import time_spine_sources  # noqa: F401
from tests_metricflow.fixtures.manifest_fixtures import template_mapping  # noqa: F401
from tests_metricflow.fixtures.sql_client_fixtures import (  # noqa: F401
    ddl_sql_client,
    sql_client,
    warn_user_about_slow_tests_without_parallelism,
)
from tests_metricflow.integration.conftest import it_helpers  # noqa: F401


@pytest.fixture(scope="session")
def mf_test_configuration() -> MetricFlowTestConfiguration:
    """Configure an isolated source schema without loading table snapshots."""
    test_directory = Path(__file__).parent
    schema_name = f"mf_test_{uuid4().hex[:8]}"
    return MetricFlowTestConfiguration(
        sql_engine_url=os.environ.get("MF_SQL_ENGINE_URL", "duckdb://"),
        sql_engine_password=os.environ.get("MF_SQL_ENGINE_PASSWORD", ""),
        mf_system_schema=schema_name,
        mf_source_schema=schema_name,
        display_graphs=False,
        use_persistent_source_schema=False,
        display_snapshots=False,
        overwrite_snapshots=False,
        snapshot_directory=test_directory / "snapshots",
        tests_directory=test_directory,
    )


@pytest.fixture(scope="session")
def create_source_tables() -> None:
    """Skip source table setup because explain() does not execute the rendered SQL."""
    return None
