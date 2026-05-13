from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from api.schemas import PipelineEvent, RunRequest

router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=4)

# In-memory run registry: run_id → metadata dict
_runs: dict[str, dict] = {}
_MAX_RUNS = 20


def _evict_oldest() -> None:
    if len(_runs) >= _MAX_RUNS:
        oldest = next(iter(_runs))
        del _runs[oldest]


@router.post("/runs")
async def create_run(body: RunRequest) -> dict:
    run_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue[PipelineEvent | None] = asyncio.Queue()
    stop_event = threading.Event()

    _evict_oldest()
    _runs[run_id] = {
        "run_id": run_id,
        "phase": body.phase,
        "status": "running",
        "application_count": 0,
        "queue": event_queue,
        "stop_event": stop_event,
    }

    from api.pipeline_adapter import run_phase1_streaming, run_phase2_streaming
    fn = run_phase1_streaming if body.phase == 1 else run_phase2_streaming

    def _run() -> None:
        try:
            fn(loop=loop, event_queue=event_queue, stop_event=stop_event,
               max_applications=body.max_applications, client_id=body.client_id)
        finally:
            loop.call_soon_threadsafe(event_queue.put_nowait, None)  # sentinel

    _executor.submit(_run)

    # Orphan protection: if no consumer connects in 30s, cancel the run
    async def _orphan_guard() -> None:
        await asyncio.sleep(30)
        if run_id in _runs and _runs[run_id]["status"] == "running":
            stop_event.set()
            _runs[run_id]["status"] = "error"

    task = asyncio.create_task(_orphan_guard())
    _runs[run_id]["_guard_task"] = task  # keep reference so GC doesn't discard it

    return {"run_id": run_id, "status": "started"}


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail="run not found")

    run = _runs[run_id]
    event_queue: asyncio.Queue = run["queue"]

    async def _generate() -> AsyncGenerator[str, None]:
        try:
            while True:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=60.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                if event is None:
                    run["status"] = "complete"
                    yield f"data: {json.dumps({'event': 'run_complete', 'application_id': 'pipeline', 'stage': 'pipeline', 'data': {}})}\n\n"
                    break

                run["application_count"] += 1
                yield f"data: {event.model_dump_json()}\n\n"

                if event.event == "run_complete":
                    run["status"] = "complete"
                    break
        except asyncio.CancelledError:
            run["stop_event"].set()
            run["status"] = "cancelled"

    return StreamingResponse(_generate(), media_type="text/event-stream")


@router.get("/applications")
async def list_applications() -> list:
    from pathlib import Path
    from src.data_loader import load_applications
    csv_path = Path(__file__).parent.parent / "crestview_client_applications.csv"
    apps = load_applications(csv_path)
    return [
        {
            "application_id": a.application_id,
            "client_name": a.client_name,
            "client_type": a.client_type,
            "status": a.status,
        }
        for a in apps
    ]


@router.get("/runs")
async def list_runs() -> list:
    return [
        {
            "run_id": r["run_id"],
            "phase": r["phase"],
            "status": r["status"],
            "application_count": r["application_count"],
        }
        for r in _runs.values()
    ]
