"""Tests for the nomothetic.hat module (HatClient).

All tests use a mock Unix socket server in a background thread — no
Raspberry Pi hardware or nomopractic daemon required.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

import pytest

from nomothetic.hat import (
    GrayscaleResult,
    HatClient,
    HatConnectionError,
    HatError,
    HatHealthResult,
    McuStatusResult,
    MotorLeaseEntry,
    MotorStatusResult,
    ServoLeaseEntry,
    ServoStatusResult,
)

# ---------------------------------------------------------------------------
# Mock server helpers
# ---------------------------------------------------------------------------

_DEFAULT_RESPONSES: dict[str, Any] = {
    "health": {
        "status": "ok",
        "version": "0.1.0",
        "hat_address": "0x14",
        "i2c_bus": 1,
        "uptime_s": 42,
    },
    "get_battery_voltage": {"voltage_v": 7.5},
    "set_servo_pulse_us": {"channel": 0, "pulse_us": 1500},
    "set_servo_angle": {"channel": 0, "angle_deg": 90.0, "pulse_us": 1611},
    "reset_mcu": {"reset_ms": 10},
    "get_servo_status": {
        "active_leases": [
            {"channel": 2, "ttl_remaining_ms": 350, "conn_id": 7},
        ]
    },
    "get_mcu_status": {
        "resets_since_start": 3,
        "last_reset_s_ago": 120,
    },
    "set_motor_speed": {"channel": 0, "speed_pct": 50.0},
    "stop_all_motors": {"stopped": 2},
    "get_motor_status": {
        "active_leases": [
            {"channel": 0, "ttl_remaining_ms": 312, "conn_id": 4},
            {"channel": 1, "ttl_remaining_ms": 198, "conn_id": 4},
        ]
    },
    "drive": {"speed_pct": 60.0, "motors": 2},
    "steer": {"servo": "steering", "channel": 2, "angle_deg": 90.0, "pulse_us": 1500},
    "pan_camera": {"servo": "camera_pan", "channel": 0, "angle_deg": 45.0, "pulse_us": 1000},
    "tilt_camera": {"servo": "camera_tilt", "channel": 1, "angle_deg": 60.0, "pulse_us": 1167},
    "read_grayscale": {"channels": [0, 1, 2], "values": [1200, 3000, 800]},
}


def _run_mock_server(
    sock_path: str,
    responses: dict[str, Any],
    *,
    error_method: str | None = None,
    max_connections: int = 4,
    ready_event: threading.Event | None = None,
) -> None:
    """Serve a minimal nomopractic mock on *sock_path*.

    Each incoming line is parsed as JSON; the method is looked up in
    *responses* and returned as ``{"id":…,"ok":true,"result":…}``.
    If *error_method* matches the request method, an error response is
    returned instead.  The server exits after *max_connections* closed
    connections.
    """
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(sock_path)
    srv.listen(max_connections)
    srv.settimeout(0.5)

    if ready_event:
        ready_event.set()

    connections_handled = 0

    def _handle(conn: socket.socket) -> None:
        with conn:
            conn.settimeout(2.0)
            f = conn.makefile("rb")
            while True:
                line = f.readline()
                if not line:
                    break
                try:
                    req = json.loads(line)
                except json.JSONDecodeError:
                    break
                method = req.get("method", "")
                req_id = req.get("id", "")
                if method == error_method:
                    resp: dict[str, Any] = {
                        "id": req_id,
                        "ok": False,
                        "error": {"code": "HARDWARE_ERROR", "message": "mock error"},
                    }
                elif method in responses:
                    resp = {"id": req_id, "ok": True, "result": responses[method]}
                else:
                    resp = {
                        "id": req_id,
                        "ok": False,
                        "error": {
                            "code": "UNKNOWN_METHOD",
                            "message": f"No method '{method}'",
                        },
                    }
                conn.sendall((json.dumps(resp) + "\n").encode())

    try:
        while connections_handled < max_connections:
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                break
            t = threading.Thread(target=_handle, args=(conn,), daemon=True)
            t.start()
            connections_handled += 1
    finally:
        srv.close()


@pytest.fixture
def mock_server(tmp_path):
    """Start a standard mock nomopractic server; yield the socket path."""
    sock_path = str(tmp_path / "nomopractic.sock")
    ready = threading.Event()
    t = threading.Thread(
        target=_run_mock_server,
        args=(sock_path, _DEFAULT_RESPONSES),
        kwargs={"ready_event": ready},
        daemon=True,
    )
    t.start()
    ready.wait(timeout=2.0)
    yield sock_path
    t.join(timeout=2.0)


@pytest.fixture
def mock_server_with_error(tmp_path):
    """Start a mock server that returns HARDWARE_ERROR for get_battery_voltage."""
    sock_path = str(tmp_path / "nomopractic.sock")
    ready = threading.Event()
    t = threading.Thread(
        target=_run_mock_server,
        args=(sock_path, _DEFAULT_RESPONSES),
        kwargs={"error_method": "get_battery_voltage", "ready_event": ready},
        daemon=True,
    )
    t.start()
    ready.wait(timeout=2.0)
    yield sock_path
    t.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def test_connect_unavailable_socket_raises(tmp_path):
    """`connect()` raises HatConnectionError when socket does not exist."""
    client = HatClient(socket_path=str(tmp_path / "missing.sock"))
    with pytest.raises(HatConnectionError) as exc_info:
        client.connect()
    assert exc_info.value.code == "CONNECTION_ERROR"


def test_context_manager_opens_and_closes(mock_server):
    """`with HatClient()` connects and disconnects cleanly."""
    with HatClient(socket_path=mock_server) as hat:
        assert hat._sock is not None
    assert hat._sock is None


def test_close_is_idempotent(mock_server):
    """`close()` may be called multiple times without error."""
    hat = HatClient(socket_path=mock_server)
    hat.close()  # never connected
    hat.close()  # still fine


def test_lazy_connect_on_first_request(mock_server):
    """A request auto-connects if the client is not yet connected."""
    hat = HatClient(socket_path=mock_server)
    assert hat._sock is None
    voltage = hat.get_battery_voltage()
    assert hat._sock is not None
    assert voltage == pytest.approx(7.5)
    hat.close()


def test_socket_path_from_env_var(monkeypatch, mock_server):
    """`NOMON_HAT_SOCKET_PATH` overrides the default socket path."""
    monkeypatch.setenv("NOMON_HAT_SOCKET_PATH", mock_server)
    hat = HatClient()
    assert hat._socket_path == mock_server
    assert hat.get_battery_voltage() == pytest.approx(7.5)
    hat.close()


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------


def test_health_returns_dataclass(mock_server):
    """`health()` returns a populated `HatHealthResult`."""
    with HatClient(socket_path=mock_server) as hat:
        result = hat.health()
    assert isinstance(result, HatHealthResult)
    assert result.status == "ok"
    assert result.version == "0.1.0"
    assert result.hat_address == "0x14"
    assert result.i2c_bus == 1
    assert result.uptime_s == 42


# ---------------------------------------------------------------------------
# get_battery_voltage()
# ---------------------------------------------------------------------------


def test_get_battery_voltage_success(mock_server):
    """`get_battery_voltage()` returns the float from the result."""
    with HatClient(socket_path=mock_server) as hat:
        v = hat.get_battery_voltage()
    assert v == pytest.approx(7.5)


def test_get_battery_voltage_hardware_error(mock_server_with_error):
    """HARDWARE_ERROR from daemon raises `HatError`."""
    with HatClient(socket_path=mock_server_with_error) as hat:
        with pytest.raises(HatError) as exc_info:
            hat.get_battery_voltage()
    assert exc_info.value.code == "HARDWARE_ERROR"


# ---------------------------------------------------------------------------
# set_servo_pulse_us()
# ---------------------------------------------------------------------------


def test_set_servo_pulse_us_success(mock_server):
    """`set_servo_pulse_us()` returns without error on success."""
    with HatClient(socket_path=mock_server) as hat:
        hat.set_servo_pulse_us(channel=0, pulse_us=1500)


def test_set_servo_pulse_us_bad_channel(mock_server):
    """channel out of range raises `ValueError` before sending."""
    hat = HatClient(socket_path=mock_server)
    with pytest.raises(ValueError, match="channel"):
        hat.set_servo_pulse_us(channel=12, pulse_us=1500)


def test_set_servo_pulse_us_pulse_too_low(mock_server):
    """`pulse_us` below 500 raises `ValueError`."""
    hat = HatClient(socket_path=mock_server)
    with pytest.raises(ValueError, match="pulse_us"):
        hat.set_servo_pulse_us(channel=0, pulse_us=499)


def test_set_servo_pulse_us_pulse_too_high(mock_server):
    """`pulse_us` above 2500 raises `ValueError`."""
    hat = HatClient(socket_path=mock_server)
    with pytest.raises(ValueError, match="pulse_us"):
        hat.set_servo_pulse_us(channel=0, pulse_us=2501)


# ---------------------------------------------------------------------------
# set_servo_angle()
# ---------------------------------------------------------------------------


def test_set_servo_angle_success(mock_server):
    """`set_servo_angle()` returns without error on success."""
    with HatClient(socket_path=mock_server) as hat:
        hat.set_servo_angle(channel=0, angle_deg=90.0)


def test_set_servo_angle_boundaries(mock_server):
    """0.0° and 180.0° are within the valid range."""
    with HatClient(socket_path=mock_server) as hat:
        hat.set_servo_angle(channel=0, angle_deg=0.0)
        hat.set_servo_angle(channel=0, angle_deg=180.0)


def test_set_servo_angle_bad_channel(mock_server):
    """Negative channel raises `ValueError`."""
    hat = HatClient(socket_path=mock_server)
    with pytest.raises(ValueError, match="channel"):
        hat.set_servo_angle(channel=-1, angle_deg=90.0)


def test_set_servo_angle_out_of_range(mock_server):
    """angle_deg > 180 raises `ValueError`."""
    hat = HatClient(socket_path=mock_server)
    with pytest.raises(ValueError, match="angle_deg"):
        hat.set_servo_angle(channel=0, angle_deg=181.0)


# ---------------------------------------------------------------------------
# reset_mcu()
# ---------------------------------------------------------------------------


def test_reset_mcu_success(mock_server):
    """`reset_mcu()` returns without error on success."""
    with HatClient(socket_path=mock_server) as hat:
        hat.reset_mcu()


# ---------------------------------------------------------------------------
# Unknown method
# ---------------------------------------------------------------------------


def test_unknown_method_raises_hat_error(mock_server):
    """Server returns UNKNOWN_METHOD → `HatError` raised."""
    with HatClient(socket_path=mock_server) as hat:
        with pytest.raises(HatError) as exc_info:
            # Directly exercise the internal request path with a bogus method.
            hat._request("nonexistent_method", {})
    assert exc_info.value.code == "UNKNOWN_METHOD"


# ---------------------------------------------------------------------------
# Multiple sequential requests on same connection
# ---------------------------------------------------------------------------


def test_multiple_requests_on_same_connection(mock_server):
    """Several requests reuse the same socket connection."""
    with HatClient(socket_path=mock_server) as hat:
        sock_before = hat._sock
        hat.get_battery_voltage()
        hat.health()
        hat.get_battery_voltage()
        sock_after = hat._sock
    # Same socket object throughout (no reconnect triggered).
    assert sock_before is sock_after


# ---------------------------------------------------------------------------
# Reconnect on broken connection
# ---------------------------------------------------------------------------


def _run_drop_after_one(sock_path: str, ready_event: threading.Event) -> None:
    """Serve one request, then close the connection to simulate a drop.
    After the drop, serves one more connection normally so the retry succeeds.
    """
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(sock_path)
    srv.listen(4)
    srv.settimeout(2.0)
    ready_event.set()

    try:
        # First connection: serve health, then shut down to force FIN.
        conn, _ = srv.accept()
        conn.settimeout(2.0)
        # Receive the health request via raw recv to avoid makefile dup fd issues.
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
        req = json.loads(buf.rstrip())
        resp = {
            "id": req["id"],
            "ok": True,
            "result": _DEFAULT_RESPONSES["health"],
        }
        conn.sendall((json.dumps(resp) + "\n").encode())
        # Force TCP FIN immediately; shutdown() bypasses dup'd-fd issues.
        conn.shutdown(socket.SHUT_RDWR)
        conn.close()

        # Second connection: serve normally.
        conn2, _ = srv.accept()
        conn2.settimeout(2.0)
        f2 = conn2.makefile("rb")
        try:
            while True:
                line = f2.readline()
                if not line:
                    break
                req2 = json.loads(line)
                method = req2.get("method", "")
                resp2: dict[str, Any] = {
                    "id": req2["id"],
                    "ok": True,
                    "result": _DEFAULT_RESPONSES.get(method, {}),
                }
                conn2.sendall((json.dumps(resp2) + "\n").encode())
        finally:
            f2.close()
            conn2.shutdown(socket.SHUT_RDWR)
            conn2.close()
    finally:
        srv.close()


def test_reconnect_on_broken_connection(tmp_path):
    """Client reconnects transparently when the daemon closes the connection."""
    sock_path = str(tmp_path / "nomopractic.sock")
    ready = threading.Event()
    t = threading.Thread(
        target=_run_drop_after_one,
        args=(sock_path, ready),
        daemon=True,
    )
    t.start()
    ready.wait(timeout=2.0)

    with HatClient(socket_path=sock_path, timeout_s=2.0) as hat:
        # First request succeeds.
        result = hat.health()
        assert result.status == "ok"

        # Give the server time to close its end.
        time.sleep(0.05)

        # This request fails on the broken pipe and triggers a reconnect.
        voltage = hat.get_battery_voltage()
        assert voltage == pytest.approx(7.5)

    t.join(timeout=3.0)


# ---------------------------------------------------------------------------
# get_servo_status()
# ---------------------------------------------------------------------------


def test_get_servo_status_returns_dataclass(mock_server):
    """`get_servo_status()` returns a `ServoStatusResult` with lease entries."""
    with HatClient(socket_path=mock_server) as hat:
        result = hat.get_servo_status()
    assert isinstance(result, ServoStatusResult)
    assert len(result.active_leases) == 1
    lease = result.active_leases[0]
    assert isinstance(lease, ServoLeaseEntry)
    assert lease.channel == 2
    assert lease.ttl_remaining_ms == 350
    assert lease.conn_id == 7


def test_get_servo_status_empty_list(tmp_path):
    """`get_servo_status()` handles an empty active_leases list."""
    sock_path = str(tmp_path / "nomopractic.sock")
    responses = dict(_DEFAULT_RESPONSES)
    responses["get_servo_status"] = {"active_leases": []}
    ready = threading.Event()
    t = threading.Thread(
        target=_run_mock_server,
        args=(sock_path, responses),
        kwargs={"ready_event": ready},
        daemon=True,
    )
    t.start()
    ready.wait(timeout=2.0)

    with HatClient(socket_path=sock_path) as hat:
        result = hat.get_servo_status()
    assert result.active_leases == []

    t.join(timeout=2.0)


# ---------------------------------------------------------------------------
# get_mcu_status()
# ---------------------------------------------------------------------------


def test_get_mcu_status_returns_dataclass(mock_server):
    """`get_mcu_status()` returns a populated `McuStatusResult`."""
    with HatClient(socket_path=mock_server) as hat:
        result = hat.get_mcu_status()
    assert isinstance(result, McuStatusResult)
    assert result.resets_since_start == 3
    assert result.last_reset_s_ago == 120


def test_get_mcu_status_no_previous_reset(tmp_path):
    """`get_mcu_status()` returns ``last_reset_s_ago=None`` when never reset."""
    sock_path = str(tmp_path / "nomopractic.sock")
    responses = dict(_DEFAULT_RESPONSES)
    responses["get_mcu_status"] = {"resets_since_start": 0, "last_reset_s_ago": None}
    ready = threading.Event()
    t = threading.Thread(
        target=_run_mock_server,
        args=(sock_path, responses),
        kwargs={"ready_event": ready},
        daemon=True,
    )
    t.start()
    ready.wait(timeout=2.0)

    with HatClient(socket_path=sock_path) as hat:
        result = hat.get_mcu_status()
    assert result.resets_since_start == 0
    assert result.last_reset_s_ago is None

    t.join(timeout=2.0)


# ---------------------------------------------------------------------------
# set_motor_speed()
# ---------------------------------------------------------------------------


def test_set_motor_speed_success(mock_server):
    """`set_motor_speed()` returns without error on success."""
    with HatClient(socket_path=mock_server) as hat:
        hat.set_motor_speed(channel=0, speed_pct=50.0)


def test_set_motor_speed_full_reverse(mock_server):
    """Negative speed_pct (full reverse) is accepted."""
    with HatClient(socket_path=mock_server) as hat:
        hat.set_motor_speed(channel=1, speed_pct=-100.0)


def test_set_motor_speed_stop(mock_server):
    """speed_pct=0.0 (stop) is accepted."""
    with HatClient(socket_path=mock_server) as hat:
        hat.set_motor_speed(channel=0, speed_pct=0.0)


def test_set_motor_speed_bad_channel_high(mock_server):
    """channel above 3 raises `ValueError` before sending."""
    hat = HatClient(socket_path=mock_server)
    with pytest.raises(ValueError, match="channel"):
        hat.set_motor_speed(channel=4, speed_pct=50.0)


def test_set_motor_speed_bad_channel_negative(mock_server):
    """Negative channel raises `ValueError`."""
    hat = HatClient(socket_path=mock_server)
    with pytest.raises(ValueError, match="channel"):
        hat.set_motor_speed(channel=-1, speed_pct=0.0)


def test_set_motor_speed_pct_too_high(mock_server):
    """`speed_pct` above 100 raises `ValueError`."""
    hat = HatClient(socket_path=mock_server)
    with pytest.raises(ValueError, match="speed_pct"):
        hat.set_motor_speed(channel=0, speed_pct=101.0)


def test_set_motor_speed_pct_too_low(mock_server):
    """`speed_pct` below -100 raises `ValueError`."""
    hat = HatClient(socket_path=mock_server)
    with pytest.raises(ValueError, match="speed_pct"):
        hat.set_motor_speed(channel=0, speed_pct=-101.0)


# ---------------------------------------------------------------------------
# stop_all_motors()
# ---------------------------------------------------------------------------


def test_stop_all_motors_success(mock_server):
    """`stop_all_motors()` returns the stopped count from the daemon."""
    with HatClient(socket_path=mock_server) as hat:
        stopped = hat.stop_all_motors()
    assert stopped == 2


def test_stop_all_motors_zero(tmp_path):
    """`stop_all_motors()` returns 0 when no motors are configured."""
    sock_path = str(tmp_path / "nomopractic.sock")
    responses = dict(_DEFAULT_RESPONSES)
    responses["stop_all_motors"] = {"stopped": 0}
    ready = threading.Event()
    t = threading.Thread(
        target=_run_mock_server,
        args=(sock_path, responses),
        kwargs={"ready_event": ready},
        daemon=True,
    )
    t.start()
    ready.wait(timeout=2.0)

    with HatClient(socket_path=sock_path) as hat:
        stopped = hat.stop_all_motors()
    assert stopped == 0

    t.join(timeout=2.0)


# ---------------------------------------------------------------------------
# get_motor_status()
# ---------------------------------------------------------------------------


def test_get_motor_status_returns_dataclass(mock_server):
    """`get_motor_status()` returns a `MotorStatusResult` with lease entries."""
    with HatClient(socket_path=mock_server) as hat:
        result = hat.get_motor_status()
    assert isinstance(result, MotorStatusResult)
    assert len(result.active_leases) == 2
    lease = result.active_leases[0]
    assert isinstance(lease, MotorLeaseEntry)
    assert lease.channel == 0
    assert lease.ttl_remaining_ms == 312
    assert lease.conn_id == 4


def test_get_motor_status_empty_list(tmp_path):
    """`get_motor_status()` handles an empty active_leases list."""
    sock_path = str(tmp_path / "nomopractic.sock")
    responses = dict(_DEFAULT_RESPONSES)
    responses["get_motor_status"] = {"active_leases": []}
    ready = threading.Event()
    t = threading.Thread(
        target=_run_mock_server,
        args=(sock_path, responses),
        kwargs={"ready_event": ready},
        daemon=True,
    )
    t.start()
    ready.wait(timeout=2.0)

    with HatClient(socket_path=sock_path) as hat:
        result = hat.get_motor_status()
    assert result.active_leases == []

    t.join(timeout=2.0)


# ---------------------------------------------------------------------------
# drive()
# ---------------------------------------------------------------------------


def test_drive_returns_motor_count(mock_server):
    """`drive()` returns the number of motors commanded."""
    with HatClient(socket_path=mock_server) as hat:
        motors = hat.drive(speed_pct=60.0)
    assert motors == 2


def test_drive_forward(mock_server):
    """`drive()` accepts positive speed_pct."""
    with HatClient(socket_path=mock_server) as hat:
        motors = hat.drive(speed_pct=100.0)
    assert motors == 2


def test_drive_reverse(mock_server):
    """`drive()` accepts negative speed_pct for reverse."""
    with HatClient(socket_path=mock_server) as hat:
        motors = hat.drive(speed_pct=-50.0)
    assert motors == 2


def test_drive_stop(mock_server):
    """`drive(0.0)` stops all motors."""
    with HatClient(socket_path=mock_server) as hat:
        motors = hat.drive(speed_pct=0.0)
    assert motors == 2


def test_drive_speed_too_high(mock_server):
    """`speed_pct` above 100 raises `ValueError`."""
    hat = HatClient(socket_path=mock_server)
    with pytest.raises(ValueError, match="speed_pct"):
        hat.drive(speed_pct=101.0)


def test_drive_speed_too_low(mock_server):
    """`speed_pct` below -100 raises `ValueError`."""
    hat = HatClient(socket_path=mock_server)
    with pytest.raises(ValueError, match="speed_pct"):
        hat.drive(speed_pct=-101.0)


# ---------------------------------------------------------------------------
# steer()
# ---------------------------------------------------------------------------


def test_steer_succeeds(mock_server):
    """`steer()` completes without error."""
    with HatClient(socket_path=mock_server) as hat:
        hat.steer(angle_deg=90.0)


def test_steer_full_left(mock_server):
    """`steer()` accepts 0° (full left)."""
    with HatClient(socket_path=mock_server) as hat:
        hat.steer(angle_deg=0.0)


def test_steer_full_right(mock_server):
    """`steer()` accepts 180° (full right)."""
    with HatClient(socket_path=mock_server) as hat:
        hat.steer(angle_deg=180.0)


def test_steer_angle_too_high(mock_server):
    """`angle_deg` above 180 raises `ValueError`."""
    hat = HatClient(socket_path=mock_server)
    with pytest.raises(ValueError, match="angle_deg"):
        hat.steer(angle_deg=181.0)


def test_steer_angle_too_low(mock_server):
    """`angle_deg` below 0 raises `ValueError`."""
    hat = HatClient(socket_path=mock_server)
    with pytest.raises(ValueError, match="angle_deg"):
        hat.steer(angle_deg=-1.0)


# ---------------------------------------------------------------------------
# pan_camera()
# ---------------------------------------------------------------------------


def test_pan_camera_succeeds(mock_server):
    """`pan_camera()` completes without error."""
    with HatClient(socket_path=mock_server) as hat:
        hat.pan_camera(angle_deg=45.0)


def test_pan_camera_centre(mock_server):
    """`pan_camera(90)` is the centred position."""
    with HatClient(socket_path=mock_server) as hat:
        hat.pan_camera(angle_deg=90.0)


def test_pan_camera_angle_out_of_range(mock_server):
    """`angle_deg` outside 0–180 raises `ValueError`."""
    hat = HatClient(socket_path=mock_server)
    with pytest.raises(ValueError, match="angle_deg"):
        hat.pan_camera(angle_deg=200.0)


# ---------------------------------------------------------------------------
# tilt_camera()
# ---------------------------------------------------------------------------


def test_tilt_camera_succeeds(mock_server):
    """`tilt_camera()` completes without error."""
    with HatClient(socket_path=mock_server) as hat:
        hat.tilt_camera(angle_deg=60.0)


def test_tilt_camera_angle_out_of_range(mock_server):
    """`angle_deg` outside 0–180 raises `ValueError`."""
    hat = HatClient(socket_path=mock_server)
    with pytest.raises(ValueError, match="angle_deg"):
        hat.tilt_camera(angle_deg=-5.0)


# ---------------------------------------------------------------------------
# read_grayscale()
# ---------------------------------------------------------------------------


def test_read_grayscale_returns_dataclass(mock_server):
    """`read_grayscale()` returns a `GrayscaleResult` with three values."""
    with HatClient(socket_path=mock_server) as hat:
        result = hat.read_grayscale()
    assert isinstance(result, GrayscaleResult)
    assert result.channels == [0, 1, 2]
    assert result.values == [1200, 3000, 800]


def test_read_grayscale_three_values(mock_server):
    """`read_grayscale()` result always has exactly three channel/value entries."""
    with HatClient(socket_path=mock_server) as hat:
        result = hat.read_grayscale()
    assert len(result.channels) == 3
    assert len(result.values) == 3


def test_read_grayscale_hardware_error(tmp_path):
    """`read_grayscale()` propagates `HatError` on hardware failure."""
    sock_path = str(tmp_path / "nomopractic.sock")
    ready = threading.Event()
    t = threading.Thread(
        target=_run_mock_server,
        args=(sock_path, _DEFAULT_RESPONSES),
        kwargs={"error_method": "read_grayscale", "ready_event": ready},
        daemon=True,
    )
    t.start()
    ready.wait(timeout=2.0)

    with HatClient(socket_path=sock_path) as hat:
        with pytest.raises(HatError):
            hat.read_grayscale()

    t.join(timeout=2.0)
