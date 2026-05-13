from __future__ import annotations

import json
import logging
import os
from typing import Optional

from models.extraction import ExtractedClientData, CompletenessCheckResult
from models.pipeline import PipelineResult
from persistence.models import ApplicationResultRecord, EvaluationScoreRecord, LLMCallRecord
from persistence.repository import (
    save_application_result,
    save_eval,
    save_llm_call,
    update_application_result,
)
from src.pipeline import _MODEL

logger = logging.getLogger("crestview.pipeline")


def _save_phase1_to_db(
    session,
    run_id: str,
    application,
    monday_item_id: Optional[str],
    extracted_data: Optional[ExtractedClientData],
    completeness_result: Optional[CompletenessCheckResult],
    s0_latency: float,
    s0_tokens: int,
    corrections: dict,
    ops_review_applied: bool,
) -> None:
    record = ApplicationResultRecord(
        run_id=run_id,
        application_id=application.application_id,
        client_name=application.client_name,
        client_type=application.client_type,
        estimated_aum=application.estimated_aum,
        processing_status="phase1_complete",
        stage0_latency_seconds=round(s0_latency, 2) if s0_latency else None,
        extraction_confidence=extracted_data.overall_extraction_confidence if extracted_data else None,
        fields_missing=json.dumps(completeness_result.required_fields_missing) if completeness_result else None,
        review_action=completeness_result.recommended_action if completeness_result else None,
        ops_review_corrections=json.dumps(corrections) if corrections else None,
        monday_item_id=monday_item_id,
        extraction_json=extracted_data.model_dump_json() if extracted_data else None,
        completeness_json=completeness_result.model_dump_json() if completeness_result else None,
        phase_completed=1,
    )
    save_application_result(session, record)

    if s0_tokens and s0_latency:
        save_llm_call(session, LLMCallRecord(
            run_id=run_id,
            application_id=application.application_id,
            stage="document_extraction",
            model=_MODEL,
            total_tokens=s0_tokens,
            latency_seconds=round(s0_latency, 2),
            success=True,
        ))


def _update_phase2_in_db(
    session,
    phase1_run_id: str,
    phase2_run_id: str,
    result: PipelineResult,
) -> None:
    ra = result.risk_assessment
    os_ = result.onboarding_summary
    pm = result.pipeline_metadata
    ev = result.evaluation

    update_application_result(
        session,
        run_id=phase1_run_id,
        application_id=result.client_application.application_id,
        processing_status="completed",
        risk_level=ra.overall_risk_level,
        review_track=ra.recommended_review_track,
        complexity_level=os_.complexity_level,
        compliance_flags=json.dumps(ra.compliance_flags),
        missing_information=json.dumps(ra.missing_information),
        risk_assessment_json=ra.model_dump_json(),
        onboarding_summary_json=os_.model_dump_json(),
        stage1_latency_seconds=pm.stage1_latency_seconds,
        stage2_latency_seconds=pm.stage2_latency_seconds,
        eval_latency_seconds=pm.evaluation_latency_seconds,
        monday_eval_item_id=pm.monday_eval_item_id,
        phase_completed=2,
    )

    if ev:
        ra_e = ev.risk_assessment_evaluation
        os_e = ev.onboarding_summary_evaluation
        ext_e = ev.extraction_evaluation
        eval_record = EvaluationScoreRecord(
            run_id=phase2_run_id,
            application_id=result.client_application.application_id,
            reasoning_quality=ra_e.reasoning_quality.score,
            reasoning_completeness=ra_e.reasoning_completeness.score,
            risk_level_appropriateness=ra_e.risk_level_appropriateness.score,
            compliance_flags_accuracy=ra_e.compliance_flags_accuracy.score,
            overall_risk_assessment_score=ra_e.overall_assessment_quality.score,
            actionability=os_e.actionability.score,
            risk_grounding=os_e.risk_grounding.score,
            next_steps_quality=os_e.next_steps_quality.score,
            completeness=os_e.completeness.score,
            overall_summary_score=os_e.overall_summary_quality.score,
            critical_issues=json.dumps(ra_e.critical_issues),
            evaluator_reasoning=json.dumps({
                "reasoning_quality": ra_e.reasoning_quality.model_dump(),
                "reasoning_completeness": ra_e.reasoning_completeness.model_dump(),
                "risk_level_appropriateness": ra_e.risk_level_appropriateness.model_dump(),
                "compliance_flags_accuracy": ra_e.compliance_flags_accuracy.model_dump(),
                "overall_assessment_quality": ra_e.overall_assessment_quality.model_dump(),
                "actionability": os_e.actionability.model_dump(),
                "risk_grounding": os_e.risk_grounding.model_dump(),
                "next_steps_quality": os_e.next_steps_quality.model_dump(),
                "completeness": os_e.completeness.model_dump(),
                "overall_summary_quality": os_e.overall_summary_quality.model_dump(),
            }),
            extraction_accuracy=ext_e.extraction_accuracy.score if ext_e else None,
            confidence_calibration=ext_e.confidence_calibration.score if ext_e else None,
            completeness_detection=ext_e.completeness_detection.score if ext_e else None,
            extraction_eval_reasoning=json.dumps({
                "extraction_accuracy": ext_e.extraction_accuracy.model_dump(),
                "confidence_calibration": ext_e.confidence_calibration.model_dump(),
                "completeness_detection": ext_e.completeness_detection.model_dump(),
            }) if ext_e else None,
        )
        save_eval(session, eval_record)

    for stage, tokens, latency in [
        ("risk_assessment", pm.stage1_tokens, pm.stage1_latency_seconds),
        ("onboarding_summary", pm.stage2_tokens, pm.stage2_latency_seconds),
    ]:
        if tokens:
            save_llm_call(session, LLMCallRecord(
                run_id=phase2_run_id,
                application_id=result.client_application.application_id,
                stage=stage,
                model=_MODEL,
                total_tokens=tokens,
                latency_seconds=latency or 0.0,
                success=True,
            ))

    if pm.evaluator_tokens and pm.evaluation_latency_seconds:
        save_llm_call(session, LLMCallRecord(
            run_id=phase2_run_id,
            application_id=result.client_application.application_id,
            stage="evaluation",
            model=os.getenv("EVALUATOR_MODEL", _MODEL),
            total_tokens=pm.evaluator_tokens,
            latency_seconds=pm.evaluation_latency_seconds,
            success=True,
        ))


def _save_failure_to_db(session, run_id: str, application, error_message: str) -> None:
    save_application_result(session, ApplicationResultRecord(
        run_id=run_id,
        application_id=application.application_id,
        client_name=application.client_name,
        client_type=application.client_type,
        estimated_aum=application.estimated_aum,
        processing_status="failed",
        error_message=error_message[:1000] if error_message else None,
    ))
