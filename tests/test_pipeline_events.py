"""Tests verifying PipelineEvent emissions from run_agent1, run_phase1, process_application_phase2, and run_phase2."""
from __future__ import annotations

import threading
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.events import PipelineEvent
from tests.conftest import VALID_RISK_ASSESSMENT_DICT, VALID_ONBOARDING_SUMMARY_DICT


# ── Helpers ────────────────────────────────────────────────────────────────────

def _collect_events(fn, *args, **kwargs) -> list[PipelineEvent]:
    events: list[PipelineEvent] = []
    fn(*args, on_event=events.append, **kwargs)
    return events


def _make_application(app_id: str = "APP-TEST-01", name: str = "Test Client"):
    from models.client import ClientApplication
    return ClientApplication(
        application_id=app_id, client_name=name, client_type="Corporate",
        requested_services="Equity", estimated_aum=1_000_000,
        submission_date="01/01/2026", status="New", description="Test.",
    )


def _fake_extracted():
    m = MagicMock()
    m.overall_extraction_confidence = 0.9
    m.client_name.extracted_value = "Test Client"
    m.entity_type.extracted_value = "Corporate"
    m.jurisdiction.extracted_value = "US"
    m.source_of_funds.extracted_value = "Business operations"
    return m


def _fake_completeness(action: str = "proceed_to_compliance"):
    m = MagicMock()
    m.recommended_action = action
    m.confidence_threshold_used = 0.75
    m.required_fields_missing = []
    m.reasoning = ""
    return m


# ── TestRunAgent1Events ────────────────────────────────────────────────────────

class TestRunAgent1Events:
    @pytest.fixture(autouse=True)
    def _base_patches(self):
        self.fake_extracted = _fake_extracted()
        self.fake_completeness = _fake_completeness()
        with patch("src.run_phase1.client_hub"), \
             patch("src.run_phase1.monday"), \
             patch("src.run_phase1.outlook"), \
             patch("src.run_phase1.check_completeness", return_value=self.fake_completeness):
            yield

    def test_emits_stage_started(self):
        from src.run_phase1 import run_agent1
        app = _make_application()
        with patch("src.run_phase1.load_documents", return_value=[MagicMock()]), \
             patch("src.run_phase1.run_stage0", return_value=(self.fake_extracted, 1.0, 100)):
            events = _collect_events(
                run_agent1, app, None, [], [],
            )
        types = [e.event for e in events]
        assert "stage_started" in types
        assert events[0].stage == "stage0"

    def test_emits_stage_completed_on_success(self):
        from src.run_phase1 import run_agent1
        app = _make_application()
        with patch("src.run_phase1.load_documents", return_value=[MagicMock()]), \
             patch("src.run_phase1.run_stage0", return_value=(self.fake_extracted, 1.0, 100)):
            events = _collect_events(run_agent1, app, None, [], [])
        completed = [e for e in events if e.event == "stage_completed"]
        assert len(completed) == 1
        assert completed[0].data["confidence"] == pytest.approx(0.9, abs=0.01)
        assert completed[0].data["action"] == "proceed_to_compliance"

    def test_emits_stage_completed_no_documents(self):
        from src.run_phase1 import run_agent1
        app = _make_application()
        with patch("src.run_phase1.load_documents", return_value=[]):
            events = _collect_events(run_agent1, app, None, [], [])
        completed = [e for e in events if e.event == "stage_completed"]
        assert len(completed) == 1
        assert completed[0].data["action"] == "no_documents"
        assert completed[0].data["confidence"] == 0.0

    def test_emits_error_when_stage0_fails(self):
        from src.run_phase1 import run_agent1
        app = _make_application()
        with patch("src.run_phase1.load_documents", return_value=[MagicMock()]), \
             patch("src.run_phase1.run_stage0", side_effect=RuntimeError("LLM failure")):
            events = _collect_events(run_agent1, app, None, [], [])
        error_events = [e for e in events if e.event == "error"]
        assert len(error_events) == 1
        assert error_events[0].stage == "pipeline"

    def test_no_events_without_on_event(self):
        """Calling run_agent1 without on_event should not raise."""
        from src.run_phase1 import run_agent1
        app = _make_application()
        with patch("src.run_phase1.load_documents", return_value=[MagicMock()]), \
             patch("src.run_phase1.run_stage0", return_value=(self.fake_extracted, 1.0, 100)):
            result = run_agent1(app, None, [], [])  # no on_event — must not raise
        assert result[0] is self.fake_extracted


