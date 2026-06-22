"""Tests for the RoutineManager process supervisor.

The full launch/stop/watchdog lifecycle is exercised without spawning real
processes by injecting a fake launcher that returns controllable fake processes.
No Pi hardware and no autonomon install required.
"""

import asyncio
import json
import signal
import sys
from typing import Any

import pytest

from nomothetic.routine_log_store import InvalidRoutineName, RoutineLogStore
from nomothetic.routine_manager import (
    RoutineAlreadyRunning,
    RoutineCredentialsError,
    RoutineManager,
    RoutineManagerConfig,
)


class FakeProcess:
    """A controllable stand-in for asyncio.subprocess.Process.

    Stays alive until signalled (SIGINT, when ``honor_signal``) or killed, or
    until the test sets it exited directly via :meth:`finish`.
    """

    def __init__(self, pid: int, honor_signal: bool = True) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.signals: list[int] = []
        self.killed = False
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        if sig == signal.SIGINT and self._honors:
            self.finish(0)

    def kill(self) -> None:
        self.killed = True
        self.finish(-9)

    def finish(self, returncode: int) -> None:
        if self.returncode is None:
            self.returncode = returncode
            self._exited.set()

    # honor flag set by the launcher
    _honors = True


class FakeLauncher:
    """Records launches and hands back fake processes for inspection."""

    def __init__(self, honor_signal: bool = True) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.processes: list[FakeProcess] = []
        self._honor = honor_signal

    async def __call__(self, argv: list[str], env: dict[str, str]) -> FakeProcess:
        self.calls.append((argv, env))
        proc = FakeProcess(pid=4000 + len(self.processes))
        proc._honors = self._honor
        self.processes.append(proc)
        return proc


def _config(**overrides: Any) -> RoutineManagerConfig:
    base: dict[str, Any] = {
        "plugin_token": "test-token",
        "default_heartbeat_timeout_s": 100.0,
        "heartbeat_timeout_ceiling_s": 300.0,
        "grace_period_s": 0.1,
    }
    base.update(overrides)
    return RoutineManagerConfig(**base)


async def _await_monitor(manager: RoutineManager, routine: str) -> None:
    """Await the routine's monitor task to completion (white-box helper)."""
    entry = manager._running.get(routine)
    if entry is not None and entry.monitor_task is not None:
        await asyncio.wait_for(entry.monitor_task, timeout=2.0)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_from_env_reads_values():
    cfg = RoutineManagerConfig.from_env(
        {
            "NOMON_PLUGIN_TOKEN": "x",
            "NOMON_DEVICE_ID": "perceptua",
            "NOMON_AUTONOMON_DEVICE_URL": "https://dev:8443",
            "NOMON_ROUTINE_HEARTBEAT_TIMEOUT_S": "30",
            "NOMON_ROUTINE_MAX_DURATION_S": "1800",
        }
    )
    assert cfg.device_id == "perceptua"
    assert cfg.device_url == "https://dev:8443"
    assert cfg.default_heartbeat_timeout_s == 30.0
    assert cfg.max_duration_s == 1800.0
    assert cfg.has_credentials()


def test_config_from_env_bad_heartbeat_falls_back():
    cfg = RoutineManagerConfig.from_env({"NOMON_ROUTINE_HEARTBEAT_TIMEOUT_S": "abc"})
    assert cfg.default_heartbeat_timeout_s == 120.0


def test_config_from_env_absolute_cap_defaults_off():
    # No NOMON_ROUTINE_MAX_DURATION_S → no absolute cap (rely on heartbeat).
    cfg = RoutineManagerConfig.from_env({"NOMON_PLUGIN_TOKEN": "t"})
    assert cfg.max_duration_s is None


def test_config_no_credentials():
    assert RoutineManagerConfig().has_credentials() is False


def test_config_resolves_bin_beside_interpreter(tmp_path, monkeypatch):
    # Legacy fallback: with no published catalogue, a script beside the running
    # interpreter is used (e.g. an older same-venv install).
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python").write_text("")
    (bindir / "nomon-autonomon").write_text("")
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    cfg = RoutineManagerConfig.from_env(
        {"NOMON_PLUGIN_TOKEN": "t", "NOMON_ROUTINE_CATALOG_PATH": str(tmp_path / "none.json")}
    )
    assert cfg.autonomon_bin == str(bindir / "nomon-autonomon")


def test_config_bin_env_override_wins(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "nomon-autonomon").write_text("")
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    cfg = RoutineManagerConfig.from_env(
        {"NOMON_PLUGIN_TOKEN": "t", "NOMON_AUTONOMON_BIN": "/opt/nomon-autonomon"}
    )
    assert cfg.autonomon_bin == "/opt/nomon-autonomon"


