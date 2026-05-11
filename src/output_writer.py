"""
Per-run and per-application file output writer (Fix 5).
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import shutil as _shutil

from models.client import ClientApplication
from models.extraction import CompletenessCheckResult, DocumentInput, ExtractedClientData
from models.pipeline import PipelineMetrics, PipelineResult


def _output_root() -> Path:
    return Path(os.getenv("OUTPUT_DIR", "outputs"))


def create_run_directory(run_id: str) -> Path:
    run_dir = _output_root() / "runs" / run_id
    (run_dir / "applications").mkdir(parents=True, exist_ok=True)
    (run_dir / "failed").mkdir(parents=True, exist_ok=True)
    return run_dir


def write_application_files(
    run_dir: Path,
    result: PipelineResult,
    source_documents: Optional[list[DocumentInput]] = None,
) -> None:
    app_id = result.client_application.application_id
    app_dir = run_dir / "applications" / app_id
    app_dir.mkdir(parents=True, exist_ok=True)

    _write_json(app_dir / "input.json", result.client_application.model_dump())
    _write_json(app_dir / "risk_assessment.json", result.risk_assessment.model_dump())
    _write_json(app_dir / "onboarding_summary.json", result.onboarding_summary.model_dump())

    if result.extracted_data:
        _write_json(app_dir / "extraction.json", result.extracted_data.model_dump())
    if result.completeness_result:
        _write_json(app_dir / "completeness_check.json", result.completeness_result.model_dump())

    if result.evaluation:
        _write_json(app_dir / "evaluation.json", result.evaluation.model_dump())

    if source_documents:
        docs_dir = app_dir / "documents"
        docs_dir.mkdir(exist_ok=True)
        for doc in source_documents:
            dest = docs_dir / doc.filename
            try:
                _shutil.copy2(doc.file_path, dest)
            except OSError:
                dest.write_text(doc.content, encoding="utf-8")

    (app_dir / "compliance_report.md").write_text(
        _build_compliance_report(result), encoding="utf-8"
    )


def write_phase1_files(
    run_dir: Path,
    application: ClientApplication,
    extracted_data: Optional[ExtractedClientData],
    completeness_result: Optional[CompletenessCheckResult],
    source_documents: Optional[list[DocumentInput]] = None,
) -> None:
    """Write extraction-only artefacts for Phase 1 (no compliance output yet)."""
    app_id = application.application_id
    app_dir = run_dir / "applications" / app_id
    app_dir.mkdir(parents=True, exist_ok=True)

    _write_json(app_dir / "input.json", application.model_dump())

    if extracted_data:
        _write_json(app_dir / "extraction.json", extracted_data.model_dump())
    if completeness_result:
        _write_json(app_dir / "completeness_check.json", completeness_result.model_dump())

    if source_documents:
        docs_dir = app_dir / "documents"
        docs_dir.mkdir(exist_ok=True)
        for doc in source_documents:
            dest = docs_dir / doc.filename
            try:
                _shutil.copy2(doc.file_path, dest)
            except OSError:
                dest.write_text(doc.content, encoding="utf-8")


def write_failed_application_files(
    run_dir: Path, application: ClientApplication, error_message: str
) -> None:
    fail_dir = run_dir / "failed" / application.application_id
    fail_dir.mkdir(parents=True, exist_ok=True)

    _write_json(fail_dir / "input.json", application.model_dump())
    _write_json(fail_dir / "error.json", {
        "application_id": application.application_id,
        "error": error_message,
        "failed_at": datetime.now(timezone.utc).isoformat(),
    })


def write_run_summary(
    run_dir: Path, run_id: str, metrics: PipelineMetrics, results: list[PipelineResult]
) -> None:
    _write_json(run_dir / "run_summary.json", {
        "run_id": run_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics.model_dump(),
        "application_ids": [r.client_application.application_id for r in results],
    })


def update_latest_symlink(run_dir: Path) -> None:
    latest = _output_root() / "latest"
    if latest.is_symlink():
        latest.unlink()
    elif latest.exists():
        shutil.rmtree(latest)
    try:
        latest.symlink_to(run_dir.resolve())
    except OSError:
        shutil.copytree(str(run_dir), str(latest))


# ── Compliance report ───────────────────────────────────────────────────────────

def _build_compliance_report(result: PipelineResult) -> str:
    app = result.client_application
    ra = result.risk_assessment
    os_ = result.onboarding_summary
    ev = result.evaluation
    pm = result.pipeline_metadata
    ext = result.extracted_data
    cr = result.completeness_result

    lines: list[str] = [
        f"# Compliance Review: {app.client_name}",
        "",
        f"**Application ID:** {app.application_id}",
        f"**Processed:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**Pipeline Version:** {pm.pipeline_version}",
        f"**Model:** {result.risk_assessment.metadata.model_used}",
        "",
    ]

    # ── Document Extraction section ────────────────────────────────────────────
    if ext:
        lines += [
            "## Document Extraction",
            "",
            f"**Documents Processed:** {len(ext.documents_provided)}",
            f"**Overall Extraction Confidence:** {ext.overall_extraction_confidence:.0%}",
            f"**Review Action:** {cr.recommended_action if cr else 'N/A'}",
            "",
            "### Extracted Fields",
            "",
            "| Field | Extracted Value | Confidence | Source Document |",
            "|-------|----------------|------------|----------------|",
        ]
        for field_name in [
            "client_name", "entity_type", "jurisdiction", "registered_address",
            "tax_id", "investment_amount", "investment_strategy", "risk_tolerance",
            "source_of_funds", "source_of_wealth",
        ]:
            conf = getattr(ext, field_name, None)
            if conf:
                val = (conf.extracted_value or "—")[:60]
                lines.append(f"| {field_name} | {val} | {conf.confidence:.2f} | {conf.source_document} |")
        if ext.beneficial_owners:
            for i, owner in enumerate(ext.beneficial_owners):
                val = (owner.extracted_value or "—")[:60]
                lines.append(f"| beneficial_owner_{i+1} | {val} | {owner.confidence:.2f} | {owner.source_document} |")
        if ext.authorized_signatories:
            for i, sig in enumerate(ext.authorized_signatories):
                val = (sig.extracted_value or "—")[:60]
                lines.append(f"| authorized_signatory_{i+1} | {val} | {sig.confidence:.2f} | {sig.source_document} |")
        lines.append("")

        low_conf_fields = [
            f for f in ["client_name", "entity_type", "jurisdiction", "investment_amount", "source_of_funds"]
            if (c := getattr(ext, f, None)) and c.confidence < 0.7
        ]
        if low_conf_fields or (ext.beneficial_owners and any(o.confidence < 0.7 for o in ext.beneficial_owners)):
            lines += ["### Low Confidence Fields", ""]
            for f in low_conf_fields:
                conf = getattr(ext, f)
                lines.append(f"- **{f}** (confidence {conf.confidence:.2f}): {conf.reasoning}")
            for owner in ext.beneficial_owners:
                if owner.confidence < 0.7:
                    lines.append(f"- **beneficial_owners** (confidence {owner.confidence:.2f}): {owner.reasoning}")
            lines.append("")

        if cr and cr.recommended_action != "proceed_to_compliance" and pm.ops_review_applied:
            lines += [
                "### Corrections Applied",
                "",
                f"**Review type:** {cr.recommended_action}",
                f"**Fields corrected by ops review:** {', '.join(cr.required_fields_missing) if cr.required_fields_missing else 'None'}",
                "",
            ]

        if ext.extraction_warnings:
            lines += ["### Extraction Warnings", ""]
            for w in ext.extraction_warnings:
                lines.append(f"- {w}")
            lines.append("")

    lines += [
        "## Application Details",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| Client Type | {app.client_type} |",
        f"| Estimated AUM | ${app.estimated_aum:,.0f} |",
        f"| Requested Services | {app.requested_services} |",
        f"| Submission Date | {app.submission_date} |",
        "",
        "## Risk Assessment",
        "",
        f"**Overall Risk Level:** {ra.overall_risk_level.upper()}",
        f"**Recommended Review Track:** {ra.recommended_review_track}",
        "",
        "### Overall Reasoning",
        ra.overall_reasoning,
        "",
        "### Risk Dimensions",
        "",
    ]

    for dim in ra.risk_dimensions:
        lines += [
            f"#### {dim.dimension}",
            f"- **Risk Contribution:** {dim.risk_contribution}",
            f"- **Findings:** {dim.findings}",
            f"- **Reasoning:** {dim.reasoning}",
            "",
        ]

    lines += ["### Compliance Flags", ""]
    if ra.compliance_flags:
        lines += [f"- {flag}" for flag in ra.compliance_flags]
    else:
        lines.append("No compliance flags identified")
    lines.append("")

    if ra.missing_information:
        lines += ["### Missing Information", ""]
        lines += [f"- {mi}" for mi in ra.missing_information]
        lines.append("")

    lines += [
        "## Onboarding Summary",
        "",
        "### Client Overview",
        os_.client_overview,
        "",
        "### Investment Profile",
        "",
        f"- **Strategy:** {os_.investment_profile.strategy}",
        f"- **Amount:** ${os_.investment_profile.amount:,.0f} {os_.investment_profile.currency}",
    ]
    if os_.investment_profile.special_requirements:
        lines.append("- **Special Requirements:**")
        for req in os_.investment_profile.special_requirements:
            lines.append(f"  - {req}")
    lines.append("")

    lines += [
        "### Complexity",
        "",
        f"**Level:** {os_.complexity_level.upper()}",
        "",
        os_.complexity_reasoning,
        "",
        "### Next Steps",
        "",
    ]
    for step in os_.next_steps:
        dep = f" *(depends on: {step.depends_on})*" if step.depends_on else ""
        lines.append(f"{step.step_number}. **[{step.owner} / {step.priority}]** {step.action}{dep}")
    lines.append("")

    lines += ["### Blockers", ""]
    if os_.blockers:
        lines += [f"- {b}" for b in os_.blockers]
    else:
        lines.append("No blockers identified")
    lines.append("")

    lines += [
        "### Track Assignment",
        "",
        f"**Onboarding Track:** {os_.estimated_onboarding_track}",
        f"**Estimated Review Time:** {os_.estimated_review_time}",
        "",
    ]

    if ev:
        ra_e = ev.risk_assessment_evaluation
        os_e = ev.onboarding_summary_evaluation
        lines += [
            "## Evaluation Scores",
            "",
            "| Metric | Score |",
            "|--------|-------|",
            f"| Reasoning Quality | {ra_e.reasoning_quality.score}/100 |",
            f"| Reasoning Completeness | {ra_e.reasoning_completeness.score}/100 |",
            f"| Risk Level Appropriateness | {ra_e.risk_level_appropriateness.score}/100 |",
            f"| Compliance Flags Accuracy | {ra_e.compliance_flags_accuracy.score}/100 |",
            f"| Overall Risk Assessment | {ra_e.overall_assessment_quality.score}/100 |",
            f"| Actionability | {os_e.actionability.score}/100 |",
            f"| Risk Grounding | {os_e.risk_grounding.score}/100 |",
            f"| Next Steps Quality | {os_e.next_steps_quality.score}/100 |",
            f"| Completeness | {os_e.completeness.score}/100 |",
            f"| Overall Summary | {os_e.overall_summary_quality.score}/100 |",
            "",
            "### Critical Issues",
            "",
        ]
        if ra_e.critical_issues:
            lines += [f"- {issue}" for issue in ra_e.critical_issues]
        else:
            lines.append("None")

    return "\n".join(lines) + "\n"


def _write_json(path: Path, data: object) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
