from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


ComplexityLevel = Literal["low", "medium", "high"]
OnboardingTrack = Literal["fast_track", "standard", "enhanced_due_diligence", "manual_escalation"]
MondayStatus = Literal[
    "New", "Processing", "Data Processed", "Ready to Onboard", "Pending Review", "Pending Client",
    "In Compliance", "Out Of Compliance", "Need Information",
    "Approved", "Rejected",
]
MondayPriority = Literal["Low", "Medium", "High", "Critical"]
StepOwner = Literal["operations", "compliance", "relationship_manager", "client"]
StepPriority = Literal["immediate", "standard", "deferred"]


class InvestmentProfile(BaseModel):
    strategy: str = Field(..., description="High-level investment strategy (e.g. 'Fixed Income and Real Assets')")
    amount: float = Field(..., description="Commitment amount in base currency units")
    currency: str = Field("USD", description="ISO 4217 currency code")
    special_requirements: list[str] = Field(
        default_factory=list,
        description="Non-standard requirements (ESG screens, reporting cadences, phased funding, etc.)",
    )


class OnboardingNextStep(BaseModel):
    step_number: int = Field(..., ge=1, description="Ordinal position in the execution sequence")
    action: str = Field(..., description="Specific, actionable task description")
    owner: StepOwner = Field(..., description="Team or party responsible for completing this step")
    priority: StepPriority = Field(..., description="Execution priority relative to other steps")
    depends_on: Optional[str] = Field(
        None,
        description="Description of the prerequisite that must be completed first (null if no dependency)",
    )


class MondayBoardFields(BaseModel):
    """Column values that will be written to the monday.com board item."""

    status: MondayStatus = Field(
        ...,
        description=(
            "Agent 2 compliance determination to set on the board. "
            "MUST be one of exactly three values — do not use any other status: "
            "Use 'In Compliance' if due diligence is complete and the client meets all compliance requirements. "
            "Use 'Out Of Compliance' if due diligence is complete but the client fails one or more compliance requirements. "
            "Use 'Need Information' if critical information is missing that prevents completing the compliance review. "
            "Do NOT write 'Approved', 'Rejected', 'Ready to Onboard', or any other status — those are set by other actors."
        ),
    )
    priority: MondayPriority = Field(..., description="Board priority column value")
    assigned_team: str = Field(..., description="Team assigned as primary owner on the board")
    tags: list[str] = Field(default_factory=list, description="Board tags for filtering and reporting")


class OnboardingSummaryMetadata(BaseModel):
    processed_at: str = Field(..., description="ISO-8601 timestamp")
    pipeline_stage: Literal["stage_2_onboarding_summary"] = "stage_2_onboarding_summary"
    model_used: str = Field(..., description="LiteLLM model string")
    risk_assessment_reference: str = Field(
        ...,
        description="client_id of the Stage 1 RiskAssessment this summary is grounded in",
    )


class OnboardingSummary(BaseModel):
    """
    Stage 2 output — actionable onboarding summary for the operations team.
    Must be grounded in the Stage 1 RiskAssessment. Complexity, next steps,
    and review track must logically derive from the risk context.
    """

    client_id: str
    client_name: str

    client_overview: str = Field(
        ...,
        description="2-3 sentence plain-language description of who this client is and why they are engaging Crestview",
    )
    investment_profile: InvestmentProfile

    complexity_level: ComplexityLevel = Field(
        ...,
        description="Operational complexity of onboarding this client",
    )
    complexity_reasoning: str = Field(
        ...,
        description="Explanation of why this complexity level was assigned, grounded in the risk assessment",
    )

    risk_summary: str = Field(
        ...,
        description=(
            "Plain-language risk summary for the ops team — no compliance jargon. "
            "Should convey what the compliance team found and what it means for onboarding."
        ),
    )

    next_steps: list[OnboardingNextStep] = Field(
        ...,
        min_length=3,
        description="Ordered action items for the onboarding team",
    )
    blockers: list[str] = Field(
        default_factory=list,
        description="Issues that will halt onboarding until resolved",
    )

    estimated_onboarding_track: OnboardingTrack = Field(
        ...,
        description="Expected onboarding timeline category, derived from risk assessment review track",
    )
    estimated_review_time: str = Field(
        ...,
        description="Human-readable estimate of calendar time to complete onboarding (e.g. '2-3 business days')",
    )

    monday_board_fields: MondayBoardFields

    metadata: OnboardingSummaryMetadata
