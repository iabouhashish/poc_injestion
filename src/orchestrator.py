"""
Meta-orchestrator: reads risk assessment + eval scores → final routing decision.
A separate LLM call with a compliance officer persona that acts on pipeline outputs.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Literal, Optional

import litellm
from pydantic import BaseModel, Field

from src.utils import extract_json, retry_with_backoff

logger = logging.getLogger(__name__)
_MODEL = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-6")
_MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
_BACKOFF_BASE = float(os.getenv("RETRY_BACKOFF_BASE", "2"))


class RoutingDecision(BaseModel):
    decision: Literal["APPROVE", "CONDITIONAL_APPROVE", "ESCALATE", "REJECT"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    conditions: list[str] = []
    escalation_reason: Optional[str] = None
    reviewer_team: Literal["compliance_officer", "senior_review", "legal", "rejected"]


_SYSTEM = """You are a senior compliance officer at Crestview Capital Group.
You receive a completed risk assessment, onboarding summary, and LLM-as-judge evaluation scores
for a client application. Your job is to make a final routing decision.

Decision criteria:
- APPROVE: overall_risk_level=low, no critical issues, eval scores >=80 across all dimensions
- CONDITIONAL_APPROVE: overall_risk_level=medium OR any eval score 60-79, no sanctions/PEP hits;
  list specific conditions (documents to request, enhanced DD steps)
- ESCALATE: overall_risk_level=high OR any critical issues OR eval score <60; assign to senior review
- REJECT: active sanctions match (confidence >=0.7) OR high + PEP + adverse media together

Always give a specific rationale that could justify the decision to a regulator."""


def route_decision(
    risk_assessment_json: str,
    onboarding_summary_json: str,
    eval_json: str,
) -> RoutingDecision:
    schema = RoutingDecision.model_json_schema()
    user_msg = (
        f"<risk_assessment>{risk_assessment_json}</risk_assessment>\n"
        f"<onboarding_summary>{onboarding_summary_json}</onboarding_summary>\n"
        f"<evaluation>{eval_json}</evaluation>\n\n"
        f"Respond with JSON matching this schema:\n{json.dumps(schema, indent=2)}"
    )
    response = retry_with_backoff(
        lambda: litellm.completion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0,
        ),
        max_retries=_MAX_RETRIES,
        backoff_base=_BACKOFF_BASE,
        context_label="Orchestrator",
    )
    raw = response.choices[0].message.content or ""
    decision = RoutingDecision.model_validate(json.loads(extract_json(raw)))
    logger.info(
        "[Orchestrator] decision=%s | confidence=%.2f | team=%s",
        decision.decision, decision.confidence, decision.reviewer_team,
    )
    return decision
