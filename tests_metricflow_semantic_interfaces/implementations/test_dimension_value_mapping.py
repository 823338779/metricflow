from __future__ import annotations

import logging

import pytest

from metricflow_semantic_interfaces.implementations.elements.dimension import PydanticDimension

logger = logging.getLogger(__name__)

MAPPING = {
    "semantic_model": "status_codes",
    "value_dimension": "raw_value",
    "display_dimension": "display_value",
}


def test_dimension_mapping_survives_manifest_serialization() -> None:
    """Dropping the formal field would silently disable display mapping."""
    dimension = PydanticDimension.parse_obj({"name": "status", "type": "categorical", "value_mapping": MAPPING})
    restored = PydanticDimension.parse_raw(dimension.json())
    assert restored.value_mapping is not None
    assert restored.value_mapping.dict() == MAPPING


def test_legacy_dimension_has_no_mapping() -> None:
    """Old manifests must remain readable without opting into mapping."""
    assert PydanticDimension(name="status", type="categorical").value_mapping is None


@pytest.mark.parametrize(
    "mapping",
    [
        {},
        {**MAPPING, "unknown": "ignored"},
        {**MAPPING, "semantic_model": ""},
        {**MAPPING, "value_dimension": "   "},
        {**MAPPING, "display_dimension": 123},
        {**MAPPING, "semantic_model": None},
    ],
)
def test_invalid_mapping_is_rejected(mapping: dict[str, object]) -> None:
    """Unknown fields and coerced/empty references must not reach planning."""
    with pytest.raises(ValueError):
        PydanticDimension.parse_obj({"name": "status", "type": "categorical", "value_mapping": mapping})
