"""Tests for the AI command HTTP endpoints (/api/ai/*).

The routes are exercised with device auth disabled and a fake AiCommandService
injected into ``app.state`` — asserting HTTP behaviour (status codes, payload
mapping, key lifecycle, rate limiting) without any Anthropic traffic. The
agentic loop itself is covered in test_ai_command.py.
"""

import os
import stat
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from nomothetic.ai_command import AiProviderAuthError, AiProviderError, AiUnavailableError
from nomothetic.api import create_app

TEST_KEY = "sk-ant-api03-test-key-0123456789"


class FakeAiService:
    """In-memory stand-in for AiCommandService used by the route tests."""

    def __init__(self):
        self.result = {
            "reply": "Done.",
            "actions": [{"tool": "drive", "input": {"speed_pct": 50}, "ok": True, "result": 4}],
            "model": "claude-opus-4-8",
            "stop_reason": "end_turn",
        }
        self.fail: Exception | None = None
        self.calls: list[tuple[list, str]] = []

    async def run_command(self, messages, api_key):
        self.calls.append((messages, api_key))
        if self.fail is not None:
            raise self.fail
        return dict(self.result)


@pytest.fixture
def client_service(tmp_path, monkeypatch):
    """TestClient + FakeAiService + key-file path, device auth disabled."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    key_path = tmp_path / "ai_key"
    with patch.dict(
        os.environ,
        {
            "NOMON_DEVICE_AUTH": "false",
            "NOMON_API_MODE": "device",
            "NOMON_AI_API_KEY_PATH": str(key_path),
        },
        clear=False,
    ):
        app = create_app()
    service = FakeAiService()
    app.state.ai_service = service
    return TestClient(app), service, key_path


# ============================================================================
# Key lifecycle
# ============================================================================


def test_key_status_unconfigured(client_service):
    client, _, _ = client_service
    resp = client.get("/api/ai/key")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["source"] is None
    assert body["timestamp"]


def test_key_put_get_delete_lifecycle(client_service):
    client, _, key_path = client_service

    resp = client.put("/api/ai/key", json={"api_key": TEST_KEY})
    assert resp.status_code == 200
    assert resp.json()["configured"] is True
    assert resp.json()["source"] == "stored"
    # The key itself is never echoed back.
    assert TEST_KEY not in resp.text
    # Stored owner-read-only on disk.
    assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600

    resp = client.get("/api/ai/key")
    assert resp.json()["configured"] is True

    resp = client.delete("/api/ai/key")
    assert resp.status_code == 200
    assert resp.json()["configured"] is False
    assert not key_path.exists()


def test_key_put_invalid_format_400(client_service):
    client, _, key_path = client_service
    resp = client.put("/api/ai/key", json={"api_key": "not-an-anthropic-key"})
    assert resp.status_code == 400
    assert not key_path.exists()


def test_key_env_fallback_reported_as_env(client_service, monkeypatch):
    client, _, _ = client_service
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-key-0123456789")
    resp = client.get("/api/ai/key")
    assert resp.json()["configured"] is True
    assert resp.json()["source"] == "env"


# ============================================================================
# Command endpoint
# ============================================================================


def _messages(*turns):
    roles = ["user", "assistant"]
    return {"messages": [{"role": roles[i % 2], "content": text} for i, text in enumerate(turns)]}


def test_command_without_key_503(client_service):
    client, service, _ = client_service
    resp = client.post("/api/ai/command", json=_messages("hi"))
    assert resp.status_code == 503
    assert service.calls == []


def test_command_success_maps_result(client_service):
    client, service, _ = client_service
    client.put("/api/ai/key", json={"api_key": TEST_KEY})
    resp = client.post("/api/ai/command", json=_messages("hello", "hi!", "drive forward"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "Done."
    assert body["stop_reason"] == "end_turn"
    assert body["actions"] == [
        {"tool": "drive", "input": {"speed_pct": 50}, "ok": True, "result": 4, "error": None}
    ]
    assert body["timestamp"]
    messages, api_key = service.calls[0]
    assert api_key == TEST_KEY
    assert messages[-1] == {"role": "user", "content": "drive forward"}
    assert len(messages) == 3


def test_command_uses_env_key_when_no_stored_key(client_service, monkeypatch):
    client, service, _ = client_service
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-key-0123456789")
    resp = client.post("/api/ai/command", json=_messages("hi"))
    assert resp.status_code == 200
    assert service.calls[0][1] == "sk-ant-env-key-0123456789"


def test_command_provider_auth_error_502(client_service):
    client, service, _ = client_service
    client.put("/api/ai/key", json={"api_key": TEST_KEY})
    service.fail = AiProviderAuthError("the AI provider rejected the configured API key")
    resp = client.post("/api/ai/command", json=_messages("hi"))
    assert resp.status_code == 502
    # The app-wide HTTPException handler shapes errors as {"error", "timestamp"}.
    assert "rejected" in resp.json()["error"]


def test_command_provider_error_502(client_service):
    client, service, _ = client_service
    client.put("/api/ai/key", json={"api_key": TEST_KEY})
    service.fail = AiProviderError("AI provider request failed: overloaded")
    resp = client.post("/api/ai/command", json=_messages("hi"))
    assert resp.status_code == 502


def test_command_ai_unavailable_503(client_service):
    client, service, _ = client_service
    client.put("/api/ai/key", json={"api_key": TEST_KEY})
    service.fail = AiUnavailableError("AI support is not installed on this device")
    resp = client.post("/api/ai/command", json=_messages("hi"))
    assert resp.status_code == 503


def test_command_rejects_bad_turn_shapes(client_service):
    client, _, _ = client_service
    # Empty history.
    assert client.post("/api/ai/command", json={"messages": []}).status_code == 422
    # Last turn must be the user's.
    assert (
        client.post(
            "/api/ai/command",
            json={
                "messages": [
                    {"role": "user", "content": "a"},
                    {"role": "assistant", "content": "b"},
                ]
            },
        ).status_code
        == 422
    )
    # Roles must alternate.
    assert (
        client.post(
            "/api/ai/command",
            json={
                "messages": [
                    {"role": "user", "content": "a"},
                    {"role": "user", "content": "b"},
                ]
            },
        ).status_code
        == 422
    )
    # Unknown role.
    assert (
        client.post(
            "/api/ai/command",
            json={"messages": [{"role": "system", "content": "x"}]},
        ).status_code
        == 422
    )
    # Empty content.
    assert (
        client.post(
            "/api/ai/command",
            json={"messages": [{"role": "user", "content": ""}]},
        ).status_code
        == 422
    )


def test_command_rate_limited_429(client_service):
    client, _, _ = client_service
    client.put("/api/ai/key", json={"api_key": TEST_KEY})
    payload = _messages("hi")
    for _ in range(10):
        assert client.post("/api/ai/command", json=payload).status_code == 200
    assert client.post("/api/ai/command", json=payload).status_code == 429


# ============================================================================
# Auth inheritance
# ============================================================================


def test_ai_endpoints_require_auth_when_enabled(tmp_path):
    with patch.dict(
        os.environ,
        {
            "NOMON_DEVICE_AUTH": "true",
            "NOMON_API_MODE": "device",
            "NOMON_AI_API_KEY_PATH": str(tmp_path / "ai_key"),
        },
        clear=False,
    ):
        app = create_app()
    client = TestClient(app)
    assert client.get("/api/ai/key").status_code == 401
    resp = client.post("/api/ai/command", json={"messages": [{"role": "user", "content": "x"}]})
    assert resp.status_code == 401
