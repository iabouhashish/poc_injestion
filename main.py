"""
Crestview Capital Group — AI-Powered Client Onboarding Pipeline

Two-phase operation:
  Phase 1 (Agent 1):  python main.py --phase=1
  Phase 2 (Agent 2):  python main.py --phase=2

  Demo reset:         python main.py --clear-db
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich import box

load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────────
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("crestview.pipeline")

_api_key = os.getenv("ANTHROPIC_API_KEY", "")
if _api_key.startswith("sk-ant-") and "xxx" not in _api_key:
    logger.warning("Real ANTHROPIC_API_KEY detected — ensure .env is NOT committed to version control")

# ── Imports ────────────────────────────────────────────────────────────────────
from models.extraction import ExtractedClientData, CompletenessCheckResult, ExtractionConfidence
from models.pipeline import PipelineResult, PipelineMetrics, PipelineResultMetadata
from src.data_loader import load_applications
from src.document_loader import load_documents
from src.completeness_checker import check_completeness
from src.pipeline import run_stage0, run_stage1, run_stage2, _ENABLE_TOOLS, _MODEL, _PIPELINE_VERSION
from src.evaluator import evaluate_application
from src.output_writer import (
    create_run_directory,
    update_latest_symlink,
    write_application_files,
    write_failed_application_files,
    write_phase1_files,
    write_run_summary,
)
from services import (
    ClientHubService,
    ComplianceOneService,
    SharePointService,
    SalesforceService,
    OutlookService,
    MondayService,
    MondayEvalService,
)
from src.tools import ToolDispatcher
from persistence import create_all_tables, get_session
from persistence.models import (
    ApplicationResultRecord,
    EvaluationScoreRecord,
    LLMCallRecord,
    PipelineRunRecord,
)
from persistence.repository import (
    get_phase1_records_by_monday_ids,
    get_unprocessed_phase1_records,
    save_application_result,
    save_eval,
    save_llm_call,
    save_pipeline_run,
    update_application_result,
    update_pipeline_run,
)

console = Console()
RUN_EVALUATIONS = os.getenv("RUN_EVALUATIONS", "true").lower() == "true"
STORE_RAW_LLM = os.getenv("RAW_LLM_RESPONSES", "false").lower() == "true"
CSV_PATH = Path(__file__).parent / "crestview_client_applications.csv"
OUTPUTS_DIR = Path(os.getenv("OUTPUT_DIR", "outputs"))
OUTPUTS_DIR.mkdir(exist_ok=True)

# ── Simulated service instances ────────────────────────────────────────────────
client_hub = ClientHubService()
compliance_one = ComplianceOneService()
sharepoint = SharePointService()
salesforce = SalesforceService()
outlook = OutlookService()
monday = MondayService()
monday_eval = MondayEvalService()

tool_dispatcher = ToolDispatcher(
    compliance_service=compliance_one,
    sharepoint_service=sharepoint,
    outlook_service=outlook,
)


# ── Ops review simulation ──────────────────────────────────────────────────────

def _make_corrected_confidence(field_name: str, value: str) -> ExtractionConfidence:
    return ExtractionConfidence(
        field_name=field_name,
        extracted_value=value,
        confidence=1.0,
        source_document="ops_review",
        reasoning="Value verified and corrected by ops team using verified application data.",
    )


def _apply_ops_corrections(
    extracted: ExtractedClientData,
    completeness: CompletenessCheckResult,
    application,
) -> tuple[ExtractedClientData, dict]:
    """Fill low-confidence fields from CSV ground truth during ops review."""
    data = extracted.model_dump()
    corrections: dict = {}

    csv_map: dict[str, object] = {
        "client_name": application.client_name,
        "entity_type": application.client_type,
        "investment_amount": str(application.estimated_aum),
        "investment_strategy": application.requested_services,
        "jurisdiction": application.jurisdiction,
        "source_of_funds": application.source_of_funds,
    }

    for field in completeness.required_fields_missing:
        csv_val = csv_map.get(field)
        if csv_val:
            logger.info("[OpsReview] Correcting field '%s' from CSV ground truth", field)
            if field == "beneficial_owners":
                owners = application.beneficial_owners or []
                data["beneficial_owners"] = [
                    _make_corrected_confidence("beneficial_owners", o).model_dump()
                    for o in owners
                ] if owners else data.get("beneficial_owners", [])
            elif field == "authorized_signatories":
                sigs = application.authorized_signatories or []
                data["authorized_signatories"] = [
                    _make_corrected_confidence("authorized_signatories", s).model_dump()
                    for s in sigs
                ] if sigs else data.get("authorized_signatories", [])
            else:
                data[field] = _make_corrected_confidence(field, str(csv_val)).model_dump()
            corrections[field] = str(csv_val)

    data["overall_extraction_confidence"] = max(
        extracted.overall_extraction_confidence,
        len(completeness.required_fields_present) / max(len(completeness.minimum_required_fields), 1),
    )
    return ExtractedClientData.model_validate(data), corrections


def _apply_client_corrections(
    extracted: ExtractedClientData,
    completeness: CompletenessCheckResult,
    application,
) -> tuple[ExtractedClientData, dict]:
    """Apply client-provided document updates (same mechanism as ops corrections)."""
    logger.info(
        "[ClientResponse] Applying client document updates for %s",
        application.application_id,
    )
    return _apply_ops_corrections(extracted, completeness, application)


# ── Agent 1: document extraction + validation ──────────────────────────────────

def run_agent1(
    application,
    monday_item_id: Optional[str],
    pydantic_failures: list,
    json_failures: list,
) -> tuple[Optional[ExtractedClientData], Optional[CompletenessCheckResult], float, int, dict, bool]:
    """
    Full Agent 1 flow: load docs → Stage 0 extraction → completeness check → routing.
    Returns (extracted_data, completeness_result, latency, tokens, corrections, ops_review_applied).
    """
    app_id = application.application_id
    corrections: dict = {}
    ops_review_applied = False

    documents = load_documents(app_id)
    if not documents:
        logger.warning("[Agent1] No documents found for %s — skipping Stage 0", app_id)
        return None, None, 0.0, 0, corrections, ops_review_applied

    doc_names = [d.filename for d in documents]

    if monday_item_id:
        monday.add_update(
            monday_item_id,
            f"Beginning document extraction. {len(documents)} document(s) loaded for {app_id}: {', '.join(doc_names)}",
        )

    try:
        extracted, s0_latency, s0_tokens = run_stage0(
            documents=documents,
            application_id=app_id,
            pydantic_failures=pydantic_failures,
            json_failures=json_failures,
        )
    except Exception as exc:
        logger.error("[Stage0] FAILED for %s: %s", app_id, exc)
        return None, None, 0.0, 0, corrections, ops_review_applied

    completeness = check_completeness(extracted)
    action = completeness.recommended_action
    threshold = completeness.confidence_threshold_used

    if monday_item_id:
        monday.update_extraction_columns(
            monday_item_id,
            extraction_confidence=extracted.overall_extraction_confidence,
            fields_missing=completeness.required_fields_missing,
            review_reason=completeness.reasoning if action != "proceed_to_compliance" else "",
        )

    if action == "proceed_to_compliance":
        logger.info("[Agent1] %s — extraction complete, proceeding to compliance", app_id)
        if monday_item_id:
            monday.update_item_status(
                monday_item_id,
                "Data Processed",
                f"Extraction complete. All required fields extracted with confidence ≥ {threshold:.2f}. "
                f"Overall confidence: {extracted.overall_extraction_confidence:.2f}. "
                f"Data written to ClientHub. Ready for compliance review.",
            )

    elif action == "ops_review_required":
        low_fields = completeness.required_fields_missing
        logger.info(
            "[Agent1] %s requires ops review — %d field(s) below threshold: %s",
            app_id, len(low_fields), ", ".join(low_fields),
        )
        if monday_item_id:
            monday.update_item_status(
                monday_item_id,
                "Pending Review",
                f"Processing → Pending Review: {len(low_fields)} field(s) below confidence "
                f"threshold ({threshold:.2f}): {', '.join(low_fields)}. Ops review required.",
            )

        extracted, corrections = _apply_ops_corrections(extracted, completeness, application)
        ops_review_applied = True
        logger.info("[Agent1] %s — ops review complete, %d field(s) corrected", app_id, len(corrections))

        if monday_item_id:
            corrected_fields = list(corrections.keys()) if corrections else []
            monday.update_item_status(
                monday_item_id,
                "Data Processed",
                f"Ops review complete. {len(corrected_fields)} field(s) corrected: "
                f"{', '.join(corrected_fields) if corrected_fields else 'no changes needed'}. "
                f"Data written to ClientHub. Ready for compliance review.",
            )

    elif action == "return_to_client":
        missing = completeness.required_fields_missing
        logger.info(
            "[Agent1] %s — missing critical information, returning to client: %s",
            app_id, ", ".join(missing),
        )
        if monday_item_id:
            monday.update_item_status(
                monday_item_id,
                "Pending Client",
                f"Processing → Pending Client: Missing critical information — {', '.join(missing)}. "
                "Client has been notified to provide additional documentation.",
            )

        outlook.send_notification(
            recipient="relationship_manager",
            subject=f"Additional Documents Required — {application.client_name}",
            body=f"Application {app_id}: The following required information could not be extracted "
                 f"from submitted documents: {', '.join(missing)}. Please request from client.",
        )

        extracted, corrections = _apply_client_corrections(extracted, completeness, application)
        ops_review_applied = True
        logger.info("[Agent1] %s — simulated client response received, %d field(s) filled", app_id, len(corrections))

        if monday_item_id:
            monday.update_item_status(
                monday_item_id,
                "Data Processed",
                f"Client provided additional documentation. Missing fields resolved: "
                f"{', '.join(corrections.keys()) if corrections else 'no changes'}. "
                f"Data written to ClientHub. Ready for compliance review.",
            )

    client_hub.write_client_record(_to_client_hub_format(application, extracted))

    logger.info("[Agent1] %s — data populated in ClientHub", app_id)
    return extracted, completeness, s0_latency, s0_tokens, corrections, ops_review_applied


def _to_client_hub_format(application, extracted: ExtractedClientData) -> dict:
    data = application.model_dump()
    data["extraction_confidence"] = extracted.overall_extraction_confidence
    data["extracted_client_name"] = extracted.client_name.extracted_value
    data["extracted_entity_type"] = extracted.entity_type.extracted_value
    data["extracted_jurisdiction"] = extracted.jurisdiction.extracted_value
    data["extracted_source_of_funds"] = extracted.source_of_funds.extracted_value
    return data


# ── Compliance officer determination ──────────────────────────────────────────

def _record_officer_determination(
    item_id: Optional[str],
    phase2_status: str,
    risk_level: str,
    review_track: str,
) -> str:
    """
    Records the compliance officer's final determination based on Agent 2's output,
    updates the monday.com board, and returns the final status string.
    """
    if phase2_status == "In Compliance":
        final_status = "Approved"
        comment = (
            f"Compliance officer reviewed risk assessment "
            f"(risk level: {risk_level}, review track: {review_track}). "
            f"Client approved for onboarding."
        )
    elif phase2_status == "Out Of Compliance":
        final_status = "Rejected"
        comment = (
            f"Compliance officer reviewed risk assessment "
            f"(risk level: {risk_level}, review track: {review_track}). "
            f"Client rejected — compliance requirements not met."
        )
    else:  # "Need Information" — terminal in POC, no further action simulated
        final_status = phase2_status
        comment = (
            f"Compliance officer reviewed risk assessment. "
            f"Additional information required before a determination can be made. "
            f"Case remains open."
        )

    if item_id:
        try:
            monday.add_update(item_id, comment)
        except Exception as exc:
            logger.error("[Monday] Officer review update failed for item %s: %s", item_id, exc)

    return final_status


# ── Phase 2: compliance pipeline for a single application ─────────────────────

def process_application_phase2(
    application,
    extracted_data: Optional[ExtractedClientData],
    completeness_result: Optional[CompletenessCheckResult],
    monday_item_id: Optional[str],
    run_id: str,
    pydantic_failures: list,
    json_failures: list,
) -> PipelineResult | None:
    app_id = application.application_id
    console.rule(f"[bold cyan]{app_id} — {application.client_name}")
    total_start = time.time()

    if monday_item_id:
        try:
            monday.update_item_status(
                monday_item_id,
                "Pending Review",
                update_text="Compliance review in progress. Running risk assessment...",
            )
        except Exception as exc:
            logger.error("[Monday] Status update failed for %s: %s", app_id, exc)

    # ── Stage 1: Risk Assessment ───────────────────────────────────────────────
    # Production: client_data = client_hub_service.read_client_record(application_id)
    # POC: using Phase 1's extracted and validated data directly
    try:
        risk_assessment, s1_latency, s1_tokens = run_stage1(
            application=application,
            extracted_data=extracted_data,
            tool_dispatcher=tool_dispatcher if _ENABLE_TOOLS else None,
            pydantic_failures=pydantic_failures,
            json_failures=json_failures,
        )
    except Exception as exc:
        logger.error("[Stage1] FAILED for %s: %s", app_id, exc)
        return None

    # ── ComplianceOne screening ────────────────────────────────────────────────
    logger.info("[ComplianceOne] Submitting post-Stage-1 screening")
    screening = compliance_one.submit_screening({"client_name": application.client_name})
    if screening["sanctions_check"]["result"] != "no_match":
        logger.warning(
            "[ComplianceOne] Possible sanctions match for %s: %s",
            app_id, screening["sanctions_check"]["matched_list"],
        )

    # ── Stage 2: Onboarding Summary ────────────────────────────────────────────
    try:
        onboarding_summary, s2_latency, s2_tokens = run_stage2(
            application=application,
            risk_assessment=risk_assessment,
            pydantic_failures=pydantic_failures,
            json_failures=json_failures,
        )
    except Exception as exc:
        logger.error("[Stage2] FAILED for %s: %s", app_id, exc)
        return None

    mbf = onboarding_summary.monday_board_fields
    final_status = mbf.status
    _track = risk_assessment.recommended_review_track

    # ── Evaluation (optional) ──────────────────────────────────────────────────
    evaluation = None
    eval_latency = None
    eval_tokens = 0

    if RUN_EVALUATIONS:
        try:
            evaluation, eval_latency, eval_tokens = evaluate_application(
                application=application,
                risk_assessment=risk_assessment,
                onboarding_summary=onboarding_summary,
                extracted_data=extracted_data,
                completeness_result=completeness_result,
            )
        except Exception as exc:
            logger.error("[Evaluator] FAILED for %s: %s", app_id, exc)

    # ── Eval board write ───────────────────────────────────────────────────────
    monday_eval_item_id = None
    if evaluation:
        try:
            monday_eval_item_id = monday_eval.post_evaluation_result(
                client_id=app_id,
                client_name=application.client_name,
                ra_eval=evaluation.risk_assessment_evaluation,
                os_eval=evaluation.onboarding_summary_evaluation,
                processing_time_seconds=time.time() - total_start,
                ext_eval=evaluation.extraction_evaluation,
            )
        except Exception as exc:
            logger.error("[MondayEval] Failed for %s: %s", app_id, exc)

    # ── monday.com: final compliance columns + status ─────────────────────────
    try:
        _assigned_team = "Compliance" if _track in ("enhanced_due_diligence", "manual_escalation") else "Operations"
        monday.post_application_result(
            client_name=application.client_name,
            client_id=app_id,
            risk_level=risk_assessment.overall_risk_level,
            complexity_level=onboarding_summary.complexity_level,
            estimated_review_time=onboarding_summary.estimated_review_time,
            monday_status=final_status,
            monday_priority=mbf.priority,
            assigned_team=_assigned_team,
            tags=mbf.tags,
            risk_reasoning=risk_assessment.overall_reasoning,
            onboarding_summary_text=onboarding_summary.risk_summary,
            review_track=risk_assessment.recommended_review_track,
            aum=application.estimated_aum,
            submission_date=application.submission_date,
            existing_item_id=monday_item_id,
        )
    except Exception as exc:
        logger.error("[Monday] Final update failed for %s: %s", app_id, exc)

    if monday_item_id:
        try:
            monday.update_item_status(
                monday_item_id,
                final_status,
                update_text=(
                    f"Agent 2 compliance determination: {final_status}. "
                    f"Review track: {_track}. Risk level: {risk_assessment.overall_risk_level.upper()}."
                ),
            )
        except Exception as exc:
            logger.error("[Monday] Determination status update failed for %s: %s", app_id, exc)

        if onboarding_summary.blockers:
            try:
                body = "**Blockers**\n\n" + "\n\n".join(f"• {b}" for b in onboarding_summary.blockers)
                monday.add_update(monday_item_id, body)
            except Exception as exc:
                logger.error("[Monday] Blockers post failed for %s: %s", app_id, exc)

        if risk_assessment.compliance_flags:
            try:
                body = "**Compliance Flags**\n\n" + "\n\n".join(f"• {f}" for f in risk_assessment.compliance_flags)
                monday.add_update(monday_item_id, body)
            except Exception as exc:
                logger.error("[Monday] Compliance flags post failed for %s: %s", app_id, exc)

        if risk_assessment.missing_information:
            try:
                body = "**Missing Information**\n\n" + "\n\n".join(f"• {m}" for m in risk_assessment.missing_information)
                monday.add_update(monday_item_id, body)
            except Exception as exc:
                logger.error("[Monday] Missing information post failed for %s: %s", app_id, exc)

        if onboarding_summary.next_steps:
            try:
                lines = ["**Next Steps**\n"]
                for step in onboarding_summary.next_steps:
                    header = f"**{step.step_number}. [{step.priority.upper()}] — {step.owner.replace('_', ' ').title()}**"
                    entry = f"{header}\n{step.action}"
                    if step.depends_on:
                        entry += f"\n_Depends on: {step.depends_on}_"
                    lines.append(entry)
                monday.add_update(monday_item_id, "\n\n".join(lines))
            except Exception as exc:
                logger.error("[Monday] Next steps post failed for %s: %s", app_id, exc)

    # ── Simulated compliance officer review ────────────────────────────────────
    officer_status = _record_officer_determination(
        item_id=monday_item_id,
        phase2_status=final_status,
        risk_level=risk_assessment.overall_risk_level,
        review_track=_track,
    )

    # ── Downstream simulated services ──────────────────────────────────────────
    salesforce.update_opportunity_status(app_id, officer_status)
    outlook.send_notification(
        recipient="operations",
        subject=f"Onboarding Decision Ready — {application.client_name}",
        body=(
            f"{app_id}: Compliance determination={final_status}, Officer decision={officer_status}. "
            f"Risk={risk_assessment.overall_risk_level}, "
            f"Track={onboarding_summary.estimated_onboarding_track}, "
            f"Complexity={onboarding_summary.complexity_level}. "
            f"Review time: {onboarding_summary.estimated_review_time}."
        ),
    )
    if _track in ("enhanced_due_diligence", "manual_escalation"):
        outlook.send_notification(
            recipient="compliance_officer",
            subject=f"[ESCALATION] Enhanced Due Diligence Required — {application.client_name}",
            body=f"{app_id} flagged for EDD. Flags: {'; '.join(risk_assessment.compliance_flags[:3])}",
        )

    total_time = time.time() - total_start

    # Retrieve extraction metadata from DB record for metadata object
    s0_latency = None
    s0_tokens_val = None
    ops_review_applied = False
    review_action = completeness_result.recommended_action if completeness_result else None

    result = PipelineResult(
        client_application=application,
        risk_assessment=risk_assessment,
        onboarding_summary=onboarding_summary,
        evaluation=evaluation,
        extracted_data=extracted_data,
        completeness_result=completeness_result,
        pipeline_metadata=PipelineResultMetadata(
            total_processing_time_seconds=round(total_time, 2),
            pipeline_version=_PIPELINE_VERSION,
            stage0_latency_seconds=s0_latency,
            stage1_latency_seconds=round(s1_latency, 2),
            stage2_latency_seconds=round(s2_latency, 2),
            evaluation_latency_seconds=round(eval_latency, 2) if eval_latency else None,
            stage0_tokens=s0_tokens_val,
            stage1_tokens=s1_tokens,
            stage2_tokens=s2_tokens,
            evaluator_tokens=eval_tokens,
            tools_enabled=_ENABLE_TOOLS,
            monday_item_id=monday_item_id,
            monday_eval_item_id=monday_eval_item_id,
            extraction_confidence=extracted_data.overall_extraction_confidence if extracted_data else None,
            review_action=review_action,
            ops_review_applied=ops_review_applied,
        ),
    )

    _print_application_summary(result)
    return result


def _print_application_summary(result: PipelineResult) -> None:
    ra = result.risk_assessment
    os_ = result.onboarding_summary
    ev = result.evaluation
    ext = result.extracted_data
    cr = result.completeness_result

    if ext:
        action_label = cr.recommended_action if cr else "unknown"
        console.print(f"  [bold]Extraction Confidence:[/bold] {ext.overall_extraction_confidence:.0%}  |  Review Action: {action_label}")
    console.print(f"  [bold]Risk Level:[/bold] {ra.overall_risk_level.upper()}")
    console.print(f"  [bold]Review Track:[/bold] {ra.recommended_review_track}")
    console.print(f"  [bold]Complexity:[/bold] {os_.complexity_level}")
    console.print(f"  [bold]Est. Review Time:[/bold] {os_.estimated_review_time}")
    if ra.compliance_flags:
        console.print(f"  [bold yellow]Flags ({len(ra.compliance_flags)}):[/bold yellow]")
        for flag in ra.compliance_flags:
            console.print(f"    • {flag}")
    if os_.blockers:
        console.print(f"  [bold red]Blockers ({len(os_.blockers)}):[/bold red]")
        for bl in os_.blockers:
            console.print(f"    ⚠ {bl}")
    if ev:
        ra_score = ev.risk_assessment_evaluation.overall_assessment_quality.score
        os_score = ev.onboarding_summary_evaluation.overall_summary_quality.score
        ext_score = ev.extraction_evaluation.extraction_accuracy.score if ev.extraction_evaluation else None
        ext_str = f"  Ext={ext_score}/100" if ext_score is not None else ""
        console.print(f"  [bold]Eval Scores:[/bold] RA={ra_score}/100  Summary={os_score}/100{ext_str}")
    console.print()


# ── Database helpers ───────────────────────────────────────────────────────────

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
    session, phase1_run_id: str, phase2_run_id: str, result: PipelineResult
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


# ── Aggregate metrics ──────────────────────────────────────────────────────────

def compute_metrics(results: list[PipelineResult], failed_count: int, pv_failures: list, jf_failures: list) -> PipelineMetrics:
    risk_dist: Counter = Counter()
    track_dist: Counter = Counter()
    flag_freq: Counter = Counter()
    missing_freq: Counter = Counter()
    ra_scores: list[float] = []
    os_scores: list[float] = []
    s1_latencies: list[float] = []
    s2_latencies: list[float] = []
    eval_latencies: list[float] = []
    total_tokens = 0
    critical_issue_count = 0
    escalated = 0

    for r in results:
        risk_dist[r.risk_assessment.overall_risk_level] += 1
        track = r.risk_assessment.recommended_review_track
        track_dist[track] += 1
        if track in ("enhanced_due_diligence", "manual_escalation"):
            escalated += 1
        for flag in r.risk_assessment.compliance_flags:
            flag_freq[flag[:80]] += 1
        for mi in r.risk_assessment.missing_information:
            missing_freq[mi[:80]] += 1
        pm = r.pipeline_metadata
        s1_latencies.append(pm.stage1_latency_seconds)
        s2_latencies.append(pm.stage2_latency_seconds)
        if pm.evaluation_latency_seconds:
            eval_latencies.append(pm.evaluation_latency_seconds)
        total_tokens += (pm.stage0_tokens or 0) + (pm.stage1_tokens or 0) + (pm.stage2_tokens or 0) + (pm.evaluator_tokens or 0)
        if r.evaluation:
            ra_scores.append(r.evaluation.risk_assessment_evaluation.overall_assessment_quality.score)
            os_scores.append(r.evaluation.onboarding_summary_evaluation.overall_summary_quality.score)
            if r.evaluation.risk_assessment_evaluation.critical_issues:
                critical_issue_count += 1

    total = len(results)
    avg = lambda lst: round(sum(lst) / len(lst), 2) if lst else 0.0

    return PipelineMetrics(
        total_applications_processed=total,
        total_applications_failed=failed_count,
        risk_distribution=dict(risk_dist),
        review_track_distribution=dict(track_dist),
        escalation_rate=round(escalated / total, 3) if total else 0.0,
        average_risk_assessment_score=avg(ra_scores) if ra_scores else None,
        average_summary_score=avg(os_scores) if os_scores else None,
        pydantic_validation_failures=len(pv_failures),
        json_parse_failures=len(jf_failures),
        compliance_flags_frequency=dict(flag_freq.most_common(20)),
        missing_information_frequency=dict(missing_freq.most_common(20)),
        average_processing_time_seconds=avg([r.pipeline_metadata.total_processing_time_seconds for r in results]),
        stage1_avg_latency_seconds=avg(s1_latencies),
        stage2_avg_latency_seconds=avg(s2_latencies),
        evaluation_avg_latency_seconds=avg(eval_latencies) if eval_latencies else None,
        total_estimated_tokens=total_tokens,
        applications_with_critical_issues=critical_issue_count,
    )


def print_metrics_table(metrics: PipelineMetrics) -> None:
    console.rule("[bold green]Pipeline Run Summary")

    t = Table(box=box.ROUNDED, show_header=True, header_style="bold magenta")
    t.add_column("Metric", style="cyan")
    t.add_column("Value", justify="right")
    t.add_row("Applications processed", str(metrics.total_applications_processed))
    t.add_row("Applications failed", str(metrics.total_applications_failed))
    t.add_row("Escalation rate", f"{metrics.escalation_rate:.0%}")
    t.add_row("Pydantic validation failures", str(metrics.pydantic_validation_failures))
    t.add_row("JSON parse failures", str(metrics.json_parse_failures))
    t.add_row("Applications w/ critical eval issues", str(metrics.applications_with_critical_issues))
    if metrics.average_risk_assessment_score:
        t.add_row("Avg risk assessment score (0-100)", f"{metrics.average_risk_assessment_score:.1f}")
    if metrics.average_summary_score:
        t.add_row("Avg onboarding summary score (0-100)", f"{metrics.average_summary_score:.1f}")
    t.add_row("Avg processing time / application", f"{metrics.average_processing_time_seconds:.1f}s")
    t.add_row("Stage 1 avg latency", f"{metrics.stage1_avg_latency_seconds:.1f}s")
    t.add_row("Stage 2 avg latency", f"{metrics.stage2_avg_latency_seconds:.1f}s")
    if metrics.evaluation_avg_latency_seconds:
        t.add_row("Evaluator avg latency", f"{metrics.evaluation_avg_latency_seconds:.1f}s")
    t.add_row("Total tokens used (est.)", f"{metrics.total_estimated_tokens:,}")
    console.print(t)

    console.print("\n[bold]Risk Distribution:[/bold]")
    for level, count in sorted(metrics.risk_distribution.items()):
        bar = "█" * count
        console.print(f"  {level:10s} {bar} ({count})")

    console.print("\n[bold]Review Track Distribution:[/bold]")
    for track, count in sorted(metrics.review_track_distribution.items()):
        console.print(f"  {track:30s} {count}")

    if metrics.compliance_flags_frequency:
        console.print("\n[bold]Top Compliance Flags:[/bold]")
        for flag, cnt in list(metrics.compliance_flags_frequency.items())[:5]:
            console.print(f"  [{cnt}x] {flag}")


def _format_metrics_text(metrics: PipelineMetrics, run_id: str) -> str:
    lines = [
        f"**Pipeline Run Summary — Run {run_id[:8]}...**\n",
        f"- Applications processed: {metrics.total_applications_processed}",
        f"- Applications failed: {metrics.total_applications_failed}",
        f"- Escalation rate: {metrics.escalation_rate:.0%}",
        f"- Pydantic failures: {metrics.pydantic_validation_failures}",
        f"- JSON parse failures: {metrics.json_parse_failures}",
    ]
    if metrics.average_risk_assessment_score:
        lines.append(f"- Avg risk assessment score: {metrics.average_risk_assessment_score:.1f}/100")
    if metrics.average_summary_score:
        lines.append(f"- Avg summary score: {metrics.average_summary_score:.1f}/100")
    lines += [
        f"- Avg processing time: {metrics.average_processing_time_seconds:.1f}s",
        f"- Total tokens (est.): {metrics.total_estimated_tokens:,}",
        f"- Risk distribution: {', '.join(f'{k}: {v}' for k, v in sorted(metrics.risk_distribution.items()))}",
    ]
    return "\n".join(lines)


# ── Phase 1 entrypoint ─────────────────────────────────────────────────────────

def run_phase1(client_id: str | None = None) -> None:
    console.rule("[bold blue]Crestview — Phase 1: Document Extraction (Agent 1)")
    console.print(f"  Model: [cyan]{_MODEL}[/cyan]  |  Tools: [cyan]{_ENABLE_TOOLS}[/cyan]\n")

    create_all_tables()

    run_id = str(uuid.uuid4())
    run_dir = create_run_directory(run_id)
    console.print(f"  Run ID: [bold yellow]{run_id}[/bold yellow]")
    console.print(f"  Output: [dim]{run_dir}[/dim]\n")

    applications = load_applications(CSV_PATH)
    if client_id:
        applications = [a for a in applications if a.application_id == client_id]
        if not applications:
            console.print(f"[red]No application found with ID '{client_id}'[/red]")
            sys.exit(1)
    console.print(f"[green]Loaded {len(applications)} client application(s) from CSV[/green]\n")

    pydantic_failures: list[str] = []
    json_failures: list[str] = []
    phase1_count = 0
    failed_count = 0

    with get_session() as session:
        run_record = PipelineRunRecord(
            run_id=run_id,
            started_at=datetime.now(timezone.utc),
            total_applications=len(applications),
            pipeline_version=_PIPELINE_VERSION,
            model_used=_MODEL,
            tools_enabled=_ENABLE_TOOLS,
            phase=1,
        )
        save_pipeline_run(session, run_record)

        for application in applications:
            app_id = application.application_id
            console.rule(f"[bold cyan]{app_id} — {application.client_name}")

            source_docs = load_documents(app_id)

            # Create monday board item with "New" status and populate CSV-sourced columns
            monday_item_id: Optional[str] = None
            try:
                item_name = f"{application.client_name} ({app_id})"
                monday_item_id = monday.create_application_item(item_name, "New")
                if monday_item_id:
                    monday.populate_csv_columns(
                        monday_item_id,
                        client_type=application.client_type,
                        aum=application.estimated_aum,
                        submission_date=application.submission_date,
                    )
            except Exception as exc:
                logger.error("[Monday] Failed to create item for %s: %s", app_id, exc)

            try:
                extracted_data, completeness_result, s0_latency, s0_tokens, corrections, ops_review_applied = run_agent1(
                    application=application,
                    monday_item_id=monday_item_id,
                    pydantic_failures=pydantic_failures,
                    json_failures=json_failures,
                )

                _save_phase1_to_db(
                    session=session,
                    run_id=run_id,
                    application=application,
                    monday_item_id=monday_item_id,
                    extracted_data=extracted_data,
                    completeness_result=completeness_result,
                    s0_latency=s0_latency,
                    s0_tokens=s0_tokens,
                    corrections=corrections,
                    ops_review_applied=ops_review_applied,
                )

                write_phase1_files(
                    run_dir=run_dir,
                    application=application,
                    extracted_data=extracted_data,
                    completeness_result=completeness_result,
                    source_documents=source_docs,
                )

                if extracted_data:
                    action = completeness_result.recommended_action if completeness_result else "unknown"
                    conf = extracted_data.overall_extraction_confidence
                    console.print(
                        f"  [bold]Extraction Confidence:[/bold] {conf:.0%}  |  "
                        f"Action: {action}  |  "
                        f"monday item: [dim]{monday_item_id or 'N/A'}[/dim]"
                    )
                phase1_count += 1

            except Exception as exc:
                logger.error("Phase 1 error for %s: %s", app_id, exc)
                failed_count += 1
                write_failed_application_files(run_dir, application, str(exc))
                _save_failure_to_db(session, run_id, application, str(exc))

        update_pipeline_run(
            session,
            run_id,
            completed_at=datetime.now(timezone.utc),
            successful=phase1_count,
            failed=failed_count,
            phase=1,
        )

    update_latest_symlink(run_dir)

    console.rule("[bold green]Phase 1 Complete")
    console.print(f"\n  [bold]Applications extracted:[/bold] {phase1_count}")
    console.print(f"  [bold]Failed:[/bold] {failed_count}")
    console.print(f"  [bold]Run ID:[/bold] [bold yellow]{run_id}[/bold yellow]")
    console.print(f"  [bold]Output:[/bold] [dim]{run_dir}[/dim]\n")
    console.print("[bold cyan]Next steps:[/bold cyan]")
    console.print("  1. Review the monday.com board — items are now in 'Ready to Onboard' status.")
    console.print("  2. Any item you want to [bold red]block[/bold red] from Phase 2: change its status to anything other than 'Ready to Onboard'.")
    console.print("  3. When ready, run Phase 2:\n")
    console.print("     [bold green]python main.py --phase=2[/bold green]\n")


# ── Phase 2 entrypoint ─────────────────────────────────────────────────────────

def run_phase2(client_id: str | None = None) -> None:
    console.rule("[bold blue]Crestview — Phase 2: Compliance Review (Agent 2)")
    console.print(f"  Model: [cyan]{_MODEL}[/cyan]  |  Evaluations: [cyan]{RUN_EVALUATIONS}[/cyan]\n")

    create_all_tables()

    run_id = str(uuid.uuid4())
    run_dir = create_run_directory(run_id)
    console.print(f"  Run ID: [bold yellow]{run_id}[/bold yellow]")
    console.print(f"  Output: [dim]{run_dir}[/dim]\n")

    monday_configured = bool(monday.api_key and monday.board_id)

    with get_session() as session:
        if monday_configured:
            console.print("[dim]Querying Monday board for 'Ready to Onboard' items...[/dim]")
            ready_items = monday.get_items_by_status("Ready to Onboard")
            if not ready_items:
                console.print("[bold yellow]No items with 'Ready to Onboard' status found on Monday board.[/bold yellow]")
                return
            item_ids = [item["id"] for item in ready_items]
            console.print(f"[green]Found {len(item_ids)} 'Ready to Onboard' item(s) on Monday.[/green]")
            eligible_records = get_phase1_records_by_monday_ids(session, item_ids)
        else:
            eligible_records = get_unprocessed_phase1_records(session)

    if not eligible_records:
        if monday_configured:
            console.print("[bold yellow]No Phase 1 DB records found for the Ready to Onboard items.[/bold yellow]")
            console.print("[dim]Ensure Phase 1 has been run for these applications.[/dim]")
        else:
            console.print("[bold yellow]No unprocessed Phase 1 records found. Run Phase 1 first.[/bold yellow]")
        return

    if client_id:
        eligible_records = [r for r in eligible_records if r.application_id == client_id]
        if not eligible_records:
            console.print(f"[red]No eligible Phase 1 record found for client ID '{client_id}'[/red]")
            sys.exit(1)

    console.print(f"[green]{len(eligible_records)} application(s) eligible for Phase 2.[/green]\n")

    with get_session() as session:
        save_pipeline_run(session, PipelineRunRecord(
            run_id=run_id,
            started_at=datetime.now(timezone.utc),
            total_applications=len(eligible_records),
            pipeline_version=_PIPELINE_VERSION,
            model_used=_MODEL,
            tools_enabled=_ENABLE_TOOLS,
            phase=2,
        ))

    all_applications = {app.application_id: app for app in load_applications(CSV_PATH)}

    results: list[PipelineResult] = []
    pydantic_failures: list[str] = []
    json_failures: list[str] = []
    failed_count = 0

    with get_session() as session:
        for record in eligible_records:
            application = all_applications.get(record.application_id)
            if not application:
                logger.error("Application %s not found in CSV — skipping", record.application_id)
                failed_count += 1
                continue

            extracted_data: Optional[ExtractedClientData] = None
            completeness_result: Optional[CompletenessCheckResult] = None
            try:
                if record.extraction_json:
                    extracted_data = ExtractedClientData.model_validate_json(record.extraction_json)
                if record.completeness_json:
                    completeness_result = CompletenessCheckResult.model_validate_json(record.completeness_json)
            except Exception as exc:
                logger.warning("[Phase2] Could not deserialize extraction data for %s: %s", record.application_id, exc)

            try:
                result = process_application_phase2(
                    application=application,
                    extracted_data=extracted_data,
                    completeness_result=completeness_result,
                    monday_item_id=record.monday_item_id,
                    run_id=run_id,
                    pydantic_failures=pydantic_failures,
                    json_failures=json_failures,
                )

                if result:
                    results.append(result)
                    write_application_files(run_dir, result)
                    _update_phase2_in_db(session, record.run_id, run_id, result)
                else:
                    failed_count += 1
                    write_failed_application_files(run_dir, application, "Phase 2 pipeline returned no result")
                    _save_failure_to_db(session, run_id, application, "Phase 2 pipeline returned no result")

            except Exception as exc:
                logger.error("Phase 2 error for %s: %s", record.application_id, exc)
                failed_count += 1
                write_failed_application_files(run_dir, application, str(exc))
                _save_failure_to_db(session, run_id, application, str(exc))

        if not results:
            console.print("[bold red]No applications were successfully processed in Phase 2.[/bold red]")
            return

        metrics = compute_metrics(results, failed_count, pydantic_failures, json_failures)
        print_metrics_table(metrics)

        total_run_time = sum(r.pipeline_metadata.total_processing_time_seconds for r in results)
        update_pipeline_run(
            session,
            run_id,
            completed_at=datetime.now(timezone.utc),
            successful=len(results),
            failed=failed_count,
            escalation_rate=metrics.escalation_rate,
            avg_risk_assessment_score=metrics.average_risk_assessment_score or 0.0,
            avg_summary_score=metrics.average_summary_score or 0.0,
            total_processing_time_seconds=total_run_time,
            phase=2,
        )

    try:
        monday_eval.post_eval_summary_update(
            total_processed=metrics.total_applications_processed,
            avg_ra_score=metrics.average_risk_assessment_score,
            avg_os_score=metrics.average_summary_score,
            escalation_rate=metrics.escalation_rate,
            risk_distribution=metrics.risk_distribution,
            critical_issue_count=metrics.applications_with_critical_issues,
            total_pipeline_time=sum(r.pipeline_metadata.total_processing_time_seconds for r in results),
            total_tokens=metrics.total_estimated_tokens,
        )
    except Exception as exc:
        logger.error("[MondayEval] Failed to post eval summary update: %s", exc)

    write_run_summary(run_dir, run_id, metrics, results)
    update_latest_symlink(run_dir)

    output_path = OUTPUTS_DIR / "evaluation_results.json"
    output_data = {
        "run_id": run_id,
        "phase": 2,
        "pipeline_metrics": metrics.model_dump(),
        "applications": [
            {
                "client_id": r.client_application.application_id,
                "client_name": r.client_application.client_name,
                "extraction_confidence": r.pipeline_metadata.extraction_confidence,
                "review_action": r.pipeline_metadata.review_action,
                "risk_assessment": r.risk_assessment.model_dump(),
                "onboarding_summary": r.onboarding_summary.model_dump(),
                "evaluation": r.evaluation.model_dump() if r.evaluation else None,
                "pipeline_metadata": r.pipeline_metadata.model_dump(),
            }
            for r in results
        ],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, default=str)

    console.rule("[bold green]Phase 2 Complete")
    console.print(f"\n  [bold]Applications processed:[/bold] {len(results)}")
    console.print(f"  [bold]Failed:[/bold] {failed_count}")
    console.print(f"  [bold]Full results:[/bold] [dim]{output_path}[/dim]")
    console.print(f"  [bold]Run output:[/bold] [dim]{run_dir}[/dim]")
    console.print(f"  [bold]Database:[/bold] [dim]{os.getenv('DATABASE_PATH', 'data/crestview_pipeline.db')}[/dim]\n")


# ── Demo utilities ─────────────────────────────────────────────────────────────

def clear_database() -> None:
    """Delete the SQLite database file and recreate empty tables."""
    db_path = Path(os.getenv("DATABASE_PATH", "data/crestview_pipeline.db"))
    if db_path.exists():
        db_path.unlink()
        console.print(f"[green]Deleted:[/green] {db_path}")
    else:
        console.print(f"[dim]No database found at {db_path} — nothing to delete.[/dim]")
    create_all_tables()
    console.print("[bold green]Database cleared. Ready for a fresh demo run.[/bold green]")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crestview Capital Group — AI Onboarding Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py --phase=1\n"
            "  python main.py --phase=2\n"
            "  python main.py --clear-db\n"
        ),
    )
    parser.add_argument(
        "--phase",
        type=int,
        choices=[1, 2],
        default=1,
        help="Pipeline phase: 1 = Agent 1 (extraction), 2 = Agent 2 (compliance). Default: 1",
    )
    parser.add_argument(
        "--client-id",
        type=str,
        default=None,
        help="Process only the application with this ID (works for both phases)",
    )
    parser.add_argument(
        "--clear-db",
        action="store_true",
        help="Delete the database and recreate empty tables (demo reset)",
    )
    args = parser.parse_args()

    if args.clear_db:
        clear_database()
    elif args.phase == 1:
        run_phase1(client_id=args.client_id)
    elif args.phase == 2:
        run_phase2(client_id=args.client_id)


if __name__ == "__main__":
    main()