# ── TestRunPhase1Events ────────────────────────────────────────────────────────

class TestRunPhase1Events:
    def test_emits_run_complete(self):
        from src.run_phase1 import run_phase1
        app = _make_application()
        with patch("src.run_phase1.create_all_tables"), \
             patch("src.run_phase1.create_run_directory", return_value=Path("/tmp/test")), \
             patch("src.run_phase1.load_applications", return_value=[app]), \
             patch("src.run_phase1.get_session"), \
             patch("src.run_phase1.save_pipeline_run"), \
             patch("src.run_phase1.update_pipeline_run"), \
             patch("src.run_phase1.load_documents", return_value=[]), \
             patch("src.run_phase1._save_phase1_to_db"), \
             patch("src.run_phase1.write_phase1_files"), \
             patch("src.run_phase1.update_latest_symlink"):
            events = _collect_events(run_phase1)
        run_complete = [e for e in events if e.event == "run_complete"]
        assert len(run_complete) == 1
        assert run_complete[0].application_id == "pipeline"
        assert "applications_processed" in run_complete[0].data

    def test_stop_event_prevents_second_app(self):
        from src.run_phase1 import run_phase1
        app1 = _make_application("APP-001", "Client A")
        app2 = _make_application("APP-002", "Client B")
        stop = threading.Event()
        processed_ids: list[str] = []

        def fake_stage0(**kwargs):
            processed_ids.append(kwargs.get("application_id"))
            stop.set()
            raise RuntimeError("stop")

        with patch("src.run_phase1.create_all_tables"), \
             patch("src.run_phase1.create_run_directory", return_value=Path("/tmp/test")), \
             patch("src.run_phase1.load_applications", return_value=[app1, app2]), \
             patch("src.run_phase1.get_session"), \
             patch("src.run_phase1.save_pipeline_run"), \
             patch("src.run_phase1.update_pipeline_run"), \
             patch("src.run_phase1.load_documents", return_value=[MagicMock()]), \
             patch("src.run_phase1.run_stage0", side_effect=fake_stage0), \
             patch("src.run_phase1._save_phase1_to_db"), \
             patch("src.run_phase1.write_phase1_files"), \
             patch("src.run_phase1.update_latest_symlink"):
            run_phase1(max_applications=2, stop_event=stop)

        assert "APP-002" not in processed_ids


# ── TestProcessApplicationPhase2Events ────────────────────────────────────────

