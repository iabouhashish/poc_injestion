from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field

from models.client import ClientApplication
from models.risk import RiskAssessment
from models.onboarding import OnboardingSummary
from models.evaluation import ApplicationEvaluation
from models.extraction import ExtractedClientData, CompletenessCheckResult


class PipelineResultMetadata(BaseModel):
    total_processing_time_seconds: float
    pipeline_version: str
    stage0_latency_seconds: Optional[float] = None
    stage1_latency_seconds: float
    stage2_latency_seconds: float
    evaluation_latency_seconds: Optional[float] = None
    stage0_tokens: Optional[int] = None
    stage1_tokens: Optional[int] = None
    stage2_tokens: Optional[int] = None
    evaluator_tokens: Optional[int] = None
    tools_enabled: bool = False
    monday_item_id: Optional[str] = None
    monday_eval_item_id: Optional[str] = None
    extraction_confidence: Optional[float] = None
    review_action: Optional[str] = None
    ops_review_applied: bool = False


class PipelineResult(BaseModel):
    """Complete output for a single client application after all pipeline stages."""

    client_application: ClientApplication
    risk_assessment: RiskAssessment
    onboarding_summary: OnboardingSummary
    evaluation: Optional[ApplicationEvaluation] = None
    extracted_data: Optional[ExtractedClientData] = None
    completeness_result: Optional[CompletenessCheckResult] = None
    pipeline_metadata: PipelineResultMetadata


class PipelineMetrics(BaseModel):
    """Aggregate metrics computed across all processed applications."""

    total_applications_processed: int
    total_applications_failed: int

    risk_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="Count of applications per risk level: {low, medium, high, critical}",
    )
    review_track_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="Count per review track",
    )
    escalation_rate: float = Field(
        ...,
        description="Fraction routed to enhanced_due_diligence or manual_escalation",
    )

    average_risk_assessment_score: Optional[float] = Field(
        None,
        description="Mean overall_assessment_quality across evaluated applications",
    )
    average_summary_score: Optional[float] = Field(
        None,
        description="Mean overall_summary_quality across evaluated applications",
    )

    pydantic_validation_failures: int = 0
    json_parse_failures: int = 0

    compliance_flags_frequency: dict[str, int] = Field(
        default_factory=dict,
        description="Compliance flag text → occurrence count",
    )
    missing_information_frequency: dict[str, int] = Field(
        default_factory=dict,
        description="Missing info item text → occurrence count",
    )

    average_processing_time_seconds: float
    stage1_avg_latency_seconds: float
    stage2_avg_latency_seconds: float
    evaluation_avg_latency_seconds: Optional[float] = None

    total_estimated_tokens: int = 0
    applications_with_critical_issues: int = 0
