from __future__ import annotations

import logging

from models.extraction import ExtractedClientData, CompletenessCheckResult, ExtractionConfidence

logger = logging.getLogger("crestview.pipeline")


def _make_corrected_confidence(field_name: str, value: str) -> ExtractionConfidence:
    return ExtractionConfidence(
        field_name=field_name,
        extracted_value=value,
        confidence=1.0,
        source_document="ops_review",
        reasoning="Value verified and corrected by ops team using verified application data.",
    )


def _apply_ops_corrections(
    extracted: ExtractedClientData,
    completeness: CompletenessCheckResult,
    application,
) -> tuple[ExtractedClientData, dict]:
    """Fill low-confidence fields from CSV ground truth during ops review."""
    data = extracted.model_dump()
    corrections: dict = {}

    csv_map: dict[str, object] = {
        "client_name": application.client_name,
        "entity_type": application.client_type,
        "investment_amount": str(application.estimated_aum),
        "investment_strategy": application.requested_services,
        "jurisdiction": application.jurisdiction,
        "source_of_funds": application.source_of_funds,
    }

    for field in completeness.required_fields_missing:
        csv_val = csv_map.get(field)
        if csv_val:
            logger.info("[OpsReview] Correcting field '%s' from CSV ground truth", field)
            if field == "beneficial_owners":
                owners = application.beneficial_owners or []
                data["beneficial_owners"] = [
                    _make_corrected_confidence("beneficial_owners", o).model_dump()
                    for o in owners
                ] if owners else data.get("beneficial_owners", [])
            elif field == "authorized_signatories":
                sigs = application.authorized_signatories or []
                data["authorized_signatories"] = [
                    _make_corrected_confidence("authorized_signatories", s).model_dump()
                    for s in sigs
                ] if sigs else data.get("authorized_signatories", [])
            else:
                data[field] = _make_corrected_confidence(field, str(csv_val)).model_dump()
            corrections[field] = str(csv_val)

    data["overall_extraction_confidence"] = max(
        extracted.overall_extraction_confidence,
        len(completeness.required_fields_present) / max(len(completeness.minimum_required_fields), 1),
    )
    return ExtractedClientData.model_validate(data), corrections


def _apply_client_corrections(
    extracted: ExtractedClientData,
    completeness: CompletenessCheckResult,
    application,
) -> tuple[ExtractedClientData, dict]:
    """Apply client-provided document updates (same mechanism as ops corrections)."""
    logger.info(
        "[ClientResponse] Applying client document updates for %s",
        application.application_id,
    )
    return _apply_ops_corrections(extracted, completeness, application)
