from __future__ import annotations

import logging

from metricflow_semantic_interfaces.implementations.semantic_manifest import PydanticSemanticManifest

logger = logging.getLogger(__name__)


def make_mapping_manifest() -> PydanticSemanticManifest:
    """A small native manifest shared by mapping plan and database tests."""
    status_mapping = {
        "semantic_model": "status_codes",
        "value_dimension": "raw_value",
        "display_dimension": "display_value",
    }
    channel_mapping = {**status_mapping, "semantic_model": "channels"}
    return PydanticSemanticManifest.parse_obj(
        {
            "semantic_models": [
                {
                    "name": "requests",
                    "node_relation": {"schema_name": "main", "alias": "requests"},
                    "defaults": {"agg_time_dimension": "ds"},
                    "entities": [{"name": "request", "type": "primary", "expr": "id"}],
                    "dimensions": [
                        {"name": "status", "type": "categorical", "value_mapping": status_mapping},
                        {"name": "target_status", "type": "categorical", "value_mapping": status_mapping},
                        {"name": "channel", "type": "categorical", "value_mapping": channel_mapping},
                        {"name": "raw_value", "type": "categorical"},
                        {"name": "ds", "type": "time", "type_params": {"time_granularity": "day"}},
                    ],
                },
                *[
                    {
                        "name": name,
                        "node_relation": {"schema_name": "main", "alias": name},
                        # This normally exposes a computational join path; mapping must isolate it.
                        "entities": [{"name": "request", "type": "primary", "expr": "code"}],
                        "dimensions": [
                            {"name": "raw_value", "type": "categorical", "expr": "code"},
                            {"name": "display_value", "type": "categorical", "expr": "label"},
                        ],
                    }
                    for name in ("status_codes", "channels")
                ],
            ],
            "metrics": [
                {
                    "name": "requests_count",
                    "type": "simple",
                    "type_params": {
                        "expr": "amount",
                        "metric_aggregation_params": {
                            "semantic_model": "requests",
                            "agg": "sum",
                            "agg_time_dimension": "ds",
                        },
                    },
                }
            ],
            "project_configuration": {
                "time_spines": [
                    {
                        "node_relation": {"schema_name": "main", "alias": "time_spine"},
                        "primary_column": {"name": "ds", "time_granularity": "day"},
                    }
                ]
            },
        }
    )
