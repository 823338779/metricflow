from __future__ import annotations

import logging
from collections import defaultdict
from typing import Generic, Sequence

from metricflow_semantic_interfaces.protocols import SemanticManifestT, SemanticModel
from metricflow_semantic_interfaces.type_enums import DimensionType
from metricflow_semantic_interfaces.validations.validator_helpers import (
    FileContext,
    SemanticManifestValidationRule,
    ValidationError,
    ValidationIssue,
    validate_safely,
)

logger = logging.getLogger(__name__)


class DimensionValueMappingRule(SemanticManifestValidationRule[SemanticManifestT], Generic[SemanticManifestT]):
    """Validate display references without querying data or checking key uniqueness."""

    @staticmethod
    @validate_safely("validating dimension value mappings")
    def validate_manifest(semantic_manifest: SemanticManifestT) -> Sequence[ValidationIssue]:
        """Require categorical, non-recursive, display-only mapping targets."""
        models_by_name: dict[str, list[SemanticModel]] = defaultdict(list)
        for model in semantic_manifest.semantic_models:
            models_by_name[model.name].append(model)
        metric_sources = {
            metric.type_params.metric_aggregation_params.semantic_model
            for metric in semantic_manifest.metrics
            if metric.type_params.metric_aggregation_params is not None
        }
        issues: list[ValidationIssue] = []
        for source in semantic_manifest.semantic_models:
            for dimension in source.dimensions:
                mapping = dimension.value_mapping
                if mapping is None:
                    continue
                prefix = f"Dimension {source.name}.{dimension.name} value_mapping"
                errors: list[str] = []
                if dimension.type is not DimensionType.CATEGORICAL:
                    errors.append("requires a categorical source dimension")
                targets = models_by_name[mapping.semantic_model]
                if len(targets) != 1:
                    errors.append(f"semantic_model '{mapping.semantic_model}' must resolve to exactly one model")
                else:
                    target = targets[0]
                    if target.measures or target.name in metric_sources:
                        errors.append(f"semantic_model '{target.name}' must not be a metric calculation source")
                    if any(item.value_mapping is not None for item in target.dimensions):
                        errors.append(f"semantic_model '{target.name}' must not declare recursive value_mapping")
                    for field, name in (
                        ("value_dimension", mapping.value_dimension),
                        ("display_dimension", mapping.display_dimension),
                    ):
                        matches = [item for item in target.dimensions if item.name == name]
                        if len(matches) != 1:
                            errors.append(f"{field} '{name}' must resolve within semantic_model '{target.name}'")
                        elif matches[0].type is not DimensionType.CATEGORICAL:
                            errors.append(f"{field} '{name}' must be categorical")
                issues.extend(
                    ValidationError(
                        message=f"{prefix}: {error}",
                        context=FileContext.from_metadata(dimension.metadata or source.metadata),
                    )
                    for error in errors
                )
        return issues
