"""Tests for the LLM-as-judge evaluator."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from models.evaluation import ApplicationEvaluation
from tests.conftest import VALID_EVALUATION_DICT, make_llm_response


class TestExtractJsonEvaluator:
    """extract_json lives in src.utils and is shared by pipeline and evaluator."""

    def setup_method(self):
        from src.utils import extract_json
        self.extract = extract_json

    def test_bare_json(self):
        raw = '{"a": 1}'
        assert self.extract(raw) == raw

    def test_fenced_json(self):
        raw = '```json\n{"a": 1}\n```'
        assert self.extract(raw) == '{"a": 1}'

    def test_prose_surrounding_json(self):
        raw = 'Here is my evaluation:\n{"risk_assessment_evaluation": {}}\nEnd.'
        result = self.extract(raw)
        assert "{" in result


class TestBuildEvalSchema:
    def test_schema_is_valid_json(self):
        from src.evaluator import _build_eval_schema
        schema_str = _build_eval_schema()
        schema = json.loads(schema_str)
        assert "properties" in schema
        assert "risk_assessment_evaluation" in schema["properties"]
        assert "onboarding_summary_evaluation" in schema["properties"]

    def test_schema_has_required_fields(self):
        from src.evaluator import _build_eval_schema
        schema = json.loads(_build_eval_schema())
        assert "required" in schema
        assert set(schema["required"]) == {"risk_assessment_evaluation", "onboarding_summary_evaluation"}

    def test_score_bounds_in_schema(self):
        from src.evaluator import _build_eval_schema
        schema_str = _build_eval_schema()
        assert '"minimum": 0' in schema_str
        assert '"maximum": 100' in schema_str


class TestEvaluateApplication:
    @patch("litellm.completion")
    def test_returns_application_evaluation(
        self, mock_completion, low_risk_application, risk_assessment, onboarding_summary
    ):
        mock_completion.return_value = make_llm_response(json.dumps(VALID_EVALUATION_DICT))
        from src.evaluator import evaluate_application
        result, latency, tokens = evaluate_application(
            low_risk_application, risk_assessment, onboarding_summary
        )
        assert isinstance(result, ApplicationEvaluation)
        assert result.client_id == "APP-2026-0301"
        assert result.client_name == "Greenfield State Pension Fund"

    @patch("litellm.completion")
    def test_scores_extracted_correctly(
        self, mock_completion, low_risk_application, risk_assessment, onboarding_summary
    ):
        mock_completion.return_value = make_llm_response(json.dumps(VALID_EVALUATION_DICT))
        from src.evaluator import evaluate_application
        result, _, _ = evaluate_application(low_risk_application, risk_assessment, onboarding_summary)
        assert result.risk_assessment_evaluation.overall_assessment_quality.score == 87
        assert result.onboarding_summary_evaluation.overall_summary_quality.score == 79

    @patch("litellm.completion")
    def test_critical_issues_list_empty_for_good_output(
        self, mock_completion, low_risk_application, risk_assessment, onboarding_summary
    ):
        mock_completion.return_value = make_llm_response(json.dumps(VALID_EVALUATION_DICT))
        from src.evaluator import evaluate_application
        result, _, _ = evaluate_application(low_risk_application, risk_assessment, onboarding_summary)
        assert result.risk_assessment_evaluation.critical_issues == []

    @patch("litellm.completion")
    def test_critical_issues_captured(
        self, mock_completion, low_risk_application, risk_assessment, onboarding_summary
    ):
        data = {
            **VALID_EVALUATION_DICT,
            "risk_assessment_evaluation": {
                **VALID_EVALUATION_DICT["risk_assessment_evaluation"],
                "critical_issues": ["Risk level is understated", "PEP dimension missing"],
            },
        }
        mock_completion.return_value = make_llm_response(json.dumps(data))
        from src.evaluator import evaluate_application
        result, _, _ = evaluate_application(low_risk_application, risk_assessment, onboarding_summary)
        assert len(result.risk_assessment_evaluation.critical_issues) == 2

    @patch("litellm.completion")
    def test_returns_positive_latency_and_tokens(
        self, mock_completion, low_risk_application, risk_assessment, onboarding_summary
    ):
        mock_completion.return_value = make_llm_response(json.dumps(VALID_EVALUATION_DICT), tokens=750)
        from src.evaluator import evaluate_application
        _, latency, tokens = evaluate_application(low_risk_application, risk_assessment, onboarding_summary)
        assert latency >= 0
        assert tokens == 750

    @patch("litellm.completion")
    def test_evaluated_at_timestamp_set(
        self, mock_completion, low_risk_application, risk_assessment, onboarding_summary
    ):
        mock_completion.return_value = make_llm_response(json.dumps(VALID_EVALUATION_DICT))
        from src.evaluator import evaluate_application
        result, _, _ = evaluate_application(low_risk_application, risk_assessment, onboarding_summary)
        assert result.evaluated_at != ""
        assert "T" in result.evaluated_at  # ISO 8601 format

    @patch("litellm.completion")
    def test_evaluator_model_name_recorded(
        self, mock_completion, low_risk_application, risk_assessment, onboarding_summary
    ):
        mock_completion.return_value = make_llm_response(json.dumps(VALID_EVALUATION_DICT))
        from src.evaluator import evaluate_application
        result, _, _ = evaluate_application(low_risk_application, risk_assessment, onboarding_summary)
        assert result.evaluator_model != ""

    @patch("litellm.completion")
    def test_user_prompt_includes_all_three_artifacts(
        self, mock_completion, low_risk_application, risk_assessment, onboarding_summary
    ):
        """The evaluator prompt must include application, risk assessment, and summary."""
        mock_completion.return_value = make_llm_response(json.dumps(VALID_EVALUATION_DICT))
        from src.evaluator import evaluate_application
        evaluate_application(low_risk_application, risk_assessment, onboarding_summary)
        user_msg = mock_completion.call_args[1]["messages"][1]["content"]
        assert "APP-2026-0301" in user_msg  # application id
        assert "fast_track" in user_msg      # from risk_assessment
        assert "fast_track" in user_msg      # from onboarding_summary

    @patch("time.sleep")
    @patch("litellm.completion")
    def test_retries_on_transient_error(
        self, mock_completion, mock_sleep, low_risk_application, risk_assessment, onboarding_summary
    ):
        mock_completion.side_effect = [
            RuntimeError("Network error"),
            make_llm_response(json.dumps(VALID_EVALUATION_DICT)),
        ]
        import src.evaluator as ev
        ev._MAX_RETRIES = 3
        ev._BACKOFF_BASE = 1
        from src.evaluator import evaluate_application
        result, _, _ = evaluate_application(low_risk_application, risk_assessment, onboarding_summary)
        assert isinstance(result, ApplicationEvaluation)
        assert mock_sleep.called

    @patch("time.sleep")
    @patch("litellm.completion")
    def test_raises_after_all_retries(
        self, mock_completion, mock_sleep, low_risk_application, risk_assessment, onboarding_summary
    ):
        mock_completion.side_effect = RuntimeError("Always fails")
        import src.evaluator as ev
        original = ev._MAX_RETRIES
        ev._MAX_RETRIES = 2
        try:
            with pytest.raises(RuntimeError):
                from src.evaluator import evaluate_application
                evaluate_application(low_risk_application, risk_assessment, onboarding_summary)
        finally:
            ev._MAX_RETRIES = original
