from __future__ import annotations

import logging
from dataclasses import dataclass

from metricflow.sql.sql_select_node import SqlSelectStatementNode

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutputValueMapping:
    """Resolved physical projection for one final output column."""

    source_column_name: str
    mapping_select: SqlSelectStatementNode
    value_column_name: str
    display_column_name: str


@dataclass(frozen=True)
class PendingOutputValueMapping:
    """Consume once, after optimizing the raw calculation SQL."""

    mappings: tuple[OutputValueMapping, ...]
    order_by_columns: tuple[tuple[str, bool], ...]
