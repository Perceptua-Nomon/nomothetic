"""Tests for the AI chat-command service and API key store.

The Claude loop is exercised with a fake client injected through
``client_factory`` — no network traffic, and nothing assumed about the
anthropic SDK beyond the ``messages.create`` call shape. Tool execution goes
through a fake ``hat_call`` that records invocations, so these tests also pin
the security contract: which HAT methods the AI can and cannot reach.
"""

import asyncio
import json
import os
import stat
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from nomothetic import ai_command
from nomothetic.ai_command import (
    AiCommandService,
    AiKeyStore,
    AiKeyStoreError,
    AiProviderAuthError,
    AiProviderError,
    AiUnavailableError,
    validate_api_key_format,
)

TEST_KEY = "sk-ant-api03-test-key-0123456789"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_block(name, tool_input, block_id="tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def response(blocks, stop_reason="end_turn", model="claude-opus-4-8"):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, model=model)


class FakeMessages:
    def __init__(self, outer):
        self._outer = outer

    async def create(self, **kwargs):
        self._outer.calls.append(kwargs)
        item = self._outer.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    """Stands in for AsyncAnthropic: scripted responses, recorded create() calls."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.closed = False
        self.messages = FakeMessages(self)

    async def close(self):
        self.closed = True


def make_service(responses, hat_results=None, **kwargs):
    """Build a service wired to a FakeClient and a recording hat_call."""
    hat_calls = []
    hat_results = hat_results or {}

    async def fake_hat_call(method, *args, **kw):
        hat_calls.append((method, args, kw))
        result = hat_results.get(method)
        if isinstance(result, Exception):
            raise result
        return result

    client = FakeClient(responses)
    service = AiCommandService(
        hat_call=fake_hat_call,
        client_factory=lambda key: client,
        **kwargs,
    )
    return service, client, hat_calls


def _run_coro(coro):
    """Run *coro* on a private event loop.

    Not :func:`asyncio.run`, which would clear the process-global current loop
    and disturb legacy implicit-loop use in tests that run later in the suite.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def run(service, prompt="do the thing"):
    return _run_coro(service.run_command([{"role": "user", "content": prompt}], api_key=TEST_KEY))


# ============================================================================
# API key format validation
# ============================================================================


def test_validate_key_format_accepts_and_strips():
    assert validate_api_key_format("  " + TEST_KEY + "\n") == TEST_KEY


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "sk-live-0123456789abcdef",  # wrong prefix
        "sk-ant-x",  # too short
        "sk-ant-" + "x" * 300,  # too long
        "sk-ant-abc def0123456789",  # inner whitespace
    ],
)
def test_validate_key_format_rejects(bad):
    with pytest.raises(ValueError):
        validate_api_key_format(bad)


# ============================================================================
# Key store
# ============================================================================


def test_key_store_save_load_roundtrip_with_0600(tmp_path):
    path = tmp_path / "ai_key"
    store = AiKeyStore(path=str(path))
    store.save(TEST_KEY)
    assert store.load() == TEST_KEY
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_key_store_load_missing_returns_none(tmp_path):
    assert AiKeyStore(path=str(tmp_path / "missing")).load() is None


def test_key_store_short_stored_key_treated_as_unset(tmp_path):
    path = tmp_path / "ai_key"
    path.write_text("short")
    assert AiKeyStore(path=str(path)).load() is None


def test_key_store_save_missing_directory_raises(tmp_path):
    store = AiKeyStore(path=str(tmp_path / "nope" / "ai_key"))
    with pytest.raises(AiKeyStoreError):
        store.save(TEST_KEY)


def test_key_store_clear(tmp_path):
    store = AiKeyStore(path=str(tmp_path / "ai_key"))
    assert store.clear() is False
    store.save(TEST_KEY)
    assert store.clear() is True
    assert store.load() is None


