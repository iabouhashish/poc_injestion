from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class ScoreWithExplanation(BaseModel):
    score: int = Field(..., ge=0, le=100, description="Score from 0 (fundamentally flawed) to 100 (senior compliance officer would have no corrections)")
    explanation: str = Field(..., description="Specific justification for this score — must cite evidence from the output")


class RiskAssessmentEvaluation(BaseModel):
    """LLM-as-judge evaluation of a Stage 1 RiskAssessment."""

    reasoning_quality: ScoreWithExplanation = Field(
        ..., description="Is each risk dimension finding grounded in specific data from the application?",
    )
    reasoning_completeness: ScoreWithExplanation = Field(
        ..., description="Were all six risk dimensions addressed? Were obvious risk signals missed?",
    )
    risk_level_appropriateness: ScoreWithExplanation = Field(
        ..., description="Is the assigned overall risk level reasonable given the application data?",
    )
    compliance_flags_accuracy: ScoreWithExplanation = Field(
        ..., description="Are the compliance flags specific and actionable? Any false flags or missed flags?",
    )
    overall_assessment_quality: ScoreWithExplanation = Field(
        ..., description="Holistic quality of the risk assessment as a compliance artifact",
    )
    critical_issues: list[str] = Field(
        default_factory=list,
        description="Serious errors in the assessment — anything a compliance officer would consider unacceptable",
    )


class OnboardingSummaryEvaluation(BaseModel):
    """LLM-as-judge evaluation of a Stage 2 OnboardingSummary."""

    actionability: ScoreWithExplanation = Field(
        ..., description="Could an ops team member read this and know exactly what to do next?",
    )
    risk_grounding: ScoreWithExplanation = Field(
        ..., description="Is the summary clearly informed by the risk assessment? Do complexity, next steps, and track logically follow?",
    )
    next_steps_quality: ScoreWithExplanation = Field(
        ..., description="Are next steps specific, properly ordered, and assigned to the right owner?",
    )
    completeness: ScoreWithExplanation = Field(
        ..., description="Are there gaps — missing blockers, unaddressed client needs, or steps that should be there but aren't?",
    )
    overall_summary_quality: ScoreWithExplanation = Field(
        ..., description="Holistic quality of the onboarding summary as an operational artifact",
    )


class ExtractionEvaluation(BaseModel):
    """LLM-as-judge evaluation of Stage 0 document extraction quality."""

    extraction_accuracy: ScoreWithExplanation = Field(
        ...,
        description="Did the LLM correctly extract the key fields from the documents? Compare extracted values against ground truth.",
    )
    confidence_calibration: ScoreWithExplanation = Field(
        ...,
        description="Are the confidence scores well-calibrated? High confidence on correct extractions, low confidence on missing/incorrect ones.",
    )
    completeness_detection: ScoreWithExplanation = Field(
        ...,
        description="Did the completeness checker correctly identify missing or unreliable fields?",
    )


class ApplicationEvaluation(BaseModel):
    """Evaluation results for a single processed application."""

    client_id: str
    client_name: str
    risk_assessment_evaluation: RiskAssessmentEvaluation
    onboarding_summary_evaluation: OnboardingSummaryEvaluation
    extraction_evaluation: Optional[ExtractionEvaluation] = None
    evaluator_model: str
    evaluated_at: str
