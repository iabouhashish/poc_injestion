"""
Deterministic completeness check for Stage 0 extracted data.
No LLM calls — pure Python logic on confidence scores.
"""
from __future__ import annotations

import logging
import os

from models.extraction import CompletenessCheckResult, ExtractionConfidence, ExtractedClientData

logger = logging.getLogger(__name__)

MINIMUM_REQUIRED_FIELDS = [
    "client_name",
    "entity_type",
    "jurisdiction",
    "investment_amount",
    "source_of_funds",
    "beneficial_owners",
]

_DEFAULT_CONFIDENCE_THRESHOLD = 0.7


def _get_threshold() -> float:
    try:
        return float(os.getenv("CONFIDENCE_THRESHOLD", str(_DEFAULT_CONFIDENCE_THRESHOLD)))
    except ValueError:
        return _DEFAULT_CONFIDENCE_THRESHOLD


def _field_is_present(field_name: str, extracted: ExtractedClientData, threshold: float) -> bool:
    """Return True if the field is present and meets the confidence threshold."""
    if field_name == "beneficial_owners":
        owners = extracted.beneficial_owners
        if not owners:
            return False
        return any(o.confidence >= threshold and o.extracted_value for o in owners)

    conf: ExtractionConfidence | None = getattr(extracted, field_name, None)
    if conf is None:
        return False
    return bool(conf.extracted_value) and conf.confidence >= threshold


def check_completeness(extracted: ExtractedClientData) -> CompletenessCheckResult:
    """
    Evaluate extracted data against minimum required fields and confidence threshold.
    Returns a CompletenessCheckResult with a routing decision.
    """
    threshold = _get_threshold()
    present: list[str] = []
    missing: list[str] = []

    for field in MINIMUM_REQUIRED_FIELDS:
        if _field_is_present(field, extracted, threshold):
            present.append(field)
        else:
            missing.append(field)

    # Determine the worst confidence score for required fields
    min_conf_found = 1.0
    for field in MINIMUM_REQUIRED_FIELDS:
        if field == "beneficial_owners":
            owners = extracted.beneficial_owners
            if owners:
                min_conf_found = min(min_conf_found, max(o.confidence for o in owners))
            else:
                min_conf_found = 0.0
        else:
            conf = getattr(extracted, field, None)
            if conf is not None:
                min_conf_found = min(min_conf_found, conf.confidence)
            else:
                min_conf_found = 0.0

    is_complete = len(missing) == 0

    # Routing decision
    if is_complete and extracted.overall_extraction_confidence >= threshold:
        action = "proceed_to_compliance"
        reasoning = (
            f"All {len(present)} required fields extracted with confidence ≥ {threshold:.2f}. "
            f"Overall extraction confidence: {extracted.overall_extraction_confidence:.2f}. "
            "No human review needed — proceeding directly to compliance."
        )
    elif any(
        _missing_confidence_between(f, extracted, 0.3, threshold)
        for f in missing
    ):
        action = "ops_review_required"
        low_fields = [
            f for f in MINIMUM_REQUIRED_FIELDS
            if not _field_is_present(f, extracted, threshold)
        ]
        reasoning = (
            f"{len(low_fields)} required field(s) extracted but below confidence threshold "
            f"({threshold:.2f}): {', '.join(low_fields)}. "
            "Ops team review required to verify extracted values before proceeding."
        )
    else:
        action = "return_to_client"
        reasoning = (
            f"{len(missing)} required field(s) could not be reliably extracted: {', '.join(missing)}. "
            "Documents are insufficient — additional information must be requested from the client."
        )

    logger.info(
        "[CompletenessChecker] %s | action=%s | present=%d | missing=%d | overall_conf=%.2f",
        extracted.application_id,
        action,
        len(present),
        len(missing),
        extracted.overall_extraction_confidence,
    )

    return CompletenessCheckResult(
        application_id=extracted.application_id,
        is_complete=is_complete,
        required_fields_present=present,
        required_fields_missing=missing,
        recommended_action=action,
        reasoning=reasoning,
        minimum_required_fields=MINIMUM_REQUIRED_FIELDS,
        confidence_threshold_used=threshold,
    )


def _missing_confidence_between(
    field: str, extracted: ExtractedClientData, low: float, high: float
) -> bool:
    """True if field exists but confidence is in the [low, high) range (partial extraction)."""
    if field == "beneficial_owners":
        owners = extracted.beneficial_owners
        if not owners:
            return False
        return any(low <= o.confidence < high for o in owners)
    conf = getattr(extracted, field, None)
    if conf is None or not conf.extracted_value:
        return False
    return low <= conf.confidence < high
