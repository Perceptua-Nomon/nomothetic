"""IPC client for the nomopractic Rust HAT daemon.

Communicates over a Unix domain socket using NDJSON framing.
Full IPC schema: docs/hat_ipc_schema.md

Classes
-------
HatClient
    Thin client wrapping the Unix socket connection.
HatError
    Base exception for HAT IPC errors.
HatConnectionError
    Raised on socket connection failures.
HatTimeoutError
    Raised on per-request read timeout.
HatHealthResult
    Dataclass for the health IPC method result.
"""

from __future__ import annotations

import io
import json
import os
import socket
import threading
from dataclasses import dataclass
from pathlib import Path  # noqa: F401 – re-exported for callers
from typing import Optional

_DEFAULT_SOCKET_PATH = "/run/nomopractic/nomopractic.sock"


class HatError(Exception):
    """Base exception for all nomothetic.hat errors.

    Attributes
    ----------
    code : str
        Machine-readable error code (e.g. ``"HARDWARE_ERROR"``).
    message : str
        Human-readable description.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class HatConnectionError(HatError):
    """Raised when the socket connection cannot be established or is lost.

    ``code`` is always ``"CONNECTION_ERROR"``.
    """

    def __init__(self, message: str) -> None:
        super().__init__("CONNECTION_ERROR", message)


class HatTimeoutError(HatError):
    """Raised when a request does not receive a response within *timeout_s*.

    ``code`` is always ``"TIMEOUT"``.
    """

    def __init__(self, message: str) -> None:
        super().__init__("TIMEOUT", message)


@dataclass
class HatHealthResult:
    """Result payload from the ``health`` IPC method."""

    status: str
    version: str
    hat_address: str
    i2c_bus: int
    uptime_s: int


class HatClient:
    """Client for the nomopractic Unix domain socket IPC daemon.

    Parameters
    ----------
    socket_path : str | Path | None
        Path to the Unix domain socket. Defaults to
        ``/run/nomopractic/nomopractic.sock`` or the value of the
        ``NOMON_HAT_SOCKET_PATH`` environment variable.
    timeout_s : float
        Per-request read timeout in seconds. Default: 2.0.
    """

    def __init__(
        self,
        socket_path: Optional[str | Path] = None,
        timeout_s: float = 2.0,
    ) -> None:
        if socket_path is None:
            socket_path = os.environ.get("NOMON_HAT_SOCKET_PATH", _DEFAULT_SOCKET_PATH)
        self._socket_path = str(socket_path)
        self._timeout_s = timeout_s
        self._sock: Optional[socket.socket] = None
        self._rfile: Optional[io.BufferedReader] = None
        self._lock = threading.Lock()
        self._req_counter = 0

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open the socket connection to nomopractic.

        Raises
        ------
        HatConnectionError
            If the socket file does not exist or the connection is refused.
        """
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self._timeout_s)
            sock.connect(self._socket_path)
            self._sock = sock
            self._rfile = sock.makefile("rb")
        except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
            sock.close()
            raise HatConnectionError(
                f"Cannot connect to nomopractic at {self._socket_path}: {e}"
            ) from e

    def close(self) -> None:
        """Close the socket connection. Safe to call when already closed."""
        if self._rfile is not None:
            try:
                self._rfile.close()
            except OSError:
                pass
            self._rfile = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def __enter__(self) -> HatClient:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal request machinery
    # ------------------------------------------------------------------

    def _next_id(self) -> str:
        self._req_counter += 1
        return str(self._req_counter)

    def _ensure_connected(self) -> None:
        if self._sock is None:
            self.connect()

    def _send_request(self, method: str, params: dict, req_id: str) -> dict:
        """Send one NDJSON request and return the result dict.

        Caller must hold ``_lock`` and ensure the socket is connected.

        Raises
        ------
        HatTimeoutError
            On read timeout.
        HatConnectionError
            On broken pipe, connection reset, or empty response.
        HatError
            When the daemon returns ``ok=false``.
        """
        req = {"id": req_id, "method": method, "params": params}
        line = json.dumps(req) + "\n"
        try:
            if self._sock is None or self._rfile is None:
                raise HatConnectionError("Socket is not connected")
            self._sock.sendall(line.encode())
            resp_line = self._rfile.readline()
        except socket.timeout as e:
            raise HatTimeoutError(f"Request timed out after {self._timeout_s}s") from e
        except (BrokenPipeError, ConnectionResetError) as e:
            raise HatConnectionError(f"Connection lost: {e}") from e

        if not resp_line:
            raise HatConnectionError("Connection closed by daemon")

        try:
            resp = json.loads(resp_line)
        except json.JSONDecodeError as e:
            raise HatConnectionError(f"Malformed response from daemon: {e}") from e

        if not resp.get("ok"):
            error = resp.get("error", {})
            raise HatError(
                error.get("code", "UNKNOWN_ERROR"),
                error.get("message", "Unknown error"),
            )

        return resp.get("result", {})

    def _request(self, method: str, params: dict) -> dict:
        """Thread-safe request with one reconnect retry on connection loss."""
        with self._lock:
            self._ensure_connected()
            req_id = self._next_id()
            try:
                return self._send_request(method, params, req_id)
            except HatConnectionError:
                # Reconnect once and retry with a fresh request ID.
                self.close()
                self.connect()
                req_id = self._next_id()
                return self._send_request(method, params, req_id)

    # ------------------------------------------------------------------
    # Public IPC methods
    # ------------------------------------------------------------------

    def health(self) -> HatHealthResult:
        """Return daemon liveness and hardware status.

        Raises
        ------
        HatError
            If the daemon returns ok=false.
        HatConnectionError
            If the socket connection cannot be established or is lost.
        """
        result = self._request("health", {})
        return HatHealthResult(
            status=result["status"],
            version=result["version"],
            hat_address=result["hat_address"],
            i2c_bus=result["i2c_bus"],
            uptime_s=result["uptime_s"],
        )

    def get_battery_voltage(self) -> float:
        """Read battery voltage in volts via Robot HAT V4 ADC channel A4.

        Returns
        -------
        float
            Battery voltage in volts.

        Raises
        ------
        HatError
            On hardware read failure.
        HatConnectionError
            If the socket connection is lost.
        """
        result = self._request("get_battery_voltage", {})
        return float(result["voltage_v"])

    def set_servo_pulse_us(
        self,
        channel: int,
        pulse_us: int,
        ttl_ms: int = 500,
    ) -> None:
        """Set a PWM channel to a specific pulse width in microseconds.

        Parameters
        ----------
        channel : int
            PWM channel number (0–11).
        pulse_us : int
            Pulse width in microseconds (500–2500).
        ttl_ms : int
            Lease TTL in milliseconds. Default: 500.

        Raises
        ------
        ValueError
            If channel or pulse_us is out of range.
        HatError
            On hardware write failure.
        """
        if not 0 <= channel <= 11:
            raise ValueError(f"channel must be 0–11, got {channel}")
        if not 500 <= pulse_us <= 2500:
            raise ValueError(f"pulse_us must be 500–2500, got {pulse_us}")
        self._request(
            "set_servo_pulse_us",
            {"channel": channel, "pulse_us": pulse_us, "ttl_ms": ttl_ms},
        )

    def set_servo_angle(
        self,
        channel: int,
        angle_deg: float,
        ttl_ms: int = 500,
    ) -> None:
        """Set a servo to an angle in degrees (0.0–180.0).

        Parameters
        ----------
        channel : int
            PWM channel number (0–11).
        angle_deg : float
            Target angle in degrees.
        ttl_ms : int
            Lease TTL in milliseconds. Default: 500.

        Raises
        ------
        ValueError
            If channel or angle_deg is out of range.
        HatError
            On hardware write failure.
        """
        if not 0 <= channel <= 11:
            raise ValueError(f"channel must be 0–11, got {channel}")
        if not 0.0 <= angle_deg <= 180.0:
            raise ValueError(f"angle_deg must be 0.0–180.0, got {angle_deg}")
        self._request(
            "set_servo_angle",
            {"channel": channel, "angle_deg": angle_deg, "ttl_ms": ttl_ms},
        )

    def reset_mcu(self) -> None:
        """Assert and release the Robot HAT V4 MCU reset line.

        Raises
        ------
        HatError
            On GPIO failure.
        HatConnectionError
            If the socket connection is lost.
        """
        self._request("reset_mcu", {})