class TestProcessApplicationPhase2Events:
    @pytest.fixture(autouse=True)
    def _base_patches(self):
        from models.risk import RiskAssessment
        from models.onboarding import OnboardingSummary

        self.risk = RiskAssessment.model_validate(VALID_RISK_ASSESSMENT_DICT)
        self.onboarding = OnboardingSummary.model_validate(VALID_ONBOARDING_SUMMARY_DICT)

        with patch("src.run_phase2.monday"), \
             patch("src.run_phase2.monday_eval"), \
             patch("src.run_phase2.compliance_one") as mock_comp, \
             patch("src.run_phase2.salesforce"), \
             patch("src.run_phase2.outlook"), \
             patch("src.run_phase2.RUN_EVALUATIONS", False), \
             patch("src.run_phase2.ENABLE_ROUTING", False):
            mock_comp.submit_screening.return_value = {
                "sanctions_check": {"result": "no_match", "matched_list": []}
            }
            yield

    def _run(self, app=None, **extra_patches):
        from src.run_phase2 import process_application_phase2
        if app is None:
            app = _make_application()
        with patch("src.run_phase2.run_stage1", return_value=(self.risk, 1.0, 100)), \
             patch("src.run_phase2.run_stage2", return_value=(self.onboarding, 1.0, 100)):
            for k, v in extra_patches.items():
                pass  # extra patches applied in caller
            return _collect_events(
                process_application_phase2, app, None, None, None, "run-1", [], [],
            )

    def test_emits_stage1_started_and_completed(self):
        events = self._run()
        types_stages = [(e.event, e.stage) for e in events]
        assert ("stage_started", "stage1") in types_stages
        assert ("stage_completed", "stage1") in types_stages

    def test_stage1_completed_has_risk_level(self):
        events = self._run()
        completed = next(e for e in events if e.event == "stage_completed" and e.stage == "stage1")
        assert "risk_level" in completed.data
        assert "review_track" in completed.data
        assert "flags" in completed.data

    def test_emits_stage2_started_and_completed(self):
        events = self._run()
        types_stages = [(e.event, e.stage) for e in events]
        assert ("stage_started", "stage2") in types_stages
        assert ("stage_completed", "stage2") in types_stages

    def test_stage2_completed_has_complexity_and_track(self):
        events = self._run()
        completed = next(e for e in events if e.event == "stage_completed" and e.stage == "stage2")
        assert "complexity" in completed.data
        assert "track" in completed.data

    def test_emits_error_on_stage1_failure(self):
        from src.run_phase2 import process_application_phase2
        app = _make_application()
        with patch("src.run_phase2.run_stage1", side_effect=RuntimeError("LLM fail")):
            events = _collect_events(
                process_application_phase2, app, None, None, None, "run-1", [], [],
            )
        error_events = [e for e in events if e.event == "error"]
        assert len(error_events) == 1

    def test_emits_error_on_stage2_failure(self):
        from src.run_phase2 import process_application_phase2
        app = _make_application()
        with patch("src.run_phase2.run_stage1", return_value=(self.risk, 1.0, 100)), \
             patch("src.run_phase2.run_stage2", side_effect=RuntimeError("LLM fail")):
            events = _collect_events(
                process_application_phase2, app, None, None, None, "run-1", [], [],
            )
        error_events = [e for e in events if e.event == "error"]
        assert len(error_events) == 1

    def test_no_exception_without_on_event(self):
        from src.run_phase2 import process_application_phase2
        app = _make_application()
        with patch("src.run_phase2.run_stage1", return_value=(self.risk, 1.0, 100)), \
             patch("src.run_phase2.run_stage2", return_value=(self.onboarding, 1.0, 100)):
            result = process_application_phase2(app, None, None, None, "run-1", [], [])
        assert result is not None

    def test_emits_eval_events_when_enabled(self):
        from src.run_phase2 import process_application_phase2
        from models.evaluation import (
            ApplicationEvaluation, RiskAssessmentEvaluation, OnboardingSummaryEvaluation,
            ScoreWithExplanation,
        )
        score = lambda s: ScoreWithExplanation(score=s, explanation="ok")
        eval_obj = ApplicationEvaluation(
            client_id="APP-TEST-01", client_name="Test Client",
            risk_assessment_evaluation=RiskAssessmentEvaluation(
                reasoning_quality=score(4), reasoning_completeness=score(4),
                risk_level_appropriateness=score(4), compliance_flags_accuracy=score(4),
                overall_assessment_quality=score(4), critical_issues=[],
            ),
            onboarding_summary_evaluation=OnboardingSummaryEvaluation(
                actionability=score(4), risk_grounding=score(4), next_steps_quality=score(4),
                completeness=score(4), overall_summary_quality=score(4),
            ),
            evaluator_model="anthropic/claude-sonnet-4-6",
            evaluated_at="2026-01-01T00:00:00+00:00",
        )
        app = _make_application()
        mock_mon_eval = MagicMock()
        mock_mon_eval.post_evaluation_result.return_value = "EVAL-001"
        with patch("src.run_phase2.RUN_EVALUATIONS", True), \
             patch("src.run_phase2.ENABLE_ROUTING", False), \
             patch("src.run_phase2.run_stage1", return_value=(self.risk, 1.0, 100)), \
             patch("src.run_phase2.run_stage2", return_value=(self.onboarding, 1.0, 100)), \
             patch("src.run_phase2.evaluate_application", return_value=(eval_obj, 1.0, 100)), \
             patch("src.run_phase2.monday_eval", mock_mon_eval):
            events = _collect_events(
                process_application_phase2, app, None, None, None, "run-1", [], [],
            )
        eval_events = [e for e in events if e.event == "eval_score"]
        assert len(eval_events) == 1
        assert "ra_overall" in eval_events[0].data


