"""Tests for all Pydantic models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from models.client import ClientApplication
from models.risk import RiskAssessment, RiskDimensionResult
from models.onboarding import (
    OnboardingSummary,
    OnboardingNextStep,
    MondayBoardFields,
    InvestmentProfile,
)
from models.evaluation import (
    ApplicationEvaluation,
    RiskAssessmentEvaluation,
    OnboardingSummaryEvaluation,
    ScoreWithExplanation,
)
from models.pipeline import PipelineMetrics, PipelineResultMetadata
from tests.conftest import VALID_RISK_ASSESSMENT_DICT, VALID_ONBOARDING_SUMMARY_DICT, VALID_EVALUATION_DICT


# ── ClientApplication ──────────────────────────────────────────────────────────

class TestClientApplication:
    def test_basic_construction(self, low_risk_application):
        app = low_risk_application
        assert app.application_id == "APP-2026-0301"
        assert app.client_name == "Greenfield State Pension Fund"
        assert app.estimated_aum == 750_000_000.0

    def test_optional_fields_default_empty(self, low_risk_application):
        assert low_risk_application.jurisdiction is None
        assert low_risk_application.beneficial_owners == []
        assert low_risk_application.documents_provided == []
        assert low_risk_application.adverse_media_indicators == []
        assert low_risk_application.additional_notes is None

    def test_aum_plain_integer(self):
        app = ClientApplication(
            application_id="X", client_name="X", client_type="individual",
            requested_services="X", estimated_aum=1_000_000,
            submission_date="2026-01-01", status="New", description="X",
        )
        assert app.estimated_aum == 1_000_000.0

    def test_aum_string_with_commas(self):
        app = ClientApplication(
            application_id="X", client_name="X", client_type="individual",
            requested_services="X", estimated_aum="1,500,000",
            submission_date="2026-01-01", status="New", description="X",
        )
        assert app.estimated_aum == 1_500_000.0

    def test_aum_dollar_sign(self):
        app = ClientApplication(
            application_id="X", client_name="X", client_type="individual",
            requested_services="X", estimated_aum="$5,000,000",
            submission_date="2026-01-01", status="New", description="X",
        )
        assert app.estimated_aum == 5_000_000.0

    def test_aum_M_suffix(self):
        app = ClientApplication(
            application_id="X", client_name="X", client_type="individual",
            requested_services="X", estimated_aum="750M",
            submission_date="2026-01-01", status="New", description="X",
        )
        assert app.estimated_aum == 750_000_000.0

    def test_aum_B_suffix(self):
        app = ClientApplication(
            application_id="X", client_name="X", client_type="individual",
            requested_services="X", estimated_aum="2B",
            submission_date="2026-01-01", status="New", description="X",
        )
        assert app.estimated_aum == 2_000_000_000.0

    def test_aum_K_suffix(self):
        app = ClientApplication(
            application_id="X", client_name="X", client_type="individual",
            requested_services="X", estimated_aum="500K",
            submission_date="2026-01-01", status="New", description="X",
        )
        assert app.estimated_aum == 500_000.0

    def test_aum_lowercase_suffix(self):
        app = ClientApplication(
            application_id="X", client_name="X", client_type="individual",
            requested_services="X", estimated_aum="1.5m",
            submission_date="2026-01-01", status="New", description="X",
        )
        assert app.estimated_aum == 1_500_000.0

    def test_enrichment_fields_can_be_set(self):
        app = ClientApplication(
            application_id="X", client_name="X", client_type="individual",
            requested_services="X", estimated_aum=1_000_000,
            submission_date="2026-01-01", status="New", description="X",
            jurisdiction="United States",
            beneficial_owners=["Jane Doe"],
            documents_provided=["passport.pdf"],
            adverse_media_indicators=["No hits"],
        )
        assert app.jurisdiction == "United States"
        assert app.beneficial_owners == ["Jane Doe"]


# ── RiskDimensionResult ────────────────────────────────────────────────────────

class TestRiskDimensionResult:
    def test_valid_construction(self):
        dim = RiskDimensionResult(
            dimension="jurisdiction_risk",
            risk_contribution="high",
            findings="Offshore registration in Cayman Islands.",
            reasoning="FATF grey list jurisdiction.",
        )
        assert dim.risk_contribution == "high"

    def test_invalid_risk_contribution(self):
        with pytest.raises(ValidationError):
            RiskDimensionResult(
                dimension="jurisdiction_risk",
                risk_contribution="extreme",  # not a valid literal
                findings="X",
                reasoning="X",
            )

    @pytest.mark.parametrize("level", ["none", "low", "medium", "high"])
    def test_all_valid_risk_contributions(self, level):
        dim = RiskDimensionResult(
            dimension="test", risk_contribution=level, findings="X", reasoning="X"
        )
        assert dim.risk_contribution == level


# ── RiskAssessment ─────────────────────────────────────────────────────────────

class TestRiskAssessment:
    def test_valid_construction(self):
        ra = RiskAssessment.model_validate(VALID_RISK_ASSESSMENT_DICT)
        assert ra.overall_risk_level == "low"
        assert ra.recommended_review_track == "fast_track"
        assert len(ra.risk_dimensions) == 6

    def test_requires_six_dimensions(self):
        data = {**VALID_RISK_ASSESSMENT_DICT, "risk_dimensions": VALID_RISK_ASSESSMENT_DICT["risk_dimensions"][:5]}
        with pytest.raises(ValidationError):
            RiskAssessment.model_validate(data)

    @pytest.mark.parametrize("level", ["low", "medium", "high", "critical"])
    def test_all_risk_levels_valid(self, level):
        data = {**VALID_RISK_ASSESSMENT_DICT, "overall_risk_level": level}
        ra = RiskAssessment.model_validate(data)
        assert ra.overall_risk_level == level

    def test_invalid_risk_level(self):
        data = {**VALID_RISK_ASSESSMENT_DICT, "overall_risk_level": "extreme"}
        with pytest.raises(ValidationError):
            RiskAssessment.model_validate(data)

    @pytest.mark.parametrize("track", ["fast_track", "standard", "enhanced_due_diligence", "manual_escalation"])
    def test_all_review_tracks_valid(self, track):
        data = {**VALID_RISK_ASSESSMENT_DICT, "recommended_review_track": track}
        ra = RiskAssessment.model_validate(data)
        assert ra.recommended_review_track == track

    def test_compliance_flags_default_empty(self):
        data = {k: v for k, v in VALID_RISK_ASSESSMENT_DICT.items() if k != "compliance_flags"}
        ra = RiskAssessment.model_validate(data)
        assert ra.compliance_flags == []

    def test_schema_generation(self):
        schema = RiskAssessment.model_json_schema()
        assert "properties" in schema
        assert "risk_dimensions" in schema["properties"]
        assert "overall_risk_level" in schema["properties"]

    def test_metadata_pipeline_stage_literal(self):
        ra = RiskAssessment.model_validate(VALID_RISK_ASSESSMENT_DICT)
        assert ra.metadata.pipeline_stage == "stage_1_risk_assessment"

    def test_invalid_metadata_pipeline_stage(self):
        data = {
            **VALID_RISK_ASSESSMENT_DICT,
            "metadata": {
                **VALID_RISK_ASSESSMENT_DICT["metadata"],
                "pipeline_stage": "wrong_stage",
            },
        }
        with pytest.raises(ValidationError):
            RiskAssessment.model_validate(data)


# ── OnboardingSummary ──────────────────────────────────────────────────────────

class TestOnboardingSummary:
    def test_valid_construction(self):
        os_ = OnboardingSummary.model_validate(VALID_ONBOARDING_SUMMARY_DICT)
        assert os_.complexity_level == "low"
        assert os_.estimated_onboarding_track == "fast_track"
        assert len(os_.next_steps) == 3

    def test_requires_at_least_three_next_steps(self):
        data = {**VALID_ONBOARDING_SUMMARY_DICT, "next_steps": VALID_ONBOARDING_SUMMARY_DICT["next_steps"][:2]}
        with pytest.raises(ValidationError):
            OnboardingSummary.model_validate(data)

    @pytest.mark.parametrize("level", ["low", "medium", "high"])
    def test_all_complexity_levels(self, level):
        data = {**VALID_ONBOARDING_SUMMARY_DICT, "complexity_level": level}
        os_ = OnboardingSummary.model_validate(data)
        assert os_.complexity_level == level

    @pytest.mark.parametrize("track", ["fast_track", "standard", "enhanced_due_diligence", "manual_escalation"])
    def test_all_onboarding_tracks(self, track):
        data = {**VALID_ONBOARDING_SUMMARY_DICT, "estimated_onboarding_track": track}
        os_ = OnboardingSummary.model_validate(data)
        assert os_.estimated_onboarding_track == track

    def test_blockers_default_empty(self):
        data = {k: v for k, v in VALID_ONBOARDING_SUMMARY_DICT.items() if k != "blockers"}
        os_ = OnboardingSummary.model_validate(data)
        assert os_.blockers == []

    def test_schema_generation(self):
        schema = OnboardingSummary.model_json_schema()
        assert "next_steps" in schema["properties"]
        assert "monday_board_fields" in schema["properties"]


class TestMondayBoardFields:
    @pytest.mark.parametrize(
        "status",
        ["New", "Pending Review", "In Compliance", "Pending Client", "Ready to Onboard"],
    )
    def test_all_valid_statuses(self, status):
        f = MondayBoardFields(status=status, priority="Low", assigned_team="Ops", tags=[])
        assert f.status == status

    @pytest.mark.parametrize("priority", ["Low", "Medium", "High", "Critical"])
    def test_all_valid_priorities(self, priority):
        f = MondayBoardFields(status="New", priority=priority, assigned_team="Ops", tags=[])
        assert f.priority == priority

    def test_invalid_status(self):
        with pytest.raises(ValidationError):
            MondayBoardFields(status="Approved", priority="Low", assigned_team="Ops", tags=[])


class TestOnboardingNextStep:
    def test_valid_step(self):
        step = OnboardingNextStep(
            step_number=1, action="Open account", owner="operations",
            priority="immediate", depends_on=None,
        )
        assert step.owner == "operations"
        assert step.depends_on is None

    @pytest.mark.parametrize("owner", ["operations", "compliance", "relationship_manager", "client"])
    def test_all_valid_owners(self, owner):
        step = OnboardingNextStep(step_number=1, action="X", owner=owner, priority="standard")
        assert step.owner == owner

    def test_step_number_must_be_positive(self):
        with pytest.raises(ValidationError):
            OnboardingNextStep(step_number=0, action="X", owner="operations", priority="standard")


# ── Evaluation models ──────────────────────────────────────────────────────────

class TestScoreWithExplanation:
    @pytest.mark.parametrize("score", [0, 25, 50, 75, 100])
    def test_valid_scores(self, score):
        s = ScoreWithExplanation(score=score, explanation="Reason.")
        assert s.score == score

    def test_score_below_minimum(self):
        with pytest.raises(ValidationError):
            ScoreWithExplanation(score=-1, explanation="X")

    def test_score_above_maximum(self):
        with pytest.raises(ValidationError):
            ScoreWithExplanation(score=101, explanation="X")


class TestRiskAssessmentEvaluation:
    def test_valid_construction(self):
        ra_eval = RiskAssessmentEvaluation.model_validate(
            VALID_EVALUATION_DICT["risk_assessment_evaluation"]
        )
        assert ra_eval.overall_assessment_quality.score == 87
        assert ra_eval.critical_issues == []

    def test_critical_issues_list(self):
        data = {
            **VALID_EVALUATION_DICT["risk_assessment_evaluation"],
            "critical_issues": ["Risk level is too low for stated facts"],
        }
        ra_eval = RiskAssessmentEvaluation.model_validate(data)
        assert len(ra_eval.critical_issues) == 1


class TestOnboardingSummaryEvaluation:
    def test_valid_construction(self):
        os_eval = OnboardingSummaryEvaluation.model_validate(
            VALID_EVALUATION_DICT["onboarding_summary_evaluation"]
        )
        assert os_eval.overall_summary_quality.score == 79


# ── PipelineMetrics ────────────────────────────────────────────────────────────

class TestPipelineMetrics:
    def test_valid_construction(self):
        m = PipelineMetrics(
            total_applications_processed=15,
            total_applications_failed=0,
            escalation_rate=0.2,
            average_processing_time_seconds=45.0,
            stage1_avg_latency_seconds=20.0,
            stage2_avg_latency_seconds=18.0,
        )
        assert m.total_applications_processed == 15
        assert m.pydantic_validation_failures == 0

    def test_optional_eval_fields_default_none(self):
        m = PipelineMetrics(
            total_applications_processed=5,
            total_applications_failed=0,
            escalation_rate=0.0,
            average_processing_time_seconds=10.0,
            stage1_avg_latency_seconds=5.0,
            stage2_avg_latency_seconds=4.0,
        )
        assert m.average_risk_assessment_score is None
        assert m.evaluation_avg_latency_seconds is None
