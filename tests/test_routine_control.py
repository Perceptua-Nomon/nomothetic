"""Tests for the routine lifecycle-control endpoints (start/stop/stop-all).

The routes are exercised with device auth disabled and a fake RoutineManager
injected into ``app.state`` — so these assert HTTP behaviour (status codes,
payload mapping, error translation) without spawning real processes. The full
process lifecycle is covered in test_routine_manager.py.
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from nomothetic.api import create_app
from nomothetic.routine_log_store import validate_routine_name
from nomothetic.routine_manager import (
    RoutineAlreadyRunning,
    RoutineCredentialsError,
    RoutineLaunchError,
)


class FakeManager:
    """In-memory stand-in for RoutineManager used by the route tests."""

    def __init__(self) -> None:
        self.running: dict[str, dict] = {}
        self.start_fail: Exception | None = None
        self.started: list[tuple[str, dict, object, object]] = []

    async def start(self, routine, params=None, heartbeat_timeout_s=None, max_duration_s=None):
        validate_routine_name(routine)
        if self.start_fail is not None:
            raise self.start_fail
        self.started.append((routine, params or {}, heartbeat_timeout_s, max_duration_s))
        info = {
            "routine": routine,
            "launch_id": "launch-1",
            "pid": 4321,
            "started_at": "2026-06-21T00:00:00+00:00",
            "heartbeat_timeout_s": float(heartbeat_timeout_s) if heartbeat_timeout_s else 120.0,
            "max_duration_s": float(max_duration_s) if max_duration_s else None,
            "status": "running",
        }
        self.running[routine] = info
        return info

    async def stop(self, routine):
        validate_routine_name(routine)
        return self.running.pop(routine, None)

    async def stop_all(self):
        out = list(self.running.values())
        self.running.clear()
        return out

    async def heartbeat(self, routine):
        validate_routine_name(routine)
        info = self.running.get(routine)
        if info is None:
            return None
        return {
            "routine": routine,
            "status": "running",
            "heartbeat_timeout_s": info["heartbeat_timeout_s"],
            "seconds_remaining": info["heartbeat_timeout_s"],
        }


@pytest.fixture
def client_manager():
    with patch.dict(os.environ, {"NOMON_DEVICE_AUTH": "false", "NOMON_API_MODE": "device"}):
        app = create_app()
    manager = FakeManager()
    app.state.routine_manager = manager
    return TestClient(app), manager


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


def test_start_returns_run_info(client_manager):
    client, manager = client_manager
    resp = client.post(
        "/api/routines/start",
        json={
            "routine": "explore",
            "params": {"forward_speed_pct": 60},
            "heartbeat_timeout_s": 60,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["routine"] == "explore"
    assert body["status"] == "running"
    assert body["heartbeat_timeout_s"] == 60
    assert body["max_duration_s"] is None
    assert body["pid"] == 4321
    assert body["timestamp"]
    # The payload's params and lease window reached the manager.
    assert manager.started == [("explore", {"forward_speed_pct": 60}, 60, None)]


def test_start_defaults_params_to_empty(client_manager):
    client, manager = client_manager
    resp = client.post("/api/routines/start", json={"routine": "explore"})
    assert resp.status_code == 201
    assert manager.started[0][1] == {}


def test_start_already_running_conflict(client_manager):
    client, manager = client_manager
    manager.start_fail = RoutineAlreadyRunning("explore")
    resp = client.post("/api/routines/start", json={"routine": "explore"})
    assert resp.status_code == 409


def test_start_without_credentials_503(client_manager):
    client, manager = client_manager
    manager.start_fail = RoutineCredentialsError("no plugin credential configured")
    resp = client.post("/api/routines/start", json={"routine": "explore"})
    assert resp.status_code == 503


def test_start_launch_error_500(client_manager):
    client, manager = client_manager
    manager.start_fail = RoutineLaunchError("could not launch 'nomon-autonomon'")
    resp = client.post("/api/routines/start", json={"routine": "explore"})
    assert resp.status_code == 500


def test_start_invalid_routine_name_400(client_manager):
    client, _ = client_manager
    resp = client.post("/api/routines/start", json={"routine": "bad name"})
    assert resp.status_code == 400


def test_start_rejects_out_of_range_duration_422(client_manager):
    client, _ = client_manager
    # Above the model's hard cap (86400) → request validation rejects it.
    resp = client.post("/api/routines/start", json={"routine": "explore", "max_duration_s": 999999})
    assert resp.status_code == 422


def test_start_rejects_heartbeat_timeout_below_minimum_422(client_manager):
    client, _ = client_manager
    # Below the model's floor (5 s) → request validation rejects it.
    resp = client.post("/api/routines/start", json={"routine": "explore", "heartbeat_timeout_s": 1})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# stop / stop-all
# ---------------------------------------------------------------------------


def test_stop_running_routine(client_manager):
    client, manager = client_manager
    client.post("/api/routines/start", json={"routine": "explore"})
    resp = client.post("/api/routines/stop", json={"routine": "explore"})
    assert resp.status_code == 200
    assert resp.json()["routine"] == "explore"
    assert "explore" not in manager.running


def test_stop_not_running_404(client_manager):
    client, _ = client_manager
    resp = client.post("/api/routines/stop", json={"routine": "explore"})
    assert resp.status_code == 404


def test_stop_all_returns_stopped_set(client_manager):
    client, _ = client_manager
    client.post("/api/routines/start", json={"routine": "explore"})
    client.post("/api/routines/start", json={"routine": "follow-user"})
    resp = client.post("/api/routines/stop-all")
    assert resp.status_code == 200
    body = resp.json()
    assert {r["routine"] for r in body["stopped"]} == {"explore", "follow-user"}
    assert body["timestamp"]


def test_stop_all_when_none_running(client_manager):
    client, _ = client_manager
    resp = client.post("/api/routines/stop-all")
    assert resp.status_code == 200
    assert resp.json()["stopped"] == []


# ---------------------------------------------------------------------------
# available (catalogue)
# ---------------------------------------------------------------------------


def test_available_routines(client_manager, monkeypatch):
    client, _ = client_manager
    monkeypatch.setattr(
        "nomothetic.routine_control_routes.autonomon_catalog",
        lambda: {"routines": ["explore"], "params_schema": {"speed": {}}, "version": "0.1.0"},
    )
    resp = client.get("/api/routines/available")
    assert resp.status_code == 200
    body = resp.json()
    assert body["routines"] == ["explore"]
    assert body["version"] == "0.1.0"
    assert body["params_schema"] == {"speed": {}}
    assert body["timestamp"]


def test_available_routines_empty_when_autonomon_absent(client_manager, monkeypatch):
    client, _ = client_manager
    monkeypatch.setattr(
        "nomothetic.routine_control_routes.autonomon_catalog",
        lambda: {"routines": [], "params_schema": {}, "version": None},
    )
    resp = client.get("/api/routines/available")
    assert resp.status_code == 200
    assert resp.json()["routines"] == []


# ---------------------------------------------------------------------------
# heartbeat
# ---------------------------------------------------------------------------


def test_heartbeat_running_routine(client_manager):
    client, _ = client_manager
    client.post("/api/routines/start", json={"routine": "explore", "heartbeat_timeout_s": 90})
    resp = client.post("/api/routines/heartbeat", json={"routine": "explore"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["routine"] == "explore"
    assert body["status"] == "running"
    assert body["heartbeat_timeout_s"] == 90
    assert body["seconds_remaining"] == 90
    assert body["timestamp"]


def test_heartbeat_not_running_404(client_manager):
    client, _ = client_manager
    resp = client.post("/api/routines/heartbeat", json={"routine": "explore"})
    assert resp.status_code == 404


def test_manager_unavailable_503():
    with patch.dict(os.environ, {"NOMON_DEVICE_AUTH": "false", "NOMON_API_MODE": "device"}):
        app = create_app()
    app.state.routine_manager = None
    client = TestClient(app)
    resp = client.post("/api/routines/stop-all")
    assert resp.status_code == 503