# ── TestReviewerBriefEvents ───────────────────────────────────────────────────

class TestReviewerBriefEvents:
    """Verify reviewer_brief event is emitted on ESCALATE and suppressed on APPROVE."""

    @pytest.fixture(autouse=True)
    def _base_patches(self):
        from models.risk import RiskAssessment
        from models.onboarding import OnboardingSummary

        self.risk = RiskAssessment.model_validate(VALID_RISK_ASSESSMENT_DICT)
        self.onboarding = OnboardingSummary.model_validate(VALID_ONBOARDING_SUMMARY_DICT)

        with patch("src.run_phase2.monday"), \
             patch("src.run_phase2.monday_eval"), \
             patch("src.run_phase2.compliance_one") as mock_comp, \
             patch("src.run_phase2.salesforce"), \
             patch("src.run_phase2.outlook"), \
             patch("src.run_phase2.RUN_EVALUATIONS", False), \
             patch("src.run_phase2.ENABLE_ROUTING", True):
            mock_comp.submit_screening.return_value = {
                "sanctions_check": {"result": "no_match", "matched_list": []}
            }
            yield

    def _make_routing(self, decision: str):
        from src.orchestrator import RoutingDecision
        return RoutingDecision(
            decision=decision,
            confidence=0.95,
            rationale="Test rationale",
            reviewer_team="compliance_officer" if decision != "REJECT" else "rejected",
        )

    def test_reviewer_brief_event_not_emitted_on_approve(self):
        from src.run_phase2 import process_application_phase2
        app = _make_application()
        routing = self._make_routing("APPROVE")
        with patch("src.run_phase2.run_stage1", return_value=(self.risk, 1.0, 100)), \
             patch("src.run_phase2.run_stage2", return_value=(self.onboarding, 1.0, 100)), \
             patch("src.orchestrator.route_decision", return_value=routing):
            events = _collect_events(
                process_application_phase2, app, None, None, None, "run-1", [], [],
            )
        brief_events = [e for e in events if e.event == "reviewer_brief"]
        assert len(brief_events) == 0

    def test_reviewer_brief_event_emitted_on_escalate(self):
        from src.run_phase2 import process_application_phase2
        from src.reviewer_brief import ReviewerBrief
        app = _make_application()
        routing = self._make_routing("ESCALATE")
        fake_brief = ReviewerBrief(
            executive_summary="Critical risk — escalated for human review.",
            primary_risk_factors=[
                "Nominee directors with unverifiable UBO",
                "Source of funds undocumented",
                "Possible OFAC SDN match unresolved",
            ],
            verification_checklist=[
                "Confirm UBO via government-issued ID",
            ],
            suggested_next_steps=[
                "Assign to senior compliance for EDD",
            ],
            regulatory_notes="FinCEN CDD Rule 31 CFR 1010.230 requires UBO identification.",
        )
        with patch("src.run_phase2.run_stage1", return_value=(self.risk, 1.0, 100)), \
             patch("src.run_phase2.run_stage2", return_value=(self.onboarding, 1.0, 100)), \
             patch("src.orchestrator.route_decision", return_value=routing), \
             patch("src.reviewer_brief.generate_reviewer_brief", return_value=fake_brief):
            events = _collect_events(
                process_application_phase2, app, None, None, None, "run-1", [], [],
            )
        brief_events = [e for e in events if e.event == "reviewer_brief"]
        assert len(brief_events) == 1
        assert "executive_summary" in brief_events[0].data
        assert "primary_risk_factors" in brief_events[0].data


