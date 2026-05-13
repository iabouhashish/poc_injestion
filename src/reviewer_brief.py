"""
Reviewer briefing agent: triggered on ESCALATE routing decisions.
Generates a structured briefing document for the human compliance reviewer.
"""
from __future__ import annotations

import json
import logging
import os

import litellm
from pydantic import BaseModel, Field

from src.utils import extract_json, retry_with_backoff

# retry_with_backoff signature (src/utils.py):
#   fn: Callable[[], T], max_retries: int, backoff_base: float, context_label: str = "LLM"
# Raises the last exception if all retries are exhausted.

logger = logging.getLogger(__name__)
_MODEL = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-6")
_MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
_BACKOFF_BASE = float(os.getenv("RETRY_BACKOFF_BASE", "2"))


class ReviewerBrief(BaseModel):
    executive_summary: str
    primary_risk_factors: list[str] = Field(min_length=3)
    verification_checklist: list[str] = Field(min_length=1)
    suggested_next_steps: list[str] = Field(min_length=1)
    regulatory_notes: str


_SYSTEM = """You are a senior compliance analyst at Crestview Capital Group.
You receive a completed AML/KYC risk assessment, onboarding summary, and routing decision
for a client application that has been escalated for human review.

Your job: produce a structured briefing that helps the reviewing compliance officer understand
exactly why this case was escalated and what they need to do next.

Rules:
- Be specific. Name the actual risk factors you see in the data — not the scoring system.
- Verification checklist items must be actionable: "Confirm beneficial owner identity via
  government-issued ID" not "Verify identity."
- Regulatory notes must name a specific rule or threshold (e.g., BSA Section 5318(g),
  FinCEN CDD Rule 31 CFR 1010.230, FATF Recommendation 10).
- Do not repeat the routing rationale verbatim — add analytical value."""


def generate_reviewer_brief(
    risk_assessment_json: str,
    onboarding_summary_json: str,
    routing_decision_json: str,
) -> ReviewerBrief:
    schema = ReviewerBrief.model_json_schema()
    user_msg = (
        f"<risk_assessment>{risk_assessment_json}</risk_assessment>\n"
        f"<onboarding_summary>{onboarding_summary_json}</onboarding_summary>\n"
        f"<routing_decision>{routing_decision_json}</routing_decision>\n\n"
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
        context_label="ReviewerBrief",
    )
    raw = response.choices[0].message.content or ""
    brief = ReviewerBrief.model_validate(json.loads(extract_json(raw)))
    logger.info(
        "[ReviewerBrief] Generated brief — %d risk factors, %d checklist items",
        len(brief.primary_risk_factors),
        len(brief.verification_checklist),
    )
    return brief
