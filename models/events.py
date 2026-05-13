from typing import Literal

from pydantic import BaseModel

EventType = Literal[
    "stage_started", "llm_chunk", "cot_block", "tool_called",
    "stage_completed", "eval_score", "routing_decision", "reviewer_brief",
    "run_complete", "error",
]

StageLabel = Literal["stage0", "stage1", "stage2", "evaluator", "orchestrator", "reviewer_brief", "pipeline"]


class PipelineEvent(BaseModel):
    event: EventType
    application_id: str
    stage: StageLabel
    data: dict