# ── TestRunPhase2Events ────────────────────────────────────────────────────────

class TestRunPhase2Events:
    def test_emits_error_and_run_complete_when_no_records(self):
        from src.run_phase2 import run_phase2
        with patch("src.run_phase2.create_all_tables"), \
             patch("src.run_phase2.create_run_directory", return_value=Path("/tmp/test")), \
             patch("src.run_phase2.get_session"), \
             patch("src.run_phase2.get_unprocessed_phase1_records", return_value=[]):
            events = _collect_events(run_phase2)
        event_types = [e.event for e in events]
        assert "error" in event_types
        assert "run_complete" in event_types
        run_complete = next(e for e in events if e.event == "run_complete")
        assert run_complete.data["applications_processed"] == 0

    def test_emits_run_complete_at_end(self):
        from src.run_phase2 import run_phase2
        from models.risk import RiskAssessment
        from models.onboarding import OnboardingSummary

        risk = RiskAssessment.model_validate(VALID_RISK_ASSESSMENT_DICT)
        onboarding = OnboardingSummary.model_validate(VALID_ONBOARDING_SUMMARY_DICT)

        record = MagicMock()
        record.application_id = "APP-TEST-01"
        record.monday_item_id = None
        record.extraction_json = None
        record.completeness_json = None
        record.run_id = "run-prev"

        app = _make_application()

        # monday mock with empty api_key/board_id so monday_configured=False
        mock_monday = MagicMock()
        mock_monday.api_key = ""
        mock_monday.board_id = ""

        _phase2_patches = [
            ("src.run_phase2.create_all_tables", {}),
            ("src.run_phase2.create_run_directory", {"return_value": Path("/tmp/test")}),
            ("src.run_phase2.get_session", {}),
            ("src.run_phase2.get_unprocessed_phase1_records", {"return_value": [record]}),
            ("src.run_phase2.save_pipeline_run", {}),
            ("src.run_phase2.update_pipeline_run", {}),
            ("src.run_phase2.load_applications", {"return_value": [app]}),
            ("src.run_phase2.monday_eval", {}),
            ("src.run_phase2.salesforce", {}),
            ("src.run_phase2.outlook", {}),
            ("src.run_phase2.run_stage1", {"return_value": (risk, 1.0, 100)}),
            ("src.run_phase2.run_stage2", {"return_value": (onboarding, 1.0, 100)}),
            ("src.run_phase2.RUN_EVALUATIONS", {"new": False}),
            ("src.run_phase2.ENABLE_ROUTING", {"new": False}),
            ("src.run_phase2._update_phase2_in_db", {}),
            ("src.run_phase2.write_application_files", {}),
            ("src.run_phase2.write_run_summary", {}),
            ("src.run_phase2.update_latest_symlink", {}),
            ("src.run_phase2.print_metrics_table", {}),
        ]

        with ExitStack() as stack:
            mocks = {}
            for target, kwargs in _phase2_patches:
                if "new" in kwargs:
                    mocks[target] = stack.enter_context(patch(target, kwargs["new"]))
                else:
                    mocks[target] = stack.enter_context(patch(target, **kwargs))

            stack.enter_context(patch("src.run_phase2.monday", mock_monday))
            mock_comp = stack.enter_context(patch("src.run_phase2.compliance_one"))
            mock_comp.submit_screening.return_value = {
                "sanctions_check": {"result": "no_match", "matched_list": []}
            }
            mock_metrics = stack.enter_context(patch("src.run_phase2.compute_metrics"))
            mock_metrics.return_value = MagicMock(
                total_applications_processed=1, total_applications_failed=0,
                escalation_rate=0.0, average_risk_assessment_score=4.0,
                average_summary_score=4.0, total_estimated_tokens=100,
            )
            events = _collect_events(run_phase2)

        run_complete = [e for e in events if e.event == "run_complete"]
        assert len(run_complete) == 1
        assert run_complete[0].data["applications_processed"] == 1
