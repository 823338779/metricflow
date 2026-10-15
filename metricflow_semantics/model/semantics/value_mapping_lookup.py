from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from metricflow_semantics.errors.error_classes import DimensionValueMappingError

from metricflow_semantic_interfaces.protocols import SemanticManifest
from metricflow_semantic_interfaces.references import (
    DimensionReference,
    SemanticModelElementReference,
    SemanticModelReference,
)
from metricflow_semantic_interfaces.validations.dimension_value_mapping import DimensionValueMappingRule

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedValueMapping:
    """Mapping references bound to the exact source semantic model."""

    source_dimension: SemanticModelElementReference
    target_model: SemanticModelReference
    value_dimension: DimensionReference
    display_dimension: DimensionReference


class ValueMappingLookup:
    """Resolve display mappings independently of calculation graph reachability."""

    def __init__(self, semantic_manifest: SemanticManifest) -> None:
        """Validate references and index by source model and dimension identity."""
        issues = DimensionValueMappingRule.validate_manifest(semantic_manifest)
        if issues:
            raise DimensionValueMappingError("Invalid dimension value mapping", {"mapping": issues[0].message})
        self._mappings: dict[SemanticModelElementReference, ResolvedValueMapping] = {}
        for model in semantic_manifest.semantic_models:
            for dimension in model.dimensions:
                mapping = dimension.value_mapping
                if mapping is None:
                    continue
                source = SemanticModelElementReference(semantic_model_name=model.name, element_name=dimension.name)
                self._mappings[source] = ResolvedValueMapping(
                    source_dimension=source,
                    target_model=SemanticModelReference(semantic_model_name=mapping.semantic_model),
                    value_dimension=DimensionReference(element_name=mapping.value_dimension),
                    display_dimension=DimensionReference(element_name=mapping.display_dimension),
                )

    def get(self, source: SemanticModelElementReference) -> Optional[ResolvedValueMapping]:
        """Look up a mapping without conflating same-named dimensions."""
        return self._mappings.get(source)

    @property
    def mapping_model_references(self) -> frozenset[SemanticModelReference]:
        """Models reserved exclusively for output display."""
        return frozenset(mapping.target_model for mapping in self._mappings.values())
