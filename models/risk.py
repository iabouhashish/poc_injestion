from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


RiskContribution = Literal["none", "low", "medium", "high"]
RiskLevel = Literal["low", "medium", "high", "critical"]
ReviewTrack = Literal["fast_track", "standard", "enhanced_due_diligence", "manual_escalation"]


class RiskDimensionResult(BaseModel):
    """Assessment result for a single risk dimension."""

    dimension: str = Field(
        ...,
        description="Name of the risk dimension being assessed",
    )
    risk_contribution: RiskContribution = Field(
        ...,
        description="This dimension's contribution to overall risk",
    )
    findings: str = Field(
        ...,
        description=(
            "Specific observations drawn directly from the application data. "
            "Must cite actual fields and values — not generic statements."
        ),
    )
    reasoning: str = Field(
        ...,
        description=(
            "Why this finding constitutes the stated risk contribution. "
            "Defensible under regulatory audit."
        ),
    )


class RiskAssessmentMetadata(BaseModel):
    processed_at: str = Field(..., description="ISO-8601 timestamp of when this assessment was produced")
    pipeline_stage: Literal["stage_1_risk_assessment"] = "stage_1_risk_assessment"
    model_used: str = Field(..., description="LiteLLM model string used to generate this assessment")


class RiskAssessment(BaseModel):
    """
    Stage 1 output — structured risk assessment produced by the LLM.
    Every field must be grounded in the client application data and explainable
    to a compliance officer under audit.
    """

    client_id: str = Field(..., description="application_id from the source ClientApplication")
    client_name: str = Field(..., description="Full legal name of the client")
    entity_type: str = Field(..., description="Entity classification (mirrors client_type)")

    overall_risk_level: RiskLevel = Field(
        ...,
        description="Synthesized risk level across all dimensions",
    )
    risk_dimensions: list[RiskDimensionResult] = Field(
        ...,
        min_length=6,
        description=(
            "Results for all six mandatory risk dimensions: "
            "(1) entity_structure_complexity, "
            "(2) jurisdiction_risk, "
            "(3) pep_exposure, "
            "(4) source_of_funds, "
            "(5) adverse_media_sanctions, "
            "(6) application_completeness"
        ),
    )
    overall_reasoning: str = Field(
        ...,
        description=(
            "Synthesis paragraph for the compliance officer explaining why this overall risk level "
            "was assigned. Must reference the most significant risk dimensions."
        ),
    )
    compliance_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Specific, actionable flags raised by this assessment. "
            "Each flag should name the issue concisely (e.g. 'Nominee directors identified — UBO unverifiable')."
        ),
    )
    recommended_review_track: ReviewTrack = Field(
        ...,
        description="Recommended review workflow based on risk profile",
    )
    recommended_review_track_reasoning: str = Field(
        ...,
        description="Explanation of why this review track was recommended",
    )
    missing_information: list[str] = Field(
        default_factory=list,
        description="Information gaps identified in the application that should be resolved before onboarding",
    )
    metadata: RiskAssessmentMetadata