def test_key_store_resolve_prefers_stored_over_env(tmp_path, monkeypatch):
    env_key = "sk-ant-env-key-0123456789"
    store = AiKeyStore(path=str(tmp_path / "ai_key"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", env_key)
    assert store.resolve() == (env_key, "env")
    assert store.source() == "env"
    store.save(TEST_KEY)
    assert store.resolve() == (TEST_KEY, "stored")
    assert store.source() == "stored"


def test_key_store_resolve_none_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store = AiKeyStore(path=str(tmp_path / "ai_key"))
    assert store.resolve() is None
    assert store.source() is None


def test_key_store_path_from_env(tmp_path, monkeypatch):
    path = tmp_path / "custom_key"
    monkeypatch.setenv("NOMON_AI_API_KEY_PATH", str(path))
    store = AiKeyStore()
    store.save(TEST_KEY)
    assert path.read_text() == TEST_KEY


# ============================================================================
# Security contract: the tool surface is destructive-free
# ============================================================================


def test_destructive_hat_methods_not_exposed_as_tools():
    async def hat(method, *args, **kwargs):
        return None

    service = AiCommandService(hat_call=hat, client_factory=lambda key: None)
    names = set(service.tool_names)
    forbidden = {
        "reset_mcu",
        "set_motor_calibration",
        "set_servo_calibration",
        "calibrate_grayscale",
        "save_calibration",
        "reset_calibration",
        "set_servo_pulse_us",
        "set_servo_angle",
        "set_motor_speed",
    }
    assert names.isdisjoint(forbidden)
    assert {"stop", "drive", "steer", "read_ultrasonic", "start_routine"} <= names


# ============================================================================
# Agentic loop
# ============================================================================


def test_text_only_reply():
    service, client, hat_calls = make_service([response([text_block("Hello!")])])
    result = run(service, "hi")
    assert result["reply"] == "Hello!"
    assert result["actions"] == []
    assert result["stop_reason"] == "end_turn"
    assert result["model"] == "claude-opus-4-8"
    assert hat_calls == []
    assert client.closed is True
    kwargs = client.calls[0]
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert any(tool["name"] == "drive" for tool in kwargs["tools"])


def test_tool_use_executes_hat_call_and_reports():
    service, client, hat_calls = make_service(
        [
            response(
                [tool_block("drive", {"speed_pct": 50, "ttl_ms": 1000})],
                stop_reason="tool_use",
            ),
            response([text_block("Driving forward.")]),
        ],
        hat_results={"drive": 4},
    )
    result = run(service, "drive forward")
    assert hat_calls == [("drive", (50.0,), {"ttl_ms": 1000})]
    assert result["reply"] == "Driving forward."
    assert result["actions"] == [
        {"tool": "drive", "input": {"speed_pct": 50, "ttl_ms": 1000}, "ok": True, "result": 4}
    ]
    # The follow-up request passed the tool result back to the model.
    followup = client.calls[1]["messages"]
    assert followup[-1]["role"] == "user"
    tool_result = followup[-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["is_error"] is False
    assert json.loads(tool_result["content"]) == {"result": 4}


def test_invalid_tool_args_never_reach_hat():
    service, client, hat_calls = make_service(
        [
            response([tool_block("drive", {"speed_pct": 500})], stop_reason="tool_use"),
            response([text_block("That speed is out of range.")]),
        ]
    )
    result = run(service)
    assert hat_calls == []
    action = result["actions"][0]
    assert action["ok"] is False
    assert "speed_pct" in action["error"]
    tool_result = client.calls[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True


def test_unknown_tool_reported_as_error():
    service, _, hat_calls = make_service(
        [
            response([tool_block("self_destruct", {})], stop_reason="tool_use"),
            response([text_block("No such tool.")]),
        ]
    )
    result = run(service)
    assert hat_calls == []
    assert result["actions"][0]["ok"] is False
    assert "unknown tool" in result["actions"][0]["error"]


def test_hat_http_error_becomes_tool_error():
    service, _, _ = make_service(
        [
            response([tool_block("read_ultrasonic", {})], stop_reason="tool_use"),
            response([text_block("Sensor unavailable.")]),
        ],
        hat_results={
            "read_ultrasonic": HTTPException(status_code=503, detail="daemon down"),
        },
    )
    result = run(service)
    action = result["actions"][0]
    assert action["ok"] is False
    assert "daemon down" in action["error"]


def test_dataclass_results_serialized():
    from nomothetic.hat import UltrasonicResult

    service, _, _ = make_service(
        [
            response([tool_block("read_ultrasonic", {})], stop_reason="tool_use"),
            response([text_block("30 cm ahead.")]),
        ],
        hat_results={"read_ultrasonic": UltrasonicResult(distance_cm=30.5)},
    )
    result = run(service)
    assert result["actions"][0]["result"] == {"distance_cm": 30.5}


def test_stop_tool_wraps_count():
    service, _, hat_calls = make_service(
        [
            response([tool_block("stop", {})], stop_reason="tool_use"),
            response([text_block("Stopped.")]),
        ],
        hat_results={"stop_all_motors": 4},
    )
    result = run(service)
    assert hat_calls == [("stop_all_motors", (), {})]
    assert result["actions"][0]["result"] == {"stopped_channels": 4}


def test_tool_iteration_limit():
    burst = response([tool_block("read_ultrasonic", {})], stop_reason="tool_use")
    service, client, _ = make_service(
        [burst, burst, burst],
        hat_results={"read_ultrasonic": 1},
        max_tool_iterations=3,
    )
    result = run(service)
    assert result["stop_reason"] == "tool_iteration_limit"
    assert len(result["actions"]) == 3
    assert len(client.calls) == 3
    assert client.closed is True


def test_refusal_stop_reason_gets_fallback_text():
    service, _, _ = make_service([response([], stop_reason="refusal")])
    result = run(service)
    assert result["stop_reason"] == "refusal"
    assert "declined" in result["reply"]


# ============================================================================
# Routine tools
# ============================================================================


class FakeManager:
    def __init__(self):
        self.started = []
        self.stopped_all = False

    async def start(self, routine, params):
        self.started.append((routine, params))
        return {"routine": routine, "status": "running"}

    async def stop(self, routine):
        return None  # nothing running

    async def stop_all(self):
        self.stopped_all = True
        return []


def make_routine_service(responses, manager):
    async def no_hat(method, *args, **kwargs):
        raise AssertionError("hat_call must not be used by routine tools")

    client = FakeClient(responses)
    service = AiCommandService(
        hat_call=no_hat,
        get_routine_manager=lambda: manager,
        client_factory=lambda key: client,
    )
    return service, client


def test_start_routine_goes_through_manager():
    manager = FakeManager()
    service, _ = make_routine_service(
        [
            response(
                [tool_block("start_routine", {"routine": "explore", "params": {"x": 1}})],
                stop_reason="tool_use",
            ),
            response([text_block("Started explore.")]),
        ],
        manager,
    )
    result = run(service, "explore")
    assert manager.started == [("explore", {"x": 1})]
    assert result["actions"][0]["ok"] is True


def test_stop_routine_not_running_is_tool_error():
    manager = FakeManager()
    service, _ = make_routine_service(
        [
            response([tool_block("stop_routine", {"routine": "explore"})], stop_reason="tool_use"),
            response([text_block("Nothing to stop.")]),
        ],
        manager,
    )
    result = run(service, "stop exploring")
    action = result["actions"][0]
    assert action["ok"] is False
    assert "not running" in action["error"]


def test_routine_tools_without_manager_are_tool_errors():
    async def no_hat(method, *args, **kwargs):
        raise AssertionError("unexpected hat call")

    client = FakeClient(
        [
            response([tool_block("start_routine", {"routine": "explore"})], stop_reason="tool_use"),
            response([text_block("Routines unavailable.")]),
        ]
    )
    service = AiCommandService(hat_call=no_hat, client_factory=lambda key: client)
    result = run(service, "explore")
    action = result["actions"][0]
    assert action["ok"] is False
    assert "not available" in action["error"]


# ============================================================================
# Provider error mapping and availability
# ============================================================================


def test_run_without_sdk_raises_unavailable():
    async def hat(method, *args, **kwargs):
        return None

    service = AiCommandService(hat_call=hat)  # no injected client factory
    with patch.object(ai_command, "anthropic", None):
        with pytest.raises(AiUnavailableError):
            _run_coro(service.run_command([{"role": "user", "content": "x"}], api_key=TEST_KEY))


def test_provider_auth_error_mapped():
    anthropic = pytest.importorskip("anthropic")
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    exc = anthropic.AuthenticationError(
        "invalid x-api-key",
        response=httpx.Response(401, request=request),
        body=None,
    )
    service, client, _ = make_service([exc])
    with pytest.raises(AiProviderAuthError):
        run(service)
    assert client.closed is True


def test_provider_connection_error_mapped():
    anthropic = pytest.importorskip("anthropic")
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    exc = anthropic.APIConnectionError(request=request)
    service, _, _ = make_service([exc])
    with pytest.raises(AiProviderError) as excinfo:
        run(service)
    assert not isinstance(excinfo.value, AiProviderAuthError)


def test_unexpected_exception_propagates_and_closes_client():
    service, client, _ = make_service([RuntimeError("boom")])
    with pytest.raises(RuntimeError):
        run(service)
    assert client.closed is True


# ============================================================================
# Environment configuration
# ============================================================================


def test_model_and_max_tokens_from_env(monkeypatch):
    monkeypatch.setenv("NOMON_AI_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("NOMON_AI_MAX_TOKENS", "512")
    service, client, _ = make_service([response([text_block("ok")])])
    run(service)
    assert client.calls[0]["model"] == "claude-sonnet-5"
    assert client.calls[0]["max_tokens"] == 512


def test_out_of_range_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("NOMON_AI_MAX_TOKENS", "99999")
    service, client, _ = make_service([response([text_block("ok")])])
    run(service)
    assert client.calls[0]["max_tokens"] == 2048
