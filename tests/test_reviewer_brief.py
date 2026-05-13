"""Tests for the ReviewerBrief model and generate_reviewer_brief function."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from tests.conftest import make_llm_response


# ── Shared fixture ─────────────────────────────────────────────────────────────

VALID_BRIEF_DICT = {
    "executive_summary": "Apex Global Holdings presents critical AML risks across four dimensions.",
    "primary_risk_factors": [
        "Nominee directors with unverifiable UBO identity",
        "Vague 'diversified business interests' source of funds — no documentation provided",
        "Possible OFAC SDN name similarity unresolved at time of escalation",
    ],
    "verification_checklist": [
        "Confirm beneficial owner identity via government-issued photo ID and certified corporate records",
        "Obtain certified source-of-funds documentation from originating financial institution",
        "Resolve OFAC SDN possible match via ComplianceOne enhanced sanctions screening",
    ],
    "suggested_next_steps": [
        "Assign case to Senior Review team for EDD within 5 business days",
        "Request full UBO disclosure package before any further processing",
    ],
    "regulatory_notes": (
        "FinCEN CDD Rule 31 CFR 1010.230 requires identification of beneficial owners "
        "with 25%+ equity stake before account opening. BSA Section 5318(h) mandates "
        "AML program adequacy review for offshore corporate clients."
    ),
}


# ── TestReviewerBriefModel ─────────────────────────────────────────────────────

class TestReviewerBriefModel:
    def test_valid_brief(self):
        from src.reviewer_brief import ReviewerBrief
        brief = ReviewerBrief.model_validate(VALID_BRIEF_DICT)
        assert brief.executive_summary == VALID_BRIEF_DICT["executive_summary"]
        assert len(brief.primary_risk_factors) == 3
        assert len(brief.verification_checklist) == 3
        assert len(brief.suggested_next_steps) == 2

    def test_missing_executive_summary_fails(self):
        from src.reviewer_brief import ReviewerBrief
        data = {k: v for k, v in VALID_BRIEF_DICT.items() if k != "executive_summary"}
        with pytest.raises(ValidationError):
            ReviewerBrief.model_validate(data)

    def test_fewer_than_3_risk_factors_rejected(self):
        from src.reviewer_brief import ReviewerBrief
        data = {**VALID_BRIEF_DICT, "primary_risk_factors": ["only one risk factor", "two"]}
        with pytest.raises(ValidationError):
            ReviewerBrief.model_validate(data)

    def test_empty_verification_checklist_rejected(self):
        from src.reviewer_brief import ReviewerBrief
        data = {**VALID_BRIEF_DICT, "verification_checklist": []}
        with pytest.raises(ValidationError):
            ReviewerBrief.model_validate(data)

    def test_empty_suggested_next_steps_rejected(self):
        from src.reviewer_brief import ReviewerBrief
        data = {**VALID_BRIEF_DICT, "suggested_next_steps": []}
        with pytest.raises(ValidationError):
            ReviewerBrief.model_validate(data)


# ── TestGenerateReviewerBrief ──────────────────────────────────────────────────

class TestGenerateReviewerBrief:
    def _call(self, **kwargs):
        from src.reviewer_brief import generate_reviewer_brief
        return generate_reviewer_brief(
            risk_assessment_json=kwargs.get("risk_assessment_json", '{"overall_risk_level": "critical"}'),
            onboarding_summary_json=kwargs.get("onboarding_summary_json", '{"complexity_level": "high"}'),
            routing_decision_json=kwargs.get("routing_decision_json", '{"decision": "ESCALATE"}'),
        )

    @patch("litellm.completion")
    def test_returns_reviewer_brief(self, mock_completion):
        from src.reviewer_brief import ReviewerBrief
        mock_completion.return_value = make_llm_response(json.dumps(VALID_BRIEF_DICT))
        result = self._call()
        assert isinstance(result, ReviewerBrief)
        assert len(result.primary_risk_factors) >= 3

    @patch("litellm.completion")
    def test_handles_fenced_json(self, mock_completion):
        from src.reviewer_brief import ReviewerBrief
        fenced = f"```json\n{json.dumps(VALID_BRIEF_DICT)}\n```"
        mock_completion.return_value = make_llm_response(fenced)
        result = self._call()
        assert isinstance(result, ReviewerBrief)

    @patch("litellm.completion")
    def test_raises_on_bad_json(self, mock_completion):
        mock_completion.return_value = make_llm_response("This is not JSON at all.")
        with pytest.raises(Exception):
            self._call()

    @patch("time.sleep")
    @patch("litellm.completion")
    def test_retries_on_api_error(self, mock_completion, mock_sleep):
        mock_completion.side_effect = [
            RuntimeError("API timeout"),
            make_llm_response(json.dumps(VALID_BRIEF_DICT)),
        ]
        import src.reviewer_brief as rb
        original = rb._MAX_RETRIES
        rb._MAX_RETRIES = 3
        try:
            result = self._call()
            from src.reviewer_brief import ReviewerBrief
            assert isinstance(result, ReviewerBrief)
            assert mock_sleep.called
        finally:
            rb._MAX_RETRIES = original

    @patch("time.sleep")
    @patch("litellm.completion")
    def test_raises_after_max_retries(self, mock_completion, mock_sleep):
        mock_completion.side_effect = RuntimeError("Persistent API error")
        import src.reviewer_brief as rb
        original = rb._MAX_RETRIES
        rb._MAX_RETRIES = 2
        try:
            with pytest.raises(RuntimeError):
                self._call()
            assert mock_completion.call_count == 2
        finally:
            rb._MAX_RETRIES = original
