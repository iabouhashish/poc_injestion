"""
LLM-as-judge evaluation module.

A separate LLM call (independent of the pipeline stages) evaluates the quality
of each RiskAssessment, OnboardingSummary, and optionally the Stage 0 extraction.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import litellm

from models.client import ClientApplication
from models.extraction import ExtractedClientData, CompletenessCheckResult
from models.risk import RiskAssessment
from models.onboarding import OnboardingSummary
from models.evaluation import (
    ApplicationEvaluation,
    ExtractionEvaluation,
    RiskAssessmentEvaluation,
    OnboardingSummaryEvaluation,
)
from src.utils import extract_json, retry_with_backoff

logger = logging.getLogger(__name__)

_EVALUATOR_MODEL = os.getenv("EVALUATOR_MODEL", "anthropic/claude-sonnet-4-6")
_MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
_BACKOFF_BASE = float(os.getenv("RETRY_BACKOFF_BASE", "2"))


def _load_prompt(filename: str) -> str:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


_EVAL_SYSTEM = _load_prompt("evaluator_system_prompt.txt")
_EVAL_TEMPLATE = _load_prompt("evaluator_user_prompt.txt")

_SCORE_FIELD = {"type": "object", "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 100}, "explanation": {"type": "string"}}, "required": ["score", "explanation"]}


def _build_eval_schema(include_extraction: bool = False) -> str:
    schema: dict = {
        "type": "object",
        "properties": {
            "risk_assessment_evaluation": {
                "type": "object",
                "properties": {
                    "reasoning_quality": _SCORE_FIELD,
                    "reasoning_completeness": _SCORE_FIELD,
                    "risk_level_appropriateness": _SCORE_FIELD,
                    "compliance_flags_accuracy": _SCORE_FIELD,
                    "overall_assessment_quality": _SCORE_FIELD,
                    "critical_issues": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "reasoning_quality", "reasoning_completeness",
                    "risk_level_appropriateness", "compliance_flags_accuracy",
                    "overall_assessment_quality", "critical_issues",
                ],
            },
            "onboarding_summary_evaluation": {
                "type": "object",
                "properties": {
                    "actionability": _SCORE_FIELD,
                    "risk_grounding": _SCORE_FIELD,
                    "next_steps_quality": _SCORE_FIELD,
                    "completeness": _SCORE_FIELD,
                    "overall_summary_quality": _SCORE_FIELD,
                },
                "required": [
                    "actionability", "risk_grounding", "next_steps_quality",
                    "completeness", "overall_summary_quality",
                ],
            },
        },
        "required": ["risk_assessment_evaluation", "onboarding_summary_evaluation"],
    }

    if include_extraction:
        schema["properties"]["extraction_evaluation"] = {
            "type": "object",
            "properties": {
                "extraction_accuracy": _SCORE_FIELD,
                "confidence_calibration": _SCORE_FIELD,
                "completeness_detection": _SCORE_FIELD,
            },
            "required": ["extraction_accuracy", "confidence_calibration", "completeness_detection"],
        }
        schema["required"].append("extraction_evaluation")

    return json.dumps(schema, indent=2)


def evaluate_application(
    application: ClientApplication,
    risk_assessment: RiskAssessment,
    onboarding_summary: OnboardingSummary,
    extracted_data: Optional[ExtractedClientData] = None,
    completeness_result: Optional[CompletenessCheckResult] = None,
) -> tuple[ApplicationEvaluation, float, int]:
    """
    Run the LLM-as-judge evaluator for one processed application.
    Returns (ApplicationEvaluation, latency_seconds, tokens_used).
    """
    include_extraction = extracted_data is not None
    eval_schema = _build_eval_schema(include_extraction=include_extraction)

    if include_extraction and extracted_data is not None:
        cr_json = completeness_result.model_dump_json(indent=2) if completeness_result else "{}"
        extracted_section = (
            "<extracted_client_data>\n"
            f"{extracted_data.model_dump_json(indent=2)}\n"
            "</extracted_client_data>\n\n"
            "<completeness_check>\n"
            f"{cr_json}\n"
            "</completeness_check>\n\n"
            "<extraction_evaluation_instructions>\n"
            "Also evaluate the extraction quality:\n"
            "- extraction_accuracy (0-100): Compare extracted field values against the ground-truth "
            "client_application data. Did the LLM extract values that match the actual application details?\n"
            "- confidence_calibration (0-100): Are confidence scores well-calibrated? "
            "Correctly-extracted fields should have high confidence; missing/vague fields should have low confidence.\n"
            "- completeness_detection (0-100): Did the completeness checker correctly identify "
            "missing or unreliable fields based on the confidence scores?\n"
            "</extraction_evaluation_instructions>"
        )
    else:
        extracted_section = ""

    user_content = _EVAL_TEMPLATE.format(
        client_application_json=application.model_dump_json(indent=2),
        risk_assessment_json=risk_assessment.model_dump_json(indent=2),
        onboarding_summary_json=onboarding_summary.model_dump_json(indent=2),
        evaluation_schema=eval_schema,
        extracted_data_section=extracted_section,
    )

    messages = [
        {"role": "system", "content": _EVAL_SYSTEM},
        {"role": "user", "content": user_content},
    ]

    import time as _time
    logger.info("[Evaluator] Scoring | client=%s | extraction=%s", application.application_id, include_extraction)
    t0 = _time.time()

    def _do_call():
        return litellm.completion(model=_EVALUATOR_MODEL, messages=messages, temperature=0)

    response = retry_with_backoff(
        _do_call, _MAX_RETRIES, _BACKOFF_BASE,
        context_label=f"Evaluator/{application.application_id}",
    )
    tokens = response.usage.total_tokens if response.usage else 0
    raw = response.choices[0].message.content or ""
    latency = _time.time() - t0

    json_str = extract_json(raw)
    data = json.loads(json_str)

    ra_eval = RiskAssessmentEvaluation.model_validate(data["risk_assessment_evaluation"])
    os_eval = OnboardingSummaryEvaluation.model_validate(data["onboarding_summary_evaluation"])

    ext_eval: Optional[ExtractionEvaluation] = None
    if include_extraction and "extraction_evaluation" in data:
        ext_eval = ExtractionEvaluation.model_validate(data["extraction_evaluation"])

    eval_result = ApplicationEvaluation(
        client_id=application.application_id,
        client_name=application.client_name,
        risk_assessment_evaluation=ra_eval,
        onboarding_summary_evaluation=os_eval,
        extraction_evaluation=ext_eval,
        evaluator_model=_EVALUATOR_MODEL,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )

    logger.info(
        "[Evaluator] Complete | client=%s | ra_score=%.1f | os_score=%.1f | critical=%d | latency=%.1fs",
        application.application_id,
        ra_eval.overall_assessment_quality.score,
        os_eval.overall_summary_quality.score,
        len(ra_eval.critical_issues),
        latency,
    )
    return eval_result, latency, tokens
