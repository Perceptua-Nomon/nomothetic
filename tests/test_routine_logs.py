"""Tests for autonomy-routine status/log storage and endpoints.

Covers the RoutineLogStore unit and the report/get/list route flow. No Pi
hardware required; routes are exercised with device auth disabled.
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from nomothetic.api import create_app
from nomothetic.routine_log_store import (
    InvalidRoutineName,
    RoutineLogStore,
    validate_routine_name,
)

# ---------------------------------------------------------------------------
# RoutineLogStore unit
# ---------------------------------------------------------------------------


def test_record_and_get():
    store = RoutineLogStore()
    store.record("explore", "running", data={"x": 1}, run_id="r1")
    snap = store.get("explore")
    assert snap is not None
    assert snap["status"] == "running"
    assert snap["run_id"] == "r1"
    assert snap["event_count"] == 1
    assert snap["events"][0]["data"] == {"x": 1}
    assert snap["started_at"] and snap["updated_at"]


def test_status_mapping():
    store = RoutineLogStore()
    store.record("explore", "starting", run_id="r1")
    assert store.get("explore")["status"] == "starting"
    store.record("explore", "running", run_id="r1")
    assert store.get("explore")["status"] == "running"
    store.record("explore", "stopping", data={"reason": "done"}, run_id="r1")
    assert store.get("explore")["status"] == "stopped"
    store.record("explore", "error", data={"message": "boom"}, run_id="r1")
    assert store.get("explore")["status"] == "error"


def test_log_event_does_not_change_status():
    store = RoutineLogStore()
    store.record("explore", "running", run_id="r1")
    store.record("explore", "log", data={"msg": "step"}, run_id="r1")
    snap = store.get("explore")
    assert snap["status"] == "running"  # free-form log leaves status untouched
    assert snap["event_count"] == 2


def test_new_run_clears_previous_events():
    store = RoutineLogStore()
    store.record("explore", "running", run_id="r1")
    store.record("explore", "error", data={"message": "boom"}, run_id="r1")
    store.record("explore", "starting", run_id="r2")  # new run
    snap = store.get("explore")
    assert snap["run_id"] == "r2"
    assert snap["status"] == "starting"
    assert snap["event_count"] == 1


def test_bounded_ring_buffer():
    store = RoutineLogStore(max_events=3)
    for i in range(5):
        store.record("explore", "log", data={"i": i}, run_id="r1")
    snap = store.get("explore")
    assert snap["event_count"] == 3
    assert [e["data"]["i"] for e in snap["events"]] == [2, 3, 4]


def test_get_with_limit():
    store = RoutineLogStore()
    for i in range(5):
        store.record("explore", "log", data={"i": i}, run_id="r1")
    snap = store.get("explore", limit=2)
    assert snap["event_count"] == 5  # total stored
    assert len(snap["events"]) == 2  # returned subset
    assert [e["data"]["i"] for e in snap["events"]] == [3, 4]


def test_get_unknown_routine_returns_none():
    assert RoutineLogStore().get("ghost") is None


def test_list_routines():
    store = RoutineLogStore()
    store.record("explore", "running", run_id="r1")
    store.record("follow-user", "starting", run_id="r2")
    summaries = {s["routine"]: s for s in store.list_routines()}
    assert set(summaries) == {"explore", "follow-user"}
    assert summaries["explore"]["status"] == "running"
    assert "events" not in summaries["explore"]  # summary carries no events


def test_validate_routine_name():
    assert validate_routine_name("explore") == "explore"
    assert validate_routine_name("follow-user") == "follow-user"
    for bad in ["", "bad name", "-leading", ".hidden", "x" * 65]:
        with pytest.raises(InvalidRoutineName):
            validate_routine_name(bad)


def test_record_rejects_invalid_name():
    with pytest.raises(InvalidRoutineName):
        RoutineLogStore().record("bad name", "log")


# ---------------------------------------------------------------------------
# Route flow (device auth disabled)
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    with patch.dict(os.environ, {"NOMON_DEVICE_AUTH": "false", "NOMON_API_MODE": "device"}):
        app = create_app()
    app.state.routine_log_store = RoutineLogStore()
    return TestClient(app)


def test_report_then_get_logs(client):
    ack = client.post(
        "/api/routines/explore/events",
        json={"type": "running", "data": {"routine": "explore"}, "run_id": "r1"},
    )
    assert ack.status_code == 200
    body = ack.json()
    assert body["status"] == "running"
    assert body["run_id"] == "r1"
    assert body["timestamp"]

    logs = client.get("/api/routines/explore/logs")
    assert logs.status_code == 200
    data = logs.json()
    assert data["routine"] == "explore"
    assert data["status"] == "running"
    assert data["run_id"] == "r1"
    assert data["events"][-1]["type"] == "running"
    assert data["timestamp"]


def test_error_event_surfaces_status_and_message(client):
    """The user's case: a routine that dies should report an error + message."""
    client.post(
        "/api/routines/explore/events",
        json={"type": "running", "data": {}, "run_id": "r1"},
    )
    client.post(
        "/api/routines/explore/events",
        json={"type": "error", "data": {"message": "Connection refused"}, "run_id": "r1"},
    )
    data = client.get("/api/routines/explore/logs").json()
    assert data["status"] == "error"
    assert data["events"][-1]["data"]["message"] == "Connection refused"


def test_get_logs_unknown_routine_404(client):
    assert client.get("/api/routines/ghost/logs").status_code == 404


def test_logs_limit_query(client):
    for i in range(4):
        client.post(
            "/api/routines/explore/events",
            json={"type": "log", "data": {"i": i}, "run_id": "r1"},
        )
    data = client.get("/api/routines/explore/logs", params={"limit": 2}).json()
    assert len(data["events"]) == 2
    assert data["event_count"] == 4
    assert data["events"][-1]["data"]["i"] == 3


def test_list_routines_endpoint(client):
    client.post("/api/routines/explore/events", json={"type": "starting", "data": {}})
    resp = client.get("/api/routines")
    assert resp.status_code == 200
    body = resp.json()
    assert body["timestamp"]
    assert "explore" in [r["routine"] for r in body["routines"]]


def test_invalid_routine_name_rejected(client):
    # "bad name" (URL-encoded space) is not a valid routine name.
    resp = client.post("/api/routines/bad%20name/events", json={"type": "log", "data": {}})
    assert resp.status_code == 400
