from __future__ import annotations

import logging
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console

from models.extraction import ExtractedClientData, CompletenessCheckResult
from persistence import create_all_tables, get_session
from persistence.models import PipelineRunRecord
from persistence.operations import _save_failure_to_db, _save_phase1_to_db
from persistence.repository import save_pipeline_run, update_pipeline_run
from services import ClientHubService, MondayService, OutlookService
from src.completeness_checker import check_completeness
from src.corrections import _apply_client_corrections, _apply_ops_corrections
from src.data_loader import load_applications
from src.document_loader import load_documents
from src.output_writer import (
    create_run_directory,
    update_latest_symlink,
    write_failed_application_files,
    write_phase1_files,
)
from models.events import PipelineEvent
from src.pipeline import _ENABLE_TOOLS, _MODEL, _PIPELINE_VERSION, run_stage0

logger = logging.getLogger("crestview.pipeline")
console = Console()

_CSV_PATH = Path(__file__).parent.parent / "crestview_client_applications.csv"
_OUTPUTS_DIR = Path(os.getenv("OUTPUT_DIR", "outputs"))
_OUTPUTS_DIR.mkdir(exist_ok=True)

client_hub = ClientHubService()
outlook = OutlookService()
monday = MondayService()


def _to_client_hub_format(application, extracted: ExtractedClientData) -> dict:
    data = application.model_dump()
    data["extraction_confidence"] = extracted.overall_extraction_confidence
    data["extracted_client_name"] = extracted.client_name.extracted_value
    data["extracted_entity_type"] = extracted.entity_type.extracted_value
    data["extracted_jurisdiction"] = extracted.jurisdiction.extracted_value
    data["extracted_source_of_funds"] = extracted.source_of_funds.extracted_value
    return data


def run_agent1(
    application,
    monday_item_id: Optional[str],
    pydantic_failures: list,
    json_failures: list,
    on_event: Optional[Callable[[PipelineEvent], None]] = None,
) -> tuple[Optional[ExtractedClientData], Optional[CompletenessCheckResult], float, int, dict, bool]:
    """
    Full Agent 1 flow: load docs → Stage 0 extraction → completeness check → routing.
    Returns (extracted_data, completeness_result, latency, tokens, corrections, ops_review_applied).
    """
    app_id = application.application_id
    corrections: dict = {}
    ops_review_applied = False

    def _ev(event_type: str, stage: str, data: dict | None = None) -> None:
        if on_event:
            on_event(PipelineEvent(event=event_type, application_id=app_id, stage=stage, data=data or {}))

    _ev("stage_started", "stage0")

    documents = load_documents(app_id)
    if not documents:
        logger.warning("[Agent1] No documents found for %s — skipping Stage 0", app_id)
        if monday_item_id:
            monday.update_item_status(
                monday_item_id,
                "Need Information",
                f"No documents were found for application {app_id}. "
                "Please upload the required onboarding documents"
                "so that extraction can proceed.",
            )
        _ev("stage_completed", "stage0", {"confidence": 0.0, "action": "no_documents"})
        return None, None, 0.0, 0, corrections, ops_review_applied

    doc_names = [d.filename for d in documents]

    if monday_item_id:
        monday.add_update(
            monday_item_id,
            f"Beginning document extraction. {len(documents)} document(s) loaded for {app_id}: {', '.join(doc_names)}",
        )

    logger.info("[Stage0] Calling LLM — extracting from %d document(s) for %s...", len(documents), app_id)
    try:
        extracted, s0_latency, s0_tokens = run_stage0(
            documents=documents,
            application_id=app_id,
            pydantic_failures=pydantic_failures,
            json_failures=json_failures,
        )
    except Exception as exc:
        logger.error("[Stage0] FAILED for %s: %s", app_id, exc)
        _ev("error", "pipeline", {"message": "Stage 0 extraction failed"})
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
    _ev("stage_completed", "stage0", {
        "confidence": round(extracted.overall_extraction_confidence, 2),
        "action": action,
    })
    return extracted, completeness, s0_latency, s0_tokens, corrections, ops_review_applied


def run_phase1(
    client_id: str | None = None,
    max_applications: int | None = None,
    on_event: Optional[Callable[[PipelineEvent], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> None:
    console.rule("[bold blue]Crestview — Phase 1: Document Extraction (Agent 1)")
    console.print(f"  Model: [cyan]{_MODEL}[/cyan]  |  Tools: [cyan]{_ENABLE_TOOLS}[/cyan]\n")

    create_all_tables()

    run_id = str(uuid.uuid4())
    run_dir = create_run_directory(run_id)
    console.print(f"  Run ID: [bold yellow]{run_id}[/bold yellow]")
    console.print(f"  Output: [dim]{run_dir}[/dim]\n")

    applications = load_applications(_CSV_PATH)
    if client_id:
        applications = [a for a in applications if a.application_id == client_id]
        if not applications:
            console.print(f"[red]No application found with ID '{client_id}'[/red]")
            if on_event:
                on_event(PipelineEvent(event="error", application_id="pipeline", stage="pipeline",
                                       data={"message": f"No application found with ID '{client_id}'"}))
                on_event(PipelineEvent(event="run_complete", application_id="pipeline", stage="pipeline",
                                       data={"applications_processed": 0}))
                return
            sys.exit(1)
    if max_applications:
        applications = applications[:max_applications]
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
            if stop_event and stop_event.is_set():
                break
            app_id = application.application_id
            console.rule(f"[bold cyan]{app_id} — {application.client_name}")

            source_docs = load_documents(app_id)

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
                    on_event=on_event,
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

    if on_event:
        on_event(PipelineEvent(
            event="run_complete", application_id="pipeline", stage="pipeline",
            data={"applications_processed": phase1_count},
        ))

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
