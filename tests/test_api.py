"""Tests for API schemas, routes, and pipeline adapter edge cases."""
from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.schemas import PipelineEvent, RunRequest
from api.pipeline_adapter import _emit


# ── Schema tests ───────────────────────────────────────────────────────────────

class TestPipelineEvent:
    def test_valid_event_type_accepted(self):
        e = PipelineEvent(event="stage_started", application_id="APP-001",
                          stage="stage1", data={})
        assert e.event == "stage_started"

    def test_invalid_event_type_raises(self):
        with pytest.raises(ValidationError):
            PipelineEvent(event="bogus_event", application_id="APP-001",
                          stage="stage1", data={})

    def test_invalid_stage_raises(self):
        with pytest.raises(ValidationError):
            PipelineEvent(event="stage_started", application_id="APP-001",
                          stage="not_a_stage", data={})


class TestRunRequest:
    def test_phase1_valid(self):
        r = RunRequest(phase=1)
        assert r.phase == 1

    def test_phase2_valid(self):
        r = RunRequest(phase=2)
        assert r.phase == 2

    def test_phase3_invalid(self):
        with pytest.raises(ValidationError):
            RunRequest(phase=3)

    def test_defaults(self):
        r = RunRequest()
        assert r.phase == 1
        assert r.max_applications == 3


# ── Emit helper ────────────────────────────────────────────────────────────────

class TestEmitHelper:
    def test_schedules_put_nowait_on_loop(self):
        loop = MagicMock()
        q = MagicMock()
        event = PipelineEvent(event="stage_started", application_id="APP-001",
                              stage="stage0", data={})
        _emit(loop, q, event)
        loop.call_soon_threadsafe.assert_called_once_with(q.put_nowait, event)


# ── Route tests (TestClient) ───────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_runs_registry():
    import api.routes as routes_mod
    routes_mod._runs.clear()
    yield
    routes_mod._runs.clear()


@pytest.fixture
def client():
    from api.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestApiRoutes:
    def test_post_runs_returns_run_id(self, client):
        with patch("api.pipeline_adapter.run_phase1_streaming") as mock_fn:
            mock_fn.return_value = None
            resp = client.post("/api/runs", json={"phase": 1, "max_applications": 1})
        assert resp.status_code == 200
        body = resp.json()
        assert "run_id" in body
        assert body["status"] == "started"

    def test_post_runs_invalid_phase_422(self, client):
        resp = client.post("/api/runs", json={"phase": 9})
        assert resp.status_code == 422

    def test_get_runs_stream_unknown_404(self, client):
        resp = client.get("/api/runs/does-not-exist/stream")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "run not found"

    def test_get_runs_returns_list(self, client):
        resp = client.get("/api/runs")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ── Adapter edge cases ─────────────────────────────────────────────────────────

class TestAdapterEdgeCases:
    def test_phase2_no_records_emits_error(self):
        """When no Phase 1 DB records exist, run_phase2_streaming emits an error event."""
        from pathlib import Path
        from api.pipeline_adapter import run_phase2_streaming

        loop = asyncio.new_event_loop()
        q: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()
        events = []

        def collect():
            run_phase2_streaming(loop=loop, event_queue=q, stop_event=stop,
                                 max_applications=3)

        with patch("src.run_phase2.create_all_tables"), \
             patch("src.run_phase2.create_run_directory", return_value=Path("/tmp/test")), \
             patch("src.run_phase2.get_session"), \
             patch("src.run_phase2.get_unprocessed_phase1_records", return_value=[]):
            t = threading.Thread(target=collect)
            t.start()
            t.join(timeout=5)

        # Flush call_soon_threadsafe callbacks — the loop must run to process them
        loop.run_until_complete(asyncio.sleep(0))

        # Drain events
        while not q.empty():
            events.append(q.get_nowait())

        loop.close()

        error_events = [e for e in events if e and e.event == "error"]
        assert len(error_events) >= 1

    def test_phase1_stop_event_exits_before_second_app(self):
        """Setting stop_event after first app prevents processing the second."""
        from pathlib import Path
        from api.pipeline_adapter import run_phase1_streaming
        from models.client import ClientApplication

        app1 = ClientApplication(
            application_id="APP-STOP-01", client_name="Test A", client_type="Institutional",
            requested_services="Equity", estimated_aum=100_000_000,
            submission_date="01/01/2026", status="New", description="Test A.",
        )
        app2 = ClientApplication(
            application_id="APP-STOP-02", client_name="Test B", client_type="Institutional",
            requested_services="Fixed Income", estimated_aum=50_000_000,
            submission_date="01/01/2026", status="New", description="Test B.",
        )

        loop = asyncio.new_event_loop()
        q: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()
        processed_ids = []

        def fake_run_stage0(**kwargs):
            processed_ids.append(kwargs.get("application_id"))
            stop.set()  # set after first app
            raise RuntimeError("stop test")

        with patch("src.run_phase1.create_all_tables"), \
             patch("src.run_phase1.create_run_directory", return_value=Path("/tmp/test")), \
             patch("src.run_phase1.get_session"), \
             patch("src.run_phase1.save_pipeline_run"), \
             patch("src.run_phase1.update_pipeline_run"), \
             patch("src.run_phase1.load_applications", return_value=[app1, app2]), \
             patch("src.run_phase1.load_documents", return_value=[MagicMock()]), \
             patch("src.run_phase1.run_stage0", side_effect=fake_run_stage0), \
             patch("src.run_phase1._save_phase1_to_db"), \
             patch("src.run_phase1._save_failure_to_db"), \
             patch("src.run_phase1.write_phase1_files"), \
             patch("src.run_phase1.write_failed_application_files"), \
             patch("src.run_phase1.update_latest_symlink"):
            t = threading.Thread(
                target=run_phase1_streaming,
                kwargs={"loop": loop, "event_queue": q, "stop_event": stop,
                        "max_applications": 2},
            )
            t.start()
            t.join(timeout=10)

        # Only first app should have been attempted
        assert "APP-STOP-02" not in processed_ids
