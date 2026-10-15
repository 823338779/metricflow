from __future__ import annotations

import logging

import pytest

from metricflow_semantic_interfaces.implementations.elements.dimension import PydanticDimension
from metricflow_semantic_interfaces.implementations.elements.measure import PydanticMeasure
from metricflow_semantic_interfaces.implementations.metric import (
    PydanticMetricAggregationParams,
    PydanticMetricTypeParams,
)
from metricflow_semantic_interfaces.implementations.semantic_manifest import PydanticSemanticManifest
from metricflow_semantic_interfaces.test_utils import metric_with_guaranteed_meta, semantic_model_with_guaranteed_meta
from metricflow_semantic_interfaces.type_enums import MetricType
from metricflow_semantic_interfaces.validations.dimension_value_mapping import DimensionValueMappingRule
from tests_metricflow_semantic_interfaces.example_project_configuration import EXAMPLE_PROJECT_CONFIGURATION

logger = logging.getLogger(__name__)


@pytest.fixture
def mapping_manifest() -> PydanticSemanticManifest:
    """A display-only model needs no computational entity relationship."""
    return PydanticSemanticManifest(
        semantic_models=[
            semantic_model_with_guaranteed_meta(
                name="requests",
                dimensions=[
                    PydanticDimension.parse_obj(
                        {
                            "name": "status",
                            "type": "categorical",
                            "value_mapping": {
                                "semantic_model": "status_codes",
                                "value_dimension": "raw_value",
                                "display_dimension": "display_value",
                            },
                        }
                    )
                ],
            ),
            semantic_model_with_guaranteed_meta(
                name="status_codes",
                dimensions=[
                    PydanticDimension(name="raw_value", type="categorical"),
                    PydanticDimension(name="display_value", type="categorical"),
                ],
            ),
        ],
        metrics=[],
        project_configuration=EXAMPLE_PROJECT_CONFIGURATION,
    )


def test_display_only_model_is_valid(mapping_manifest: PydanticSemanticManifest) -> None:
    """Do not require measures or an entity join for dictionary lookup."""
    assert DimensionValueMappingRule.validate_manifest(mapping_manifest) == []


@pytest.mark.parametrize("field", ["semantic_model", "value_dimension", "display_dimension"])
def test_missing_reference_is_rejected(mapping_manifest: PydanticSemanticManifest, field: str) -> None:
    """Resolve references in the named model rather than globally by field name."""
    dimension = mapping_manifest.semantic_models[0].dimensions[0]
    assert dimension.value_mapping is not None
    dimension.value_mapping = dimension.value_mapping.copy(update={field: "missing"})
    issues = DimensionValueMappingRule.validate_manifest(mapping_manifest)
    assert issues
    assert "requests" in issues[0].message
    assert "status" in issues[0].message
    assert field in issues[0].message


@pytest.mark.parametrize("model_index,dimension_index", [(0, 0), (1, 0), (1, 1)])
def test_non_categorical_dimension_is_rejected(
    mapping_manifest: PydanticSemanticManifest, model_index: int, dimension_index: int
) -> None:
    """Mapping must not turn time dimensions into display labels."""
    from metricflow_semantic_interfaces.type_enums import DimensionType

    mapping_manifest.semantic_models[model_index].dimensions[dimension_index].type = DimensionType.TIME
    assert DimensionValueMappingRule.validate_manifest(mapping_manifest)


def test_recursive_mapping_is_rejected(mapping_manifest: PydanticSemanticManifest) -> None:
    """A display-only model cannot itself start a mapping chain."""
    mapping_manifest.semantic_models[1].dimensions[0].value_mapping = (
        mapping_manifest.semantic_models[0].dimensions[0].value_mapping
    )
    assert DimensionValueMappingRule.validate_manifest(mapping_manifest)


@pytest.mark.parametrize("use_measure", [True, False])
def test_computational_mapping_model_is_rejected(mapping_manifest: PydanticSemanticManifest, use_measure: bool) -> None:
    """Handle both legacy measures and native simple metric aggregation params."""
    if use_measure:
        mapping_manifest.semantic_models[1].measures = [PydanticMeasure(name="count", agg="count")]
    else:
        mapping_manifest.metrics = [
            metric_with_guaranteed_meta(
                name="count",
                type=MetricType.SIMPLE,
                type_params=PydanticMetricTypeParams(
                    metric_aggregation_params=PydanticMetricAggregationParams(
                        semantic_model="status_codes", agg="count", expr="1"
                    )
                ),
            )
        ]
    assert DimensionValueMappingRule.validate_manifest(mapping_manifest)
