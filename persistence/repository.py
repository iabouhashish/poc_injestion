from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, select

from persistence.models import (
    ApplicationResultRecord,
    EvaluationScoreRecord,
    LLMCallRecord,
    PipelineRunRecord,
)


# ── Write operations ────────────────────────────────────────────────────────────

def save_pipeline_run(session: Session, record: PipelineRunRecord) -> PipelineRunRecord:
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def update_pipeline_run(session: Session, run_id: str, **kwargs: Any) -> None:
    run = session.exec(select(PipelineRunRecord).where(PipelineRunRecord.run_id == run_id)).first()
    if run is None:
        return
    for key, value in kwargs.items():
        if hasattr(run, key):
            setattr(run, key, value)
    session.add(run)
    session.commit()


def save_application_result(session: Session, record: ApplicationResultRecord) -> ApplicationResultRecord:
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def save_eval(session: Session, record: EvaluationScoreRecord) -> EvaluationScoreRecord:
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def save_llm_call(session: Session, record: LLMCallRecord) -> LLMCallRecord:
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def get_phase1_results(session: Session, run_id: str) -> list[ApplicationResultRecord]:
    """Return all Phase-1-complete application records for a given run."""
    return list(session.exec(
        select(ApplicationResultRecord).where(
            ApplicationResultRecord.run_id == run_id,
            ApplicationResultRecord.phase_completed >= 1,
        )
    ).all())


def get_phase1_records_by_monday_ids(
    session: Session, monday_item_ids: list[str]
) -> list[ApplicationResultRecord]:
    """Return Phase 1 records matching any of the given Monday item IDs, not yet in Phase 2."""
    return list(session.exec(
        select(ApplicationResultRecord).where(
            ApplicationResultRecord.monday_item_id.in_(monday_item_ids),
            ApplicationResultRecord.phase_completed == 1,
        )
    ).all())


def get_unprocessed_phase1_records(session: Session) -> list[ApplicationResultRecord]:
    """Return all Phase 1 completed records that have not yet been processed by Phase 2."""
    return list(session.exec(
        select(ApplicationResultRecord).where(
            ApplicationResultRecord.phase_completed == 1,
        )
    ).all())


def get_latest_phase1_run(session: Session) -> Optional[PipelineRunRecord]:
    """Return the most recent Phase 1 PipelineRunRecord."""
    return session.exec(
        select(PipelineRunRecord)
        .where(PipelineRunRecord.phase == 1)
        .order_by(PipelineRunRecord.started_at.desc())  # type: ignore[arg-type]
        .limit(1)
    ).first()


def get_application_result(
    session: Session, run_id: str, application_id: str
) -> Optional[ApplicationResultRecord]:
    return session.exec(
        select(ApplicationResultRecord).where(
            ApplicationResultRecord.run_id == run_id,
            ApplicationResultRecord.application_id == application_id,
        )
    ).first()


def update_application_result(
    session: Session, run_id: str, application_id: str, **kwargs: Any
) -> None:
    record = get_application_result(session, run_id, application_id)
    if record is None:
        return
    for key, value in kwargs.items():
        if hasattr(record, key):
            setattr(record, key, value)
    session.add(record)
    session.commit()


# ── Read / query operations ─────────────────────────────────────────────────────

def get_run_summary(session: Session, run_id: str) -> dict:
    run = session.exec(select(PipelineRunRecord).where(PipelineRunRecord.run_id == run_id)).first()
    if run is None:
        return {}
    apps = session.exec(
        select(ApplicationResultRecord).where(ApplicationResultRecord.run_id == run_id)
    ).all()
    evals = session.exec(
        select(EvaluationScoreRecord).where(EvaluationScoreRecord.run_id == run_id)
    ).all()
    avg_ra = (
        sum(e.overall_risk_assessment_score for e in evals) / len(evals) if evals else None
    )
    avg_os = sum(e.overall_summary_score for e in evals) / len(evals) if evals else None
    return {
        "run": run.model_dump(),
        "application_count": len(apps),
        "avg_risk_assessment_score": avg_ra,
        "avg_summary_score": avg_os,
    }


def get_application_history(session: Session, application_id: str) -> list[dict]:
    rows = session.exec(
        select(ApplicationResultRecord)
        .where(ApplicationResultRecord.application_id == application_id)
        .order_by(ApplicationResultRecord.processed_at)  # type: ignore[arg-type]
    ).all()
    return [r.model_dump() for r in rows]


def get_runs_comparison(session: Session, limit: int = 10) -> list[dict]:
    runs = session.exec(
        select(PipelineRunRecord)
        .order_by(PipelineRunRecord.started_at.desc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all()
    return [r.model_dump() for r in runs]


def get_escalation_trend(session: Session, limit: int = 10) -> list[dict]:
    runs = session.exec(
        select(PipelineRunRecord)
        .order_by(PipelineRunRecord.started_at.desc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all()
    return [
        {"run_id": r.run_id, "started_at": r.started_at, "escalation_rate": r.escalation_rate}
        for r in reversed(runs)
    ]


def get_cost_summary(session: Session, run_id: str) -> dict:
    calls = session.exec(
        select(LLMCallRecord).where(LLMCallRecord.run_id == run_id)
    ).all()
    by_stage: dict[str, dict] = {}
    for c in calls:
        entry = by_stage.setdefault(c.stage, {"total_tokens": 0, "estimated_cost": 0.0, "calls": 0})
        entry["total_tokens"] += c.total_tokens
        entry["estimated_cost"] += c.estimated_cost
        entry["calls"] += 1
    return {
        "run_id": run_id,
        "total_tokens": sum(c.total_tokens for c in calls),
        "total_estimated_cost": sum(c.estimated_cost for c in calls),
        "by_stage": by_stage,
    }


def get_failed_applications(session: Session, run_id: str) -> list[dict]:
    rows = session.exec(
        select(ApplicationResultRecord).where(
            ApplicationResultRecord.run_id == run_id,
            ApplicationResultRecord.processing_status == "failed",
        )
    ).all()
    return [
        {
            "application_id": r.application_id,
            "client_name": r.client_name,
            "error_message": r.error_message,
            "processed_at": r.processed_at,
        }
        for r in rows
    ]