def test_config_bin_falls_back_to_bare_name(tmp_path, monkeypatch):
    # No published catalogue and no sibling script → bare name (resolved via PATH).
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    cfg = RoutineManagerConfig.from_env(
        {"NOMON_PLUGIN_TOKEN": "t", "NOMON_ROUTINE_CATALOG_PATH": str(tmp_path / "none.json")}
    )
    assert cfg.autonomon_bin == "nomon-autonomon"


def test_config_resolves_bin_from_published_catalogue(tmp_path, monkeypatch):
    # The decoupled path (ADR-005): autonomon publishes its own-venv CLI path in
    # the catalogue, and the gateway uses it without autonomon in its own venv.
    bin_path = tmp_path / "autonomon-venv" / "bin" / "nomon-autonomon"
    bin_path.parent.mkdir(parents=True)
    bin_path.write_text("")
    catalogue = tmp_path / "routine_catalog.json"
    catalogue.write_text(json.dumps({"routines": ["explore"], "autonomon_bin": str(bin_path)}))
    # No sibling beside the interpreter, so only the catalogue can supply the path.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    cfg = RoutineManagerConfig.from_env(
        {"NOMON_PLUGIN_TOKEN": "t", "NOMON_ROUTINE_CATALOG_PATH": str(catalogue)}
    )
    assert cfg.autonomon_bin == str(bin_path)


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_launches_with_expected_env():
    launcher = FakeLauncher()
    manager = RoutineManager(
        _config(device_url="https://dev:8443", device_id="perceptua"), launcher=launcher
    )
    info = await manager.start("explore", {"forward_speed_pct": 60})

    assert info["routine"] == "explore"
    assert info["pid"] == launcher.processes[0].pid
    assert info["status"] == "running"

    argv, env = launcher.calls[0]
    assert argv == ["nomon-autonomon"]
    assert json.loads(env["NOMON_PLUGIN_PARAMS"]) == {
        "forward_speed_pct": 60,
        "routine": "explore",
    }
    assert env["NOMON_DEVICE_URL"] == "https://dev:8443"
    assert env["NOMON_DEVICE_ID"] == "perceptua"
    assert env["NOMON_PLUGIN_TOKEN"] == "test-token"
    assert "NOMON_PLUGIN_KEY" not in env

    await manager.shutdown()


@pytest.mark.asyncio
async def test_start_prefers_key_over_token():
    launcher = FakeLauncher()
    manager = RoutineManager(
        _config(plugin_key="/etc/nomon/plugin.key", plugin_token="tok"), launcher=launcher
    )
    await manager.start("explore", {})
    _argv, env = launcher.calls[0]
    assert env["NOMON_PLUGIN_KEY"] == "/etc/nomon/plugin.key"
    assert "NOMON_PLUGIN_TOKEN" not in env
    await manager.shutdown()


@pytest.mark.asyncio
async def test_start_without_credentials_raises():
    manager = RoutineManager(RoutineManagerConfig(), launcher=FakeLauncher())
    with pytest.raises(RoutineCredentialsError):
        await manager.start("explore", {})


@pytest.mark.asyncio
async def test_start_rejects_invalid_routine_name():
    manager = RoutineManager(_config(), launcher=FakeLauncher())
    with pytest.raises(InvalidRoutineName):
        await manager.start("bad name", {})


@pytest.mark.asyncio
async def test_start_twice_conflicts():
    manager = RoutineManager(_config(), launcher=FakeLauncher())
    await manager.start("explore", {})
    with pytest.raises(RoutineAlreadyRunning):
        await manager.start("explore", {})
    await manager.shutdown()


@pytest.mark.asyncio
async def test_heartbeat_timeout_defaults_and_clamps_to_ceiling():
    manager = RoutineManager(
        _config(default_heartbeat_timeout_s=120.0, heartbeat_timeout_ceiling_s=300.0),
        launcher=FakeLauncher(),
    )
    default_run = await manager.start("explore", {})
    assert default_run["heartbeat_timeout_s"] == 120.0

    clamped_run = await manager.start("follow-user", {}, heartbeat_timeout_s=10_000)
    assert clamped_run["heartbeat_timeout_s"] == 300.0

    await manager.shutdown()


@pytest.mark.asyncio
async def test_heartbeat_timeout_floored_to_minimum():
    # A client-supplied tiny value is floored so the guard stays meaningful.
    manager = RoutineManager(_config(), launcher=FakeLauncher())
    run = await manager.start("explore", {}, heartbeat_timeout_s=1)
    assert run["heartbeat_timeout_s"] == 5.0
    await manager.shutdown()


@pytest.mark.asyncio
async def test_absolute_cap_defaults_off_but_passes_through():
    manager = RoutineManager(_config(), launcher=FakeLauncher())
    default_run = await manager.start("explore", {})
    assert default_run["max_duration_s"] is None  # no cap by default

    capped_run = await manager.start("follow-user", {}, max_duration_s=1800)
    assert capped_run["max_duration_s"] == 1800.0

    await manager.shutdown()


