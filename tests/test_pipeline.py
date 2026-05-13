"""Tests for Stage 1 and Stage 2 pipeline functions."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch, call

import pytest

from models.risk import RiskAssessment
from models.onboarding import OnboardingSummary
from tests.conftest import (
    VALID_RISK_ASSESSMENT_DICT,
    VALID_ONBOARDING_SUMMARY_DICT,
    make_llm_response,
    make_tool_call_response,
)


# ── _extract_json ──────────────────────────────────────────────────────────────

class TestExtractJson:
    def setup_method(self):
        from src.utils import extract_json
        self.extract = extract_json

    def test_bare_json_object(self):
        raw = '{"key": "value"}'
        assert self.extract(raw) == raw

    def test_strips_json_fenced_block(self):
        raw = '```json\n{"key": "value"}\n```'
        result = self.extract(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_fenced_block(self):
        raw = '```\n{"key": "value"}\n```'
        result = self.extract(raw)
        assert result == '{"key": "value"}'

    def test_extracts_json_from_surrounding_prose(self):
        raw = 'Here is the result:\n{"risk_level": "low"}\nEnd of output.'
        result = self.extract(raw)
        parsed = json.loads(result)
        assert parsed["risk_level"] == "low"

    def test_returns_raw_when_no_json_found(self):
        raw = "no json here at all"
        assert self.extract(raw) == raw

    def test_handles_nested_json(self):
        obj = {"a": {"b": {"c": 1}}}
        raw = json.dumps(obj)
        result = self.extract(raw)
        assert json.loads(result) == obj


# ── run_stage1 ─────────────────────────────────────────────────────────────────

class TestRunStage1:
    @patch("litellm.completion")
    def test_returns_risk_assessment(self, mock_completion, low_risk_application):
        mock_completion.return_value = make_llm_response(json.dumps(VALID_RISK_ASSESSMENT_DICT))
        from src.pipeline import run_stage1
        ra, latency, tokens = run_stage1(low_risk_application)
        assert isinstance(ra, RiskAssessment)
        assert ra.overall_risk_level == "low"
        assert ra.client_id == "APP-2026-0301"

    @patch("litellm.completion")
    def test_returns_positive_latency_and_tokens(self, mock_completion, low_risk_application):
        mock_completion.return_value = make_llm_response(json.dumps(VALID_RISK_ASSESSMENT_DICT), tokens=800)
        from src.pipeline import run_stage1
        ra, latency, tokens = run_stage1(low_risk_application)
        assert latency >= 0
        assert tokens == 800

    @patch("litellm.completion")
    def test_metadata_injected_automatically(self, mock_completion, low_risk_application):
        """Stage 1 must inject processed_at and model_used even if LLM omits them."""
        data = {k: v for k, v in VALID_RISK_ASSESSMENT_DICT.items() if k != "metadata"}
        mock_completion.return_value = make_llm_response(json.dumps(data))
        from src.pipeline import run_stage1
        ra, _, _ = run_stage1(low_risk_application)
        assert ra.metadata.pipeline_stage == "stage_1_risk_assessment"
        assert ra.metadata.model_used != ""

    @patch("litellm.completion")
    def test_strips_markdown_fences_from_response(self, mock_completion, low_risk_application):
        raw = f"```json\n{json.dumps(VALID_RISK_ASSESSMENT_DICT)}\n```"
        mock_completion.return_value = make_llm_response(raw)
        from src.pipeline import run_stage1
        ra, _, _ = run_stage1(low_risk_application)
        assert isinstance(ra, RiskAssessment)

    @patch("litellm.completion")
    def test_json_parse_failure_appends_to_list(self, mock_completion, low_risk_application):
        mock_completion.return_value = make_llm_response("not json at all {{{")
        from src.pipeline import run_stage1
        json_failures: list = []
        with pytest.raises(Exception):
            run_stage1(low_risk_application, json_failures=json_failures)
        assert "APP-2026-0301" in json_failures

    @patch("litellm.completion")
    def test_pydantic_failure_appends_to_list(self, mock_completion, low_risk_application):
        """Return valid JSON that fails Pydantic (invalid risk level)."""
        bad_data = {**VALID_RISK_ASSESSMENT_DICT, "overall_risk_level": "not_a_valid_level"}
        mock_completion.return_value = make_llm_response(json.dumps(bad_data))
        from src.pipeline import run_stage1
        pydantic_failures: list = []
        with pytest.raises(Exception):
            run_stage1(low_risk_application, pydantic_failures=pydantic_failures)
        assert "APP-2026-0301" in pydantic_failures

    @patch("time.sleep")
    @patch("litellm.completion")
    def test_retries_on_transient_error(self, mock_completion, mock_sleep, low_risk_application):
        """First call fails, second call succeeds — should retry without raising."""
        mock_completion.side_effect = [
            RuntimeError("API timeout"),
            make_llm_response(json.dumps(VALID_RISK_ASSESSMENT_DICT)),
        ]
        from src.pipeline import run_stage1
        with patch.dict(os.environ, {"MAX_RETRIES": "3", "RETRY_BACKOFF_BASE": "1"}):
            import src.pipeline as p
            p._MAX_RETRIES = 3
            p._BACKOFF_BASE = 1
            ra, _, _ = run_stage1(low_risk_application)
        assert isinstance(ra, RiskAssessment)
        assert mock_sleep.called

    @patch("time.sleep")
    @patch("litellm.completion")
    def test_raises_after_all_retries_exhausted(self, mock_completion, mock_sleep, low_risk_application):
        """All retries fail — should raise after MAX_RETRIES attempts."""
        mock_completion.side_effect = RuntimeError("Persistent error")
        import src.pipeline as p
        original = p._MAX_RETRIES
        p._MAX_RETRIES = 2
        try:
            with pytest.raises(RuntimeError):
                from src.pipeline import run_stage1
                run_stage1(low_risk_application)
            assert mock_completion.call_count == 2
        finally:
            p._MAX_RETRIES = original

    @patch("litellm.completion")
    def test_tools_not_passed_when_dispatcher_is_none(self, mock_completion, low_risk_application):
        mock_completion.return_value = make_llm_response(json.dumps(VALID_RISK_ASSESSMENT_DICT))
        from src.pipeline import run_stage1
        import src.pipeline as p
        original = p._ENABLE_TOOLS
        p._ENABLE_TOOLS = True
        try:
            run_stage1(low_risk_application, tool_dispatcher=None)
            call_kwargs = mock_completion.call_args[1]
            assert "tools" not in call_kwargs or call_kwargs.get("tools") is None
        finally:
            p._ENABLE_TOOLS = original

    @patch("litellm.completion")
    def test_tool_use_loop_executes_and_continues(self, mock_completion, low_risk_application):
        """LLM requests a tool call, dispatcher handles it, then returns final JSON."""
        from src.tools import ToolDispatcher
        from services.compliance_one import ComplianceOneService
        from services.sharepoint import SharePointService
        from services.outlook import OutlookService

        tool_resp = make_tool_call_response(
            "check_sanctions_list",
            {"entity_name": "Greenfield State Pension Fund", "entity_type": "fund"},
        )
        final_resp = make_llm_response(json.dumps(VALID_RISK_ASSESSMENT_DICT))
        mock_completion.side_effect = [tool_resp, final_resp]

        dispatcher = ToolDispatcher(
            compliance_service=ComplianceOneService(),
            sharepoint_service=SharePointService(),
            outlook_service=OutlookService(),
        )

        import src.pipeline as p
        original_enable = p._ENABLE_TOOLS
        p._ENABLE_TOOLS = True
        try:
            from src.pipeline import run_stage1
            ra, _, _ = run_stage1(low_risk_application, tool_dispatcher=dispatcher)
            assert isinstance(ra, RiskAssessment)
            assert mock_completion.call_count == 2
        finally:
            p._ENABLE_TOOLS = original_enable

    @patch("litellm.completion")
    def test_on_thinking_callback_called_when_thinking_present(self, mock_completion, low_risk_application):
        thinking_response = f"<thinking>dim analysis here</thinking>\n{json.dumps(VALID_RISK_ASSESSMENT_DICT)}"
        mock_completion.return_value = make_llm_response(thinking_response)
        from src.pipeline import run_stage1
        captured = []
        ra, _, _ = run_stage1(low_risk_application, on_thinking=captured.append)
        assert len(captured) == 1
        assert "dim analysis" in captured[0]
        assert isinstance(ra, RiskAssessment)

    @patch("litellm.completion")
    def test_on_thinking_none_does_not_crash_when_thinking_present(self, mock_completion, low_risk_application):
        thinking_response = f"<thinking>reasoning block</thinking>\n{json.dumps(VALID_RISK_ASSESSMENT_DICT)}"
        mock_completion.return_value = make_llm_response(thinking_response)
        from src.pipeline import run_stage1
        ra, _, _ = run_stage1(low_risk_application, on_thinking=None)
        assert isinstance(ra, RiskAssessment)


# ── run_stage2 ─────────────────────────────────────────────────────────────────

class TestRunStage2:
    @patch("litellm.completion")
    def test_returns_onboarding_summary(self, mock_completion, low_risk_application, risk_assessment):
        mock_completion.return_value = make_llm_response(json.dumps(VALID_ONBOARDING_SUMMARY_DICT))
        from src.pipeline import run_stage2
        summary, latency, tokens = run_stage2(low_risk_application, risk_assessment)
        assert isinstance(summary, OnboardingSummary)
        assert summary.complexity_level == "low"
        assert summary.client_id == "APP-2026-0301"

    @patch("litellm.completion")
    def test_metadata_contains_risk_reference(self, mock_completion, low_risk_application, risk_assessment):
        mock_completion.return_value = make_llm_response(json.dumps(VALID_ONBOARDING_SUMMARY_DICT))
        from src.pipeline import run_stage2
        summary, _, _ = run_stage2(low_risk_application, risk_assessment)
        assert summary.metadata.risk_assessment_reference == "APP-2026-0301"

    @patch("litellm.completion")
    def test_stage2_does_not_pass_tools(self, mock_completion, low_risk_application, risk_assessment):
        """Stage 2 must never use tool calling — it works from already-gathered data."""
        mock_completion.return_value = make_llm_response(json.dumps(VALID_ONBOARDING_SUMMARY_DICT))
        from src.pipeline import run_stage2
        run_stage2(low_risk_application, risk_assessment)
        call_kwargs = mock_completion.call_args[1]
        assert "tools" not in call_kwargs

    @patch("litellm.completion")
    def test_json_parse_failure_appends_to_list(self, mock_completion, low_risk_application, risk_assessment):
        mock_completion.return_value = make_llm_response("not valid json {")
        from src.pipeline import run_stage2
        json_failures: list = []
        with pytest.raises(Exception):
            run_stage2(low_risk_application, risk_assessment, json_failures=json_failures)
        assert "APP-2026-0301" in json_failures

    @patch("litellm.completion")
    def test_pydantic_failure_appends_to_list(self, mock_completion, low_risk_application, risk_assessment):
        bad_data = {**VALID_ONBOARDING_SUMMARY_DICT, "complexity_level": "invalid"}
        mock_completion.return_value = make_llm_response(json.dumps(bad_data))
        from src.pipeline import run_stage2
        pydantic_failures: list = []
        with pytest.raises(Exception):
            run_stage2(low_risk_application, risk_assessment, pydantic_failures=pydantic_failures)
        assert "APP-2026-0301" in pydantic_failures

    @patch("litellm.completion")
    def test_returns_positive_tokens(self, mock_completion, low_risk_application, risk_assessment):
        mock_completion.return_value = make_llm_response(json.dumps(VALID_ONBOARDING_SUMMARY_DICT), tokens=600)
        from src.pipeline import run_stage2
        _, _, tokens = run_stage2(low_risk_application, risk_assessment)
        assert tokens == 600

    @patch("litellm.completion")
    def test_message_contains_both_application_and_assessment(self, mock_completion, low_risk_application, risk_assessment):
        """Verify the Stage 2 prompt includes both Stage 1 output and the original application."""
        mock_completion.return_value = make_llm_response(json.dumps(VALID_ONBOARDING_SUMMARY_DICT))
        from src.pipeline import run_stage2
        run_stage2(low_risk_application, risk_assessment)
        call_kwargs = mock_completion.call_args[1]
        user_message = call_kwargs["messages"][1]["content"]
        assert "APP-2026-0301" in user_message
        assert "Greenfield State Pension Fund" in user_message
        assert "fast_track" in user_message  # from risk_assessment
