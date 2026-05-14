from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console

from models.extraction import ExtractedClientData, CompletenessCheckResult
from models.pipeline import PipelineResult, PipelineResultMetadata
from persistence import create_all_tables, get_session
from persistence.models import PipelineRunRecord
from persistence.operations import _save_failure_to_db, _update_phase2_in_db
from persistence.repository import (
    get_phase1_records_by_monday_ids,
    get_unprocessed_phase1_records,
    save_pipeline_run,
    update_pipeline_run,
)
from services import (
    ComplianceOneService,
    MondayEvalService,
    MondayService,
    OutlookService,
    SalesforceService,
    SharePointService,
)
from src.data_loader import load_applications
from src.evaluator import evaluate_application
from src.metrics import _print_application_summary, compute_metrics, print_metrics_table
from src.output_writer import (
    create_run_directory,
    update_latest_symlink,
    write_application_files,
    write_failed_application_files,
    write_run_summary,
)
from models.events import PipelineEvent
from src.pipeline import _ENABLE_TOOLS, _MODEL, _PIPELINE_VERSION, run_stage1, run_stage2
from src.tools import ToolDispatcher

logger = logging.getLogger("crestview.pipeline")
console = Console()

_CSV_PATH = Path(__file__).parent.parent / "crestview_client_applications.csv"
_OUTPUTS_DIR = Path(os.getenv("OUTPUT_DIR", "outputs"))
_OUTPUTS_DIR.mkdir(exist_ok=True)

RUN_EVALUATIONS = os.getenv("RUN_EVALUATIONS", "true").lower() == "true"
ENABLE_ROUTING = os.getenv("ENABLE_ROUTING", "true").lower() == "true"

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


def _record_officer_determination(
    item_id: Optional[str],
    phase2_status: str,
    risk_level: str,
    review_track: str,
) -> str:
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
    else:
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