@pytest.mark.asyncio
async def test_start_rejects_nonpositive_durations():
    manager = RoutineManager(_config(), launcher=FakeLauncher())
    with pytest.raises(ValueError):
        await manager.start("explore", {}, max_duration_s=0)
    with pytest.raises(ValueError):
        await manager.start("explore", {}, heartbeat_timeout_s=0)


# ---------------------------------------------------------------------------
# stop / stop_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_signals_and_records_reason():
    store = RoutineLogStore()
    launcher = FakeLauncher()
    manager = RoutineManager(_config(), log_store=store, launcher=launcher)
    await manager.start("explore", {})

    result = await manager.stop("explore")
    assert result is not None and result["routine"] == "explore"
    assert signal.SIGINT in launcher.processes[0].signals
    assert await manager.list_running() == []

    snap = store.get("explore")
    assert snap["status"] == "stopped"
    assert snap["events"][-1]["data"]["reason"] == "operator_request"


@pytest.mark.asyncio
async def test_stop_unknown_returns_none():
    manager = RoutineManager(_config(), launcher=FakeLauncher())
    assert await manager.stop("explore") is None


@pytest.mark.asyncio
async def test_stop_escalates_to_kill_when_sigint_ignored():
    launcher = FakeLauncher(honor_signal=False)
    manager = RoutineManager(_config(grace_period_s=0.05), launcher=launcher)
    await manager.start("explore", {})

    await manager.stop("explore")
    proc = launcher.processes[0]
    assert signal.SIGINT in proc.signals
    assert proc.killed is True


@pytest.mark.asyncio
async def test_stop_all_stops_every_routine():
    launcher = FakeLauncher()
    manager = RoutineManager(_config(), launcher=launcher)
    await manager.start("explore", {})
    await manager.start("follow-user", {})

    stopped = await manager.stop_all()
    assert {s["routine"] for s in stopped} == {"explore", "follow-user"}
    assert await manager.list_running() == []


# ---------------------------------------------------------------------------
# watchdog and natural exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_timeout_stops_routine():
    store = RoutineLogStore()
    launcher = FakeLauncher()
    manager = RoutineManager(
        _config(default_heartbeat_timeout_s=0.05), log_store=store, launcher=launcher
    )
    await manager.start("explore", {})
    await _await_monitor(manager, "explore")

    assert await manager.list_running() == []
    snap = store.get("explore")
    assert snap["status"] == "stopped"
    assert snap["events"][-1]["data"]["reason"] == "heartbeat_timeout"


@pytest.mark.asyncio
async def test_heartbeat_keeps_routine_alive_past_timeout():
    launcher = FakeLauncher()
    manager = RoutineManager(_config(default_heartbeat_timeout_s=0.1), launcher=launcher)
    await manager.start("explore", {})

    # Send heartbeats faster than the 0.1 s timeout for longer than one window.
    for _ in range(5):
        await asyncio.sleep(0.04)
        hb = await manager.heartbeat("explore")
        assert hb is not None and hb["status"] == "running"
        assert hb["seconds_remaining"] > 0

    # Still running after >2 timeout windows of heartbeating.
    assert await manager.list_running() != []

    # Stop heartbeating → the lease lapses and the routine stops.
    await _await_monitor(manager, "explore")
    assert await manager.list_running() == []


@pytest.mark.asyncio
async def test_absolute_max_duration_stops_even_with_heartbeats():
    store = RoutineLogStore()
    launcher = FakeLauncher()
    manager = RoutineManager(
        _config(default_heartbeat_timeout_s=100.0, max_duration_s=0.05),
        log_store=store,
        launcher=launcher,
    )
    await manager.start("explore", {})
    await _await_monitor(manager, "explore")

    snap = store.get("explore")
    assert snap["status"] == "stopped"
    assert snap["events"][-1]["data"]["reason"] == "max_duration_exceeded"


@pytest.mark.asyncio
async def test_heartbeat_unknown_routine_returns_none():
    manager = RoutineManager(_config(), launcher=FakeLauncher())
    assert await manager.heartbeat("explore") is None


@pytest.mark.asyncio
async def test_natural_exit_cleans_up_without_recording():
    store = RoutineLogStore()
    launcher = FakeLauncher()
    manager = RoutineManager(_config(), log_store=store, launcher=launcher)
    await manager.start("explore", {})

    # Simulate the plugin finishing on its own.
    launcher.processes[0].finish(0)
    await _await_monitor(manager, "explore")

    assert await manager.list_running() == []
    # The manager records nothing for a self-exit (the plugin reports its own).
    assert store.get("explore") is None


@pytest.mark.asyncio
async def test_shutdown_stops_running_routines():
    launcher = FakeLauncher()
    manager = RoutineManager(_config(), launcher=launcher)
    await manager.start("explore", {})
    await manager.start("follow-user", {})

    await manager.shutdown()
    assert await manager.list_running() == []
    assert all(p.returncode is not None for p in launcher.processes)
