"""Tests for RoutingDecision model and route_decision() function."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from src.orchestrator import RoutingDecision, route_decision


VALID_DECISION = {
    "decision": "APPROVE",
    "confidence": 0.92,
    "rationale": "Clean US pension fund, no red flags, eval scores above threshold.",
    "conditions": [],
    "escalation_reason": None,
    "reviewer_team": "compliance_officer",
}


class TestRoutingDecision:
    def test_valid_approve(self):
        d = RoutingDecision.model_validate(VALID_DECISION)
        assert d.decision == "APPROVE"
        assert d.confidence == 0.92
        assert d.reviewer_team == "compliance_officer"

    def test_valid_conditional_approve_with_conditions(self):
        data = {**VALID_DECISION, "decision": "CONDITIONAL_APPROVE",
                "conditions": ["Request certified UBO disclosure"],
                "reviewer_team": "compliance_officer"}
        d = RoutingDecision.model_validate(data)
        assert d.decision == "CONDITIONAL_APPROVE"
        assert len(d.conditions) == 1

    def test_confidence_too_high_raises(self):
        with pytest.raises(ValidationError):
            RoutingDecision.model_validate({**VALID_DECISION, "confidence": 1.5})

    def test_confidence_too_low_raises(self):
        with pytest.raises(ValidationError):
            RoutingDecision.model_validate({**VALID_DECISION, "confidence": -0.1})

    def test_confidence_boundary_values_valid(self):
        RoutingDecision.model_validate({**VALID_DECISION, "confidence": 0.0})
        RoutingDecision.model_validate({**VALID_DECISION, "confidence": 1.0})

    def test_unknown_decision_raises(self):
        with pytest.raises(ValidationError):
            RoutingDecision.model_validate({**VALID_DECISION, "decision": "MAYBE"})

    def test_unknown_reviewer_team_raises(self):
        with pytest.raises(ValidationError):
            RoutingDecision.model_validate({**VALID_DECISION, "reviewer_team": "unknown_team"})


class TestRouteDecision:
    def _make_mock_response(self, content: str) -> MagicMock:
        msg = MagicMock()
        msg.content = content
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    @patch("litellm.completion")
    def test_returns_routing_decision(self, mock_completion):
        mock_completion.return_value = self._make_mock_response(json.dumps(VALID_DECISION))
        result = route_decision("{}", "{}", "{}")
        assert isinstance(result, RoutingDecision)
        assert result.decision == "APPROVE"

    @patch("litellm.completion")
    def test_handles_fenced_json(self, mock_completion):
        fenced = f"```json\n{json.dumps(VALID_DECISION)}\n```"
        mock_completion.return_value = self._make_mock_response(fenced)
        result = route_decision("{}", "{}", "{}")
        assert isinstance(result, RoutingDecision)

    @patch("litellm.completion")
    def test_raises_on_bad_json(self, mock_completion):
        mock_completion.return_value = self._make_mock_response("not json at all")
        with pytest.raises(Exception):
            route_decision("{}", "{}", "{}")
