from typing import Literal, Optional
from pydantic import BaseModel, Field
from models.events import EventType, StageLabel, PipelineEvent  # noqa: F401 — re-export for API consumers


class RunRequest(BaseModel):
    phase: Literal[1, 2] = 1
    max_applications: int = Field(default=3, ge=1, le=20)
    client_id: Optional[str] = None
