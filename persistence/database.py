from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

_DB_PATH = os.getenv("DATABASE_PATH", "data/crestview_pipeline.db")


def _build_engine():
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{_DB_PATH}", echo=False)


engine = _build_engine()


def create_all_tables() -> None:
    from persistence.models import (  # noqa: F401 — registers tables with SQLModel metadata
        PipelineRunRecord,
        ApplicationResultRecord,
        EvaluationScoreRecord,
        LLMCallRecord,
    )
    SQLModel.metadata.create_all(engine)
    _migrate_tables()


def _migrate_tables() -> None:
    """Add new columns to existing tables without dropping data (SQLite ALTER TABLE)."""
    migrations = [
        ("pipeline_runs", "phase", "INTEGER NOT NULL DEFAULT 1"),
        ("application_results", "extraction_json", "TEXT"),
        ("application_results", "completeness_json", "TEXT"),
        ("application_results", "phase_completed", "INTEGER"),
    ]
    with engine.connect() as conn:
        for table, col, col_def in migrations:
            try:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE {table} ADD COLUMN {col} {col_def}"
                    )
                )
                conn.commit()
            except Exception:
                pass  # column already exists


@contextmanager
def get_session():
    with Session(engine) as session:
        yield session
