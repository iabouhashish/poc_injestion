"""Tests for the aggregate pipeline metrics computation in main.py."""
from __future__ import annotations

from unittest.mock import patch
import os

import pytest

# Guard: import main only after env is set (done in conftest.py)
from tests.conftest import (
    VALID_RISK_ASSESSMENT_DICT,
    VALID_ONBOARDING_SUMMARY_DICT,
    VALID_EVALUATION_DICT,
)


def _make_pipeline_result(
    app_id: str,
    client_name: str,
    risk_level: str = "low",
    review_track: str = "fast_track",
    compliance_flags: list[str] | None = None,
    missing_information: list[str] | None = None,
    s1_latency: float = 10.0,
    s2_latency: float = 8.0,
    eval_latency: float | None = None,
    s1_tokens: int = 500,
    s2_tokens: int = 400,
    eval_tokens: int = 300,
    ra_score: int = 4,
    os_score: int = 4,
    critical_issues: list[str] | None = None,
):
    """Build a minimal PipelineResult suitable for metrics computation."""
    from models.risk import RiskAssessment
    from models.onboarding import OnboardingSummary
    from models.evaluation import (
        ApplicationEvaluation,
        RiskAssessmentEvaluation,
        OnboardingSummaryEvaluation,
        ScoreWithExplanation,
    )
    from models.pipeline import PipelineResult, PipelineResultMetadata
    from models.client import ClientApplication

    app = ClientApplication(
        application_id=app_id,
        client_name=client_name,
        client_type="Corporate",
        requested_services="X",
        estimated_aum=1_000_000,
        submission_date="01/01/2026",
        status="New",
        description="Test description for metrics.",
    )

    ra_data = {
        **VALID_RISK_ASSESSMENT_DICT,
        "client_id": app_id,
        "client_name": client_name,
        "overall_risk_level": risk_level,
        "recommended_review_track": review_track,
        "compliance_flags": compliance_flags or [],
        "missing_information": missing_information or [],
    }
    ra = RiskAssessment.model_validate(ra_data)

    os_data = {
        **VALID_ONBOARDING_SUMMARY_DICT,
        "client_id": app_id,
        "client_name": client_name,
    }
    os_ = OnboardingSummary.model_validate(os_data)

    score = lambda s, expl: ScoreWithExplanation(score=s, explanation=expl)
    ev = ApplicationEvaluation(
        client_id=app_id,
        client_name=client_name,
        risk_assessment_evaluation=RiskAssessmentEvaluation(
            reasoning_quality=score(ra_score, "X"),
            reasoning_completeness=score(ra_score, "X"),
            risk_level_appropriateness=score(ra_score, "X"),
            compliance_flags_accuracy=score(ra_score, "X"),
            overall_assessment_quality=score(ra_score, "Overall."),
            critical_issues=critical_issues or [],
        ),
        onboarding_summary_evaluation=OnboardingSummaryEvaluation(
            actionability=score(os_score, "X"),
            risk_grounding=score(os_score, "X"),
            next_steps_quality=score(os_score, "X"),
            completeness=score(os_score, "X"),
            overall_summary_quality=score(os_score, "Overall."),
        ),
        evaluator_model="anthropic/claude-sonnet-4-6",
        evaluated_at="2026-01-01T00:00:00+00:00",
    )

    return PipelineResult(
        client_application=app,
        risk_assessment=ra,
        onboarding_summary=os_,
        evaluation=ev,
        pipeline_metadata=PipelineResultMetadata(
            total_processing_time_seconds=s1_latency + s2_latency + (eval_latency or 0),
            pipeline_version="0.1.0",
            stage1_latency_seconds=s1_latency,
            stage2_latency_seconds=s2_latency,
            evaluation_latency_seconds=eval_latency,
            stage1_tokens=s1_tokens,
            stage2_tokens=s2_tokens,
            evaluator_tokens=eval_tokens,
            tools_enabled=False,
        ),
    )


@pytest.fixture()
def sample_results():
    return [
        _make_pipeline_result("APP-001", "Client A", risk_level="low", review_track="fast_track",
                              compliance_flags=[], ra_score=5, os_score=5),
        _make_pipeline_result("APP-002", "Client B", risk_level="medium", review_track="standard",
                              compliance_flags=["Incomplete docs"], ra_score=4, os_score=4),
        _make_pipeline_result("APP-003", "Client C", risk_level="high", review_track="enhanced_due_diligence",
                              compliance_flags=["Offshore structure", "Incomplete docs"], ra_score=3, os_score=3),
        _make_pipeline_result("APP-004", "Client D", risk_level="critical", review_track="manual_escalation",
                              compliance_flags=["Sanctions match", "Offshore structure"],
                              missing_information=["UBO docs", "Source of funds"],
                              ra_score=2, os_score=2, critical_issues=["Risk level too low"]),
    ]


