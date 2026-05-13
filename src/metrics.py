from __future__ import annotations

from collections import Counter

from rich import box
from rich.console import Console
from rich.table import Table

from models.pipeline import PipelineMetrics, PipelineResult

console = Console()


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


def compute_metrics(
    results: list[PipelineResult],
    failed_count: int,
    pv_failures: list,
    jf_failures: list,
) -> PipelineMetrics:
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
