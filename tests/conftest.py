"""
Shared pytest fixtures for the Crestview onboarding pipeline test suite.

Fixtures are split into three layers:
  1. Raw data dicts — simple Python dicts used to build models
  2. Pydantic model instances — pre-validated objects ready for pipeline use
  3. Mock LLM response factories — callable helpers that produce fake litellm responses
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

# Make sure tests run from the project root without needing editable install
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-key")
os.environ.setdefault("LLM_MODEL", "anthropic/claude-sonnet-4-6")
os.environ.setdefault("MONDAY_API_KEY", "")
os.environ.setdefault("MONDAY_BOARD_ID", "")
os.environ.setdefault("MAX_RETRIES", "3")
os.environ.setdefault("RETRY_BACKOFF_BASE", "2")
os.environ.setdefault("PIPELINE_VERSION", "0.1.0")

from models.client import ClientApplication
from models.risk import RiskAssessment, RiskDimensionResult, RiskAssessmentMetadata
from models.onboarding import (
    OnboardingSummary,
    OnboardingSummaryMetadata,
    InvestmentProfile,
    OnboardingNextStep,
    MondayBoardFields,
)
from models.evaluation import (
    ApplicationEvaluation,
    RiskAssessmentEvaluation,
    OnboardingSummaryEvaluation,
    ScoreWithExplanation,
)


# ── Raw data helpers ───────────────────────────────────────────────────────────

VALID_RISK_DIMENSIONS = [
    {
        "dimension": "entity_structure_complexity",
        "risk_contribution": "none",
        "findings": "Single public pension fund. No subsidiaries or holding layers.",
        "reasoning": "Standard institutional structure with no complexity warranting elevated scrutiny.",
    },
    {
        "dimension": "jurisdiction_risk",
        "risk_contribution": "none",
        "findings": "US-domiciled. No offshore exposure.",
        "reasoning": "FATF member jurisdiction. No cross-border complexity.",
    },
    {
        "dimension": "pep_exposure",
        "risk_contribution": "none",
        "findings": "No PEPs identified among trustees or authorized signatories.",
        "reasoning": "Standard public pension governance with no political exposure.",
    },
    {
        "dimension": "source_of_funds",
        "risk_contribution": "none",
        "findings": "Source is documented public employee payroll contributions.",
        "reasoning": "Transparent, statutory source. No ambiguity.",
    },
    {
        "dimension": "adverse_media_sanctions",
        "risk_contribution": "none",
        "findings": "No sanctions hits or adverse media flags.",
        "reasoning": "Clean screening result.",
    },
    {
        "dimension": "application_completeness",
        "risk_contribution": "none",
        "findings": "Full documentation: board resolution, IPS, signatory list, IC approval.",
        "reasoning": "Application is complete. All standard documents present.",
    },
]

VALID_RISK_ASSESSMENT_DICT = {
    "client_id": "APP-2026-0301",
    "client_name": "Greenfield State Pension Fund",
    "entity_type": "Institutional",
    "overall_risk_level": "low",
    "risk_dimensions": VALID_RISK_DIMENSIONS,
    "overall_reasoning": "Clean low-risk institutional onboarding. All six dimensions score none.",
    "compliance_flags": [],
    "recommended_review_track": "fast_track",
    "recommended_review_track_reasoning": "All risk dimensions score none. Full documentation received.",
    "missing_information": [],
    "metadata": {
        "processed_at": "2026-01-06T10:00:00+00:00",
        "pipeline_stage": "stage_1_risk_assessment",
        "model_used": "anthropic/claude-sonnet-4-6",
    },
}

VALID_ONBOARDING_SUMMARY_DICT = {
    "client_id": "APP-2026-0301",
    "client_name": "Greenfield State Pension Fund",
    "client_overview": "A US public pension fund seeking to allocate $750M across equity and fixed income.",
    "investment_profile": {
        "strategy": "Equity and Fixed Income Management",
        "amount": 750_000_000,
        "currency": "USD",
        "special_requirements": [],
    },
    "complexity_level": "low",
    "complexity_reasoning": "Single entity, complete docs, no compliance concerns.",
    "risk_summary": "No issues found. Standard fast-track onboarding applies.",
    "next_steps": [
        {
            "step_number": 1,
            "action": "Open custody account",
            "owner": "operations",
            "priority": "immediate",
            "depends_on": None,
        },
        {
            "step_number": 2,
            "action": "Complete KYC review",
            "owner": "compliance",
            "priority": "standard",
            "depends_on": "Step 1 complete",
        },
        {
            "step_number": 3,
            "action": "Notify relationship manager of approval",
            "owner": "relationship_manager",
            "priority": "deferred",
            "depends_on": "Step 2 complete",
        },
    ],
    "blockers": [],
    "estimated_onboarding_track": "fast_track",
    "estimated_review_time": "2-3 business days",
    "monday_board_fields": {
        "status": "Pending Review",
        "priority": "Low",
        "assigned_team": "Institutional",
        "tags": ["pension_fund", "fast_track"],
    },
    "metadata": {
        "processed_at": "2026-01-06T10:01:00+00:00",
        "pipeline_stage": "stage_2_onboarding_summary",
        "model_used": "anthropic/claude-sonnet-4-6",
        "risk_assessment_reference": "APP-2026-0301",
    },
}

VALID_EVALUATION_DICT = {
    "risk_assessment_evaluation": {
        "reasoning_quality": {"score": 88, "explanation": "Every finding cites specific application data."},
        "reasoning_completeness": {"score": 92, "explanation": "All six dimensions addressed with no gaps."},
        "risk_level_appropriateness": {"score": 85, "explanation": "Low risk is correct for this clean institutional client."},
        "compliance_flags_accuracy": {"score": 90, "explanation": "No false flags. No missed flags for this low-risk client."},
        "overall_assessment_quality": {"score": 87, "explanation": "Exemplary assessment — audit-ready."},
        "critical_issues": [],
    },
    "onboarding_summary_evaluation": {
        "actionability": {"score": 75, "explanation": "Clear steps but could specify exact system names."},
        "risk_grounding": {"score": 84, "explanation": "Complexity and track directly reflect the risk assessment."},
        "next_steps_quality": {"score": 78, "explanation": "Well-ordered with clear owners."},
        "completeness": {"score": 82, "explanation": "No gaps for this straightforward case."},
        "overall_summary_quality": {"score": 79, "explanation": "Strong operational summary."},
    },
}


# ── Pydantic model fixtures ────────────────────────────────────────────────────

@pytest.fixture()
def low_risk_application() -> ClientApplication:
    return ClientApplication(
        application_id="APP-2026-0301",
        client_name="Greenfield State Pension Fund",
        client_type="Institutional",
        requested_services="Equity and Fixed Income Management",
        estimated_aum=750_000_000,
        submission_date="01/06/2026",
        status="New",
        description=(
            "State pension fund seeking to allocate $750M across domestic equity and "
            "investment-grade fixed income strategies. Standard institutional documentation "
            "package received including board resolution, investment policy statement, and "
            "authorized signatory list."
        ),
    )


@pytest.fixture()
def high_risk_application() -> ClientApplication:
    return ClientApplication(
        application_id="APP-2026-0303",
        client_name="Apex Global Holdings Ltd",
        client_type="Corporate",
        requested_services="Multi-Asset Management",
        estimated_aum=500_000_000,
        submission_date="01/08/2026",
        status="New",
        description=(
            "Corporate entity registered in an offshore jurisdiction. Ownership structure "
            "involves three layers of intermediate holding companies. Beneficial ownership "
            "is difficult to determine — nominee directors appear to be from a corporate "
            "services firm. Source of funds: 'diversified business interests' with no detail. "
            "Client is requesting expedited processing and pushing back on documentation requests."
        ),
    )


@pytest.fixture()
def risk_assessment(low_risk_application) -> RiskAssessment:
    return RiskAssessment.model_validate(VALID_RISK_ASSESSMENT_DICT)


@pytest.fixture()
def high_risk_assessment() -> RiskAssessment:
    dims = [
        {
            "dimension": "entity_structure_complexity",
            "risk_contribution": "high",
            "findings": "Three layers of intermediate holding companies across multiple jurisdictions.",
            "reasoning": "Layered structure obscures beneficial ownership — significant AML risk.",
        },
        {
            "dimension": "jurisdiction_risk",
            "risk_contribution": "high",
            "findings": "Registered in an offshore jurisdiction with no domicile specified.",
            "reasoning": "Offshore registration without disclosure is a high-risk indicator.",
        },
        {
            "dimension": "pep_exposure",
            "risk_contribution": "medium",
            "findings": "Directors appear to be nominee — actual principals unknown.",
            "reasoning": "Cannot assess PEP status without identifying real beneficial owners.",
        },
        {
            "dimension": "source_of_funds",
            "risk_contribution": "high",
            "findings": "'Diversified business interests' with no supporting documentation.",
            "reasoning": "Vague, undocumented source of funds is a critical AML red flag.",
        },
        {
            "dimension": "adverse_media_sanctions",
            "risk_contribution": "medium",
            "findings": "Possible OFAC SDN name similarity flagged in pre-screening.",
            "reasoning": "Unresolved possible match requires enhanced review before proceeding.",
        },
        {
            "dimension": "application_completeness",
            "risk_contribution": "high",
            "findings": "Missing: UBO disclosure, source of funds documentation, principal identities.",
            "reasoning": "Refusal to provide standard documentation is itself a risk signal.",
        },
    ]
    return RiskAssessment.model_validate(
        {
            "client_id": "APP-2026-0303",
            "client_name": "Apex Global Holdings Ltd",
            "entity_type": "Corporate",
            "overall_risk_level": "critical",
            "risk_dimensions": dims,
            "overall_reasoning": "Multiple critical red flags across all six dimensions.",
            "compliance_flags": [
                "Nominee directors — UBO unverifiable",
                "Source of funds undocumented",
                "Possible OFAC SDN match unresolved",
                "Offshore registration with opaque structure",
            ],
            "recommended_review_track": "manual_escalation",
            "recommended_review_track_reasoning": "Critical risk across four dimensions. Manual compliance escalation required.",
            "missing_information": [
                "Ultimate beneficial owner identities and documentation",
                "Source of funds supporting documentation",
                "Certified copies of constitutional documents",
            ],
            "metadata": {
                "processed_at": "2026-01-08T10:00:00+00:00",
                "pipeline_stage": "stage_1_risk_assessment",
                "model_used": "anthropic/claude-sonnet-4-6",
            },
        }
    )


@pytest.fixture()
def onboarding_summary(risk_assessment) -> OnboardingSummary:
    return OnboardingSummary.model_validate(VALID_ONBOARDING_SUMMARY_DICT)


@pytest.fixture()
def application_evaluation() -> ApplicationEvaluation:
    ra_eval = RiskAssessmentEvaluation.model_validate(VALID_EVALUATION_DICT["risk_assessment_evaluation"])
    os_eval = OnboardingSummaryEvaluation.model_validate(VALID_EVALUATION_DICT["onboarding_summary_evaluation"])
    return ApplicationEvaluation(
        client_id="APP-2026-0301",
        client_name="Greenfield State Pension Fund",
        risk_assessment_evaluation=ra_eval,
        onboarding_summary_evaluation=os_eval,
        evaluator_model="anthropic/claude-sonnet-4-6",
        evaluated_at="2026-01-06T10:02:00+00:00",
    )


# ── Mock LLM response factory ──────────────────────────────────────────────────

def make_llm_response(content: str, tokens: int = 500):
    """Return a fake litellm response object with the given content."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = None
    msg.model_dump.return_value = {"role": "assistant", "content": content, "tool_calls": None}

    choice = MagicMock()
    choice.message = msg

    usage = MagicMock()
    usage.total_tokens = tokens

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def make_tool_call_response(tool_name: str, tool_args: dict, call_id: str = "tc_001"):
    """Return a fake litellm response that requests a single tool call."""
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = tool_name
    tc.function.arguments = json.dumps(tool_args)

    msg = MagicMock()
    msg.content = None
    msg.tool_calls = [tc]
    msg.model_dump.return_value = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}],
    }

    choice = MagicMock()
    choice.message = msg

    usage = MagicMock()
    usage.total_tokens = 200

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


@pytest.fixture()
def valid_stage1_response() -> MagicMock:
    return make_llm_response(json.dumps(VALID_RISK_ASSESSMENT_DICT))


@pytest.fixture()
def valid_stage2_response() -> MagicMock:
    return make_llm_response(json.dumps(VALID_ONBOARDING_SUMMARY_DICT))


@pytest.fixture()
def valid_evaluator_response() -> MagicMock:
    return make_llm_response(json.dumps(VALID_EVALUATION_DICT))
