"""Thin shim: delegates to the real CLI pipeline functions and emits PipelineEvents for SSE."""
from __future__ import annotations

import asyncio
import threading

from models.events import PipelineEvent


def _emit(
    loop: asyncio.AbstractEventLoop,
    q: "asyncio.Queue[PipelineEvent | None]",
    event: PipelineEvent,
) -> None:
    loop.call_soon_threadsafe(q.put_nowait, event)


def run_phase1_streaming(
    loop: asyncio.AbstractEventLoop,
    event_queue: "asyncio.Queue[PipelineEvent | None]",
    stop_event: threading.Event,
    max_applications: int = 3,
    client_id: str | None = None,
) -> None:
    from src.run_phase1 import run_phase1
    run_phase1(
        client_id=client_id,
        max_applications=max_applications,
        on_event=lambda ev: _emit(loop, event_queue, ev),
        stop_event=stop_event,
    )


def run_phase2_streaming(
    loop: asyncio.AbstractEventLoop,
    event_queue: "asyncio.Queue[PipelineEvent | None]",
    stop_event: threading.Event,
    max_applications: int = 3,
    client_id: str | None = None,
) -> None:
    from src.run_phase2 import run_phase2
    run_phase2(
        client_id=client_id,
        max_applications=max_applications,
        on_event=lambda ev: _emit(loop, event_queue, ev),
        stop_event=stop_event,
    )