def process_application_phase2(
    application,
    extracted_data: Optional[ExtractedClientData],
    completeness_result: Optional[CompletenessCheckResult],
    monday_item_id: Optional[str],
    run_id: str,
    pydantic_failures: list,
    json_failures: list,
    on_event: Optional[Callable[[PipelineEvent], None]] = None,
) -> PipelineResult | None:
    app_id = application.application_id
    console.rule(f"[bold cyan]{app_id} — {application.client_name}")
    total_start = time.time()

    def _ev(event_type: str, stage: str, data: dict | None = None) -> None:
        if on_event:
            on_event(PipelineEvent(event=event_type, application_id=app_id, stage=stage, data=data or {}))

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
    _ev("stage_started", "stage1")

    def _cot_cb(thinking: str) -> None:
        _ev("cot_block", "stage1", {"thinking": thinking})

    logger.info("[Stage1] Calling LLM — risk assessment for %s...", app_id)
    try:
        risk_assessment, s1_latency, s1_tokens = run_stage1(
            application=application,
            extracted_data=extracted_data,
            tool_dispatcher=tool_dispatcher if _ENABLE_TOOLS else None,
            pydantic_failures=pydantic_failures,
            json_failures=json_failures,
            on_thinking=_cot_cb if on_event else None,
        )
    except Exception as exc:
        logger.error("[Stage1] FAILED for %s: %s", app_id, exc)
        _ev("error", "pipeline", {"message": "Stage 1 risk assessment failed"})
        return None

    _ev("stage_completed", "stage1", {
        "risk_level": risk_assessment.overall_risk_level,
        "review_track": risk_assessment.recommended_review_track,
        "flags": len(risk_assessment.compliance_flags),
    })

    logger.info("[ComplianceOne] Submitting post-Stage-1 screening")
    screening = compliance_one.submit_screening({"client_name": application.client_name})
    if screening["sanctions_check"]["result"] != "no_match":
        logger.warning(
            "[ComplianceOne] Possible sanctions match for %s: %s",
            app_id, screening["sanctions_check"]["matched_list"],
        )

    # ── Stage 2: Onboarding Summary ────────────────────────────────────────────
    _ev("stage_started", "stage2")
    logger.info("[Stage2] Calling LLM — onboarding summary for %s...", app_id)
    try:
        onboarding_summary, s2_latency, s2_tokens = run_stage2(
            application=application,
            risk_assessment=risk_assessment,
            pydantic_failures=pydantic_failures,
            json_failures=json_failures,
        )
    except Exception as exc:
        logger.error("[Stage2] FAILED for %s: %s", app_id, exc)
        _ev("error", "pipeline", {"message": "Stage 2 onboarding summary failed"})
        return None

    _ev("stage_completed", "stage2", {
        "complexity": onboarding_summary.complexity_level,
        "track": onboarding_summary.estimated_onboarding_track,
    })

    mbf = onboarding_summary.monday_board_fields
    final_status = mbf.status
    _track = risk_assessment.recommended_review_track

    # ── Evaluation (optional) ──────────────────────────────────────────────────
    evaluation = None
    eval_latency = None
    eval_tokens = 0

    if RUN_EVALUATIONS:
        _ev("stage_started", "evaluator")
        try:
            evaluation, eval_latency, eval_tokens = evaluate_application(
                application=application,
                risk_assessment=risk_assessment,
                onboarding_summary=onboarding_summary,
                extracted_data=extracted_data,
                completeness_result=completeness_result,
            )
            ra_eval = evaluation.risk_assessment_evaluation
            os_eval = evaluation.onboarding_summary_evaluation
            _ev("eval_score", "evaluator", {
                "ra_overall": ra_eval.overall_assessment_quality.score,
                "os_overall": os_eval.overall_summary_quality.score,
                "ra_reasoning": ra_eval.reasoning_quality.score,
                "ra_completeness": ra_eval.reasoning_completeness.score,
            })
        except Exception as exc:
            logger.error("[Evaluator] FAILED for %s: %s", app_id, exc)

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

    officer_status = _record_officer_determination(
        item_id=monday_item_id,
        phase2_status=final_status,
        risk_level=risk_assessment.overall_risk_level,
        review_track=_track,
    )

    if ENABLE_ROUTING:
        _ev("stage_started", "orchestrator")
        try:
            from src.orchestrator import route_decision
            routing = route_decision(
                risk_assessment_json=risk_assessment.model_dump_json(),
                onboarding_summary_json=onboarding_summary.model_dump_json(),
                eval_json=evaluation.model_dump_json() if evaluation else "{}",
            )
            _ev("routing_decision", "orchestrator", {
                "decision": routing.decision,
                "confidence": routing.confidence,
                "rationale": routing.rationale,
                "reviewer_team": routing.reviewer_team,
                "conditions": routing.conditions,
            })
            _ev("stage_completed", "orchestrator", {"decision": routing.decision})
            console.print(
                f"  [bold]Routing:[/bold] [cyan]{routing.decision}[/cyan] "
                f"(confidence {routing.confidence:.0%}) → {routing.reviewer_team}"
            )
            if routing.conditions:
                for c in routing.conditions:
                    console.print(f"    [yellow]Condition:[/yellow] {c}")
        except Exception as exc:
            logger.error("[Orchestrator] Routing decision failed for %s: %s", app_id, exc)
        else:
            if routing.decision:
                try:
                    from src.reviewer_brief import generate_reviewer_brief
                    brief = generate_reviewer_brief(
                        risk_assessment_json=risk_assessment.model_dump_json(),
                        onboarding_summary_json=onboarding_summary.model_dump_json(),
                        routing_decision_json=routing.model_dump_json(),
                    )
                    logger.info("[ReviewerBrief] %s — brief generated", app_id)
                    _ev("reviewer_brief", "reviewer_brief", brief.model_dump())
                    if monday_item_id:
                        brief_md = (
                            f"**Summary:** {brief.executive_summary}\n\n"
                            f"**Risk Factors:**\n"
                            + "\n".join(f"- {r}" for r in brief.primary_risk_factors)
                            + "\n\n**Verification Checklist:**\n"
                            + "\n".join(f"- [ ] {c}" for c in brief.verification_checklist)
                            + "\n\n**Next Steps:**\n"
                            + "\n".join(f"- {s}" for s in brief.suggested_next_steps)
                            + f"\n\n**Regulatory Notes:** {brief.regulatory_notes}"
                        )
                        submitted = monday.change_column_value(monday_item_id, "Reviewer Brief", brief_md)
                        if not submitted:
                            monday.add_update(monday_item_id, f"**Reviewer Brief:**\n\n{brief_md}")
                except Exception as exc:
                    logger.error("[ReviewerBrief] Failed for %s: %s", app_id, exc, exc_info=True)
                    _ev("error", "reviewer_brief", {"message": f"Reviewer brief generation failed: {exc}"})

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


def run_phase2(
    client_id: str | None = None,
    max_applications: int | None = None,
    on_event: Optional[Callable[[PipelineEvent], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> None:
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
        if on_event:
            on_event(PipelineEvent(
                event="error", application_id="pipeline", stage="pipeline",
                data={"message": "No Phase 1 records found. Run Phase 1 first."},
            ))
            on_event(PipelineEvent(
                event="run_complete", application_id="pipeline", stage="pipeline",
                data={"applications_processed": 0},
            ))
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
            if on_event:
                on_event(PipelineEvent(event="error", application_id="pipeline", stage="pipeline",
                                       data={"message": f"No Phase 1 record found for '{client_id}'. Run Phase 1 first."}))
                on_event(PipelineEvent(event="run_complete", application_id="pipeline", stage="pipeline",
                                       data={"applications_processed": 0}))
                return
            sys.exit(1)

    if max_applications:
        eligible_records = eligible_records[:max_applications]

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

    all_applications = {app.application_id: app for app in load_applications(_CSV_PATH)}

    results: list[PipelineResult] = []
    pydantic_failures: list[str] = []
    json_failures: list[str] = []
    failed_count = 0

    with get_session() as session:
        for record in eligible_records:
            if stop_event and stop_event.is_set():
                break
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
                    on_event=on_event,
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
            if on_event:
                on_event(PipelineEvent(
                    event="run_complete", application_id="pipeline", stage="pipeline",
                    data={"applications_processed": 0},
                ))
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

    if on_event:
        on_event(PipelineEvent(
            event="run_complete", application_id="pipeline", stage="pipeline",
            data={"applications_processed": len(results)},
        ))

    output_path = _OUTPUTS_DIR / "evaluation_results.json"
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
