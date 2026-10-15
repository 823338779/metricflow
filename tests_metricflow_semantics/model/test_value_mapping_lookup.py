from __future__ import annotations

import logging

from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup
from metricflow_semantics.model.semantics.value_mapping_lookup import ValueMappingLookup

from metricflow_semantic_interfaces.references import SemanticModelElementReference, SemanticModelReference
from tests_metricflow.mapping_fixtures import make_mapping_manifest

logger = logging.getLogger(__name__)


def test_mapping_lookup_uses_model_identity() -> None:
    """Same-named dimensions in different models must not share configuration."""
    manifest = make_mapping_manifest()
    other = manifest.semantic_models[0].copy(deep=True)
    other.name = "returns"
    other.dimensions[0].value_mapping.semantic_model = "channels"
    manifest.semantic_models.append(other)
    lookup = ValueMappingLookup(manifest)
    source = SemanticModelElementReference(semantic_model_name="requests", element_name="status")
    other_source = SemanticModelElementReference(semantic_model_name="returns", element_name="status")
    source_mapping = lookup.get(source)
    other_mapping = lookup.get(other_source)
    assert source_mapping is not None and other_mapping is not None
    assert source_mapping.target_model == SemanticModelReference(semantic_model_name="status_codes")
    assert other_mapping.target_model == SemanticModelReference(semantic_model_name="channels")
    assert lookup.get(SemanticModelElementReference(semantic_model_name="requests", element_name="raw_value")) is None


def test_full_manifest_remains_available_for_dictionary_projection() -> None:
    """Graph isolation must not remove dictionary physical relations."""
    manifest = make_mapping_manifest()
    lookup = SemanticManifestLookup(manifest)
    assert lookup.semantic_manifest is manifest
    target = SemanticModelReference(semantic_model_name="status_codes")
    assert target in lookup.semantic_model_lookup.model_reference_to_model
    assert lookup.value_mapping_lookup.mapping_model_references == frozenset(
        {target, SemanticModelReference(semantic_model_name="channels")}
    )
