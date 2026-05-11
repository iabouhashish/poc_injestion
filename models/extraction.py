"""
Agent 1 data models: document intake, LLM extraction, completeness validation.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


class DocumentInput(BaseModel):
    """A single document from the onboarding package."""
    filename: str
    document_type: str  # email, application_form, supporting_document, additional_info
    content: str
    file_path: str


class ExtractionConfidence(BaseModel):
    """A single extracted field with LLM-assigned confidence."""
    field_name: str
    extracted_value: Optional[str] = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    source_document: str
    reasoning: str


class ExtractedClientData(BaseModel):
    """
    Structured client data extracted by the LLM from unstructured onboarding documents.
    Each field carries a confidence score and provenance reference.
    """
    application_id: str

    client_name: ExtractionConfidence
    entity_type: ExtractionConfidence
    jurisdiction: ExtractionConfidence
    registered_address: ExtractionConfidence
    tax_id: ExtractionConfidence
    investment_amount: ExtractionConfidence
    investment_strategy: ExtractionConfidence
    risk_tolerance: ExtractionConfidence

    beneficial_owners: list[ExtractionConfidence] = Field(default_factory=list)
    source_of_funds: ExtractionConfidence
    source_of_wealth: ExtractionConfidence
    authorized_signatories: list[ExtractionConfidence] = Field(default_factory=list)

    documents_provided: list[str] = Field(default_factory=list)
    overall_extraction_confidence: float = Field(..., ge=0.0, le=1.0)
    extraction_warnings: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class CompletenessCheckResult(BaseModel):
    """
    Deterministic (non-LLM) assessment of whether extracted data meets minimum
    quality thresholds to proceed to compliance review.
    """
    application_id: str
    is_complete: bool
    required_fields_present: list[str]
    required_fields_missing: list[str]
    recommended_action: Literal[
        "proceed_to_compliance",
        "ops_review_required",
        "return_to_client",
    ]
    reasoning: str
    minimum_required_fields: list[str]
    confidence_threshold_used: float
