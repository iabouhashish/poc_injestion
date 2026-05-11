from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PipelineRunRecord(SQLModel, table=True):
    __tablename__ = "pipeline_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(unique=True, index=True)
    started_at: datetime
    completed_at: Optional[datetime] = None
    total_applications: int = 0
    successful: int = 0
    failed: int = 0
    escalation_rate: float = 0.0
    avg_risk_assessment_score: float = 0.0
    avg_summary_score: float = 0.0
    total_processing_time_seconds: float = 0.0
    estimated_token_cost: Optional[float] = None
    pipeline_version: str
    model_used: str
    tools_enabled: bool = False
    notes: Optional[str] = None
    phase: int = Field(default=1)  # 1 = Phase 1 complete, 2 = Phase 2 complete


class ApplicationResultRecord(SQLModel, table=True):
    __tablename__ = "application_results"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    application_id: str = Field(index=True)
    client_name: str
    client_type: str
    estimated_aum: Optional[float] = None
    processing_status: str  # completed, failed, skipped
    error_message: Optional[str] = None
    risk_level: Optional[str] = None
    review_track: Optional[str] = None
    complexity_level: Optional[str] = None
    compliance_flags: Optional[str] = None  # JSON serialized list
    missing_information: Optional[str] = None  # JSON serialized list
    risk_assessment_json: Optional[str] = None  # full Stage 1 output
    onboarding_summary_json: Optional[str] = None  # full Stage 2 output
    stage0_latency_seconds: Optional[float] = None
    stage1_latency_seconds: Optional[float] = None
    stage2_latency_seconds: Optional[float] = None
    eval_latency_seconds: Optional[float] = None
    extraction_confidence: Optional[float] = None
    fields_missing: Optional[str] = None  # JSON list
    review_action: Optional[str] = None   # proceed_to_compliance | ops_review_required | return_to_client
    ops_review_corrections: Optional[str] = None  # JSON dict of corrected fields
    monday_item_id: Optional[str] = None
    monday_eval_item_id: Optional[str] = None
    # Two-phase fields
    extraction_json: Optional[str] = None     # serialized ExtractedClientData
    completeness_json: Optional[str] = None   # serialized CompletenessCheckResult
    phase_completed: Optional[int] = None     # 1 after Agent 1, 2 after Agent 2
    processed_at: datetime = Field(default_factory=_now)


class EvaluationScoreRecord(SQLModel, table=True):
    __tablename__ = "evaluation_scores"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    application_id: str = Field(index=True)
    reasoning_quality: int
    reasoning_completeness: int
    risk_level_appropriateness: int
    compliance_flags_accuracy: int
    overall_risk_assessment_score: int
    actionability: int
    risk_grounding: int
    next_steps_quality: int
    completeness: int
    overall_summary_score: int
    critical_issues: str  # JSON serialized list
    evaluator_reasoning: str  # JSON serialized dict of explanations
    # Agent 1 extraction evaluation (optional — populated when Stage 0 ran)
    extraction_accuracy: Optional[int] = None
    confidence_calibration: Optional[int] = None
    completeness_detection: Optional[int] = None
    extraction_eval_reasoning: Optional[str] = None
    evaluated_at: datetime = Field(default_factory=_now)


class LLMCallRecord(SQLModel, table=True):
    __tablename__ = "llm_calls"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    application_id: Optional[str] = None
    stage: str  # risk_assessment, onboarding_summary, evaluation
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_seconds: float = 0.0
    estimated_cost: float = 0.0
    tool_calls_made: Optional[str] = None  # JSON serialized list
    success: bool = True
    error: Optional[str] = None
    raw_response: Optional[str] = None  # only populated if RAW_LLM_RESPONSES=true
    called_at: datetime = Field(default_factory=_now)