class TestComputeMetrics:
    def test_total_processed(self, sample_results):
        from main import compute_metrics
        m = compute_metrics(sample_results, failed_count=0, pv_failures=[], jf_failures=[])
        assert m.total_applications_processed == 4

    def test_failed_count(self, sample_results):
        from main import compute_metrics
        m = compute_metrics(sample_results, failed_count=2, pv_failures=[], jf_failures=[])
        assert m.total_applications_failed == 2

    def test_risk_distribution_counts(self, sample_results):
        from main import compute_metrics
        m = compute_metrics(sample_results, failed_count=0, pv_failures=[], jf_failures=[])
        assert m.risk_distribution["low"] == 1
        assert m.risk_distribution["medium"] == 1
        assert m.risk_distribution["high"] == 1
        assert m.risk_distribution["critical"] == 1

    def test_escalation_rate_two_of_four(self, sample_results):
        from main import compute_metrics
        m = compute_metrics(sample_results, failed_count=0, pv_failures=[], jf_failures=[])
        # enhanced_due_diligence + manual_escalation = 2 / 4
        assert m.escalation_rate == pytest.approx(0.5, abs=0.001)

    def test_review_track_distribution(self, sample_results):
        from main import compute_metrics
        m = compute_metrics(sample_results, failed_count=0, pv_failures=[], jf_failures=[])
        assert m.review_track_distribution["fast_track"] == 1
        assert m.review_track_distribution["standard"] == 1
        assert m.review_track_distribution["enhanced_due_diligence"] == 1
        assert m.review_track_distribution["manual_escalation"] == 1

    def test_average_risk_assessment_score(self, sample_results):
        from main import compute_metrics
        m = compute_metrics(sample_results, failed_count=0, pv_failures=[], jf_failures=[])
        # Scores: 5, 4, 3, 2 → mean = 3.5
        assert m.average_risk_assessment_score == pytest.approx(3.5, abs=0.01)

    def test_average_summary_score(self, sample_results):
        from main import compute_metrics
        m = compute_metrics(sample_results, failed_count=0, pv_failures=[], jf_failures=[])
        assert m.average_summary_score == pytest.approx(3.5, abs=0.01)

    def test_pydantic_validation_failures(self, sample_results):
        from main import compute_metrics
        m = compute_metrics(sample_results, failed_count=0, pv_failures=["APP-001"], jf_failures=[])
        assert m.pydantic_validation_failures == 1

    def test_json_parse_failures(self, sample_results):
        from main import compute_metrics
        m = compute_metrics(sample_results, failed_count=0, pv_failures=[], jf_failures=["APP-002", "APP-003"])
        assert m.json_parse_failures == 2

    def test_compliance_flag_frequency(self, sample_results):
        from main import compute_metrics
        m = compute_metrics(sample_results, failed_count=0, pv_failures=[], jf_failures=[])
        # "Incomplete docs" appears twice, "Offshore structure" appears twice
        freq = m.compliance_flags_frequency
        assert freq.get("Incomplete docs", 0) == 2
        assert freq.get("Offshore structure", 0) == 2
        assert freq.get("Sanctions match", 0) == 1

    def test_missing_information_frequency(self, sample_results):
        from main import compute_metrics
        m = compute_metrics(sample_results, failed_count=0, pv_failures=[], jf_failures=[])
        assert m.missing_information_frequency.get("UBO docs", 0) == 1
        assert m.missing_information_frequency.get("Source of funds", 0) == 1

    def test_applications_with_critical_issues(self, sample_results):
        from main import compute_metrics
        m = compute_metrics(sample_results, failed_count=0, pv_failures=[], jf_failures=[])
        # Only APP-004 has critical_issues
        assert m.applications_with_critical_issues == 1

    def test_stage1_avg_latency(self, sample_results):
        from main import compute_metrics
        m = compute_metrics(sample_results, failed_count=0, pv_failures=[], jf_failures=[])
        assert m.stage1_avg_latency_seconds == pytest.approx(10.0, abs=0.01)

    def test_stage2_avg_latency(self, sample_results):
        from main import compute_metrics
        m = compute_metrics(sample_results, failed_count=0, pv_failures=[], jf_failures=[])
        assert m.stage2_avg_latency_seconds == pytest.approx(8.0, abs=0.01)

    def test_total_tokens_summed(self, sample_results):
        from main import compute_metrics
        m = compute_metrics(sample_results, failed_count=0, pv_failures=[], jf_failures=[])
        # 4 results × (500 + 400 + 300) = 4800
        assert m.total_estimated_tokens == 4800

    def test_zero_results_handled(self):
        from main import compute_metrics
        # Empty results list should not crash — all averages should be 0
        m = compute_metrics([], failed_count=3, pv_failures=[], jf_failures=[])
        assert m.total_applications_processed == 0
        assert m.escalation_rate == 0.0
        assert m.stage1_avg_latency_seconds == 0.0

    def test_no_evaluation_results_returns_none_scores(self):
        from main import compute_metrics
        from models.pipeline import PipelineResult, PipelineResultMetadata
        from models.client import ClientApplication
        from models.risk import RiskAssessment
        from models.onboarding import OnboardingSummary

        # Build result without evaluation
        app = ClientApplication(
            application_id="APP-X", client_name="X", client_type="Corporate",
            requested_services="X", estimated_aum=1_000_000,
            submission_date="01/01/2026", status="New", description="X",
        )
        ra = RiskAssessment.model_validate({**VALID_RISK_ASSESSMENT_DICT, "client_id": "APP-X", "client_name": "X"})
        os_ = OnboardingSummary.model_validate({**VALID_ONBOARDING_SUMMARY_DICT, "client_id": "APP-X", "client_name": "X"})
        result = PipelineResult(
            client_application=app,
            risk_assessment=ra,
            onboarding_summary=os_,
            evaluation=None,  # no eval
            pipeline_metadata=PipelineResultMetadata(
                total_processing_time_seconds=20.0,
                pipeline_version="0.1.0",
                stage1_latency_seconds=10.0,
                stage2_latency_seconds=8.0,
            ),
        )

        m = compute_metrics([result], failed_count=0, pv_failures=[], jf_failures=[])
        assert m.average_risk_assessment_score is None
        assert m.average_summary_score is None
