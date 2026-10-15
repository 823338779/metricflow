from __future__ import annotations

import logging

import pytest
from metricflow_semantics.sql.sql_bind_parameters import SqlBindParameterSet
from metricflow_semantics.sql.sql_exprs import SqlCastToStringExpression, SqlStringExpression

from metricflow.sql.render.athena import AthenaSqlPlanRenderer
from metricflow.sql.render.big_query import BigQuerySqlPlanRenderer
from metricflow.sql.render.databricks import DatabricksSqlPlanRenderer
from metricflow.sql.render.duckdb_renderer import DuckDbSqlPlanRenderer
from metricflow.sql.render.postgres import PostgresSQLSqlPlanRenderer
from metricflow.sql.render.redshift import RedshiftSqlPlanRenderer
from metricflow.sql.render.snowflake import SnowflakeSqlPlanRenderer
from metricflow.sql.render.sql_plan_renderer import SqlPlanRenderer
from metricflow.sql.render.trino import TrinoSqlPlanRenderer
from metricflow.sql.render.vertica import VerticaSqlPlanRenderer

logger = logging.getLogger(__name__)


@pytest.mark.parametrize(
    "renderer,expected_type",
    [
        (DuckDbSqlPlanRenderer(), "VARCHAR"),
        (PostgresSQLSqlPlanRenderer(), "TEXT"),
        (BigQuerySqlPlanRenderer(), "STRING"),
        (DatabricksSqlPlanRenderer(), "STRING"),
        (RedshiftSqlPlanRenderer(), "VARCHAR(65535)"),
        (SnowflakeSqlPlanRenderer(), "VARCHAR"),
        (TrinoSqlPlanRenderer(), "VARCHAR"),
        (AthenaSqlPlanRenderer(), "VARCHAR"),
        (VerticaSqlPlanRenderer(), "LONG VARCHAR"),
    ],
)
def test_display_cast_preserves_parameters_and_warehouse_text_type(
    renderer: SqlPlanRenderer, expected_type: str
) -> None:
    """Display text must not use a warehouse's short default CHAR type or lose bindings."""
    parameters = SqlBindParameterSet.create_from_dict({"code": 404})
    expression = SqlCastToStringExpression.create(
        arg=SqlStringExpression.create(sql_expr=":code", bind_parameter_set=parameters)
    )
    rendered = renderer.expr_renderer.render_sql_expr(expression)
    assert rendered.sql == f"CAST(:code AS {expected_type})"
    assert rendered.bind_parameter_set == parameters
