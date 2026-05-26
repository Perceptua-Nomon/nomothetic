"""Tests for the Wi-Fi AP mode toggle endpoint.

POST /api/device/wifi/ap
"""

import os
import subprocess
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from nomothetic.api import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def device_client():
    """Test client with device auth enabled and hardware mocked."""
    with patch.dict(
        os.environ,
        {"NOMON_DEVICE_AUTH": "true", "NOMON_API_MODE": "device"},
        clear=False,
    ):
        app = create_app()
        client = TestClient(app)
        yield client, app


def _get_pairing_secret(app) -> str:
    """Extract the pairing secret from app state."""
    return cast(str, app.state.pairing_state.secret)


def _get_token(client: TestClient, app) -> str:
    """Pair the device and return the access token."""
    secret = _get_pairing_secret(app)
    resp = client.post(
        "/api/device/auth/pair",
        json={"secret": secret, "display_name": "Test Owner"},
    )
    assert resp.status_code == 200, resp.text
    return cast(str, resp.json()["access_token"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_run(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Build a MagicMock that behaves like subprocess.run result."""
    mock_result = MagicMock()
    mock_result.returncode = returncode
    mock_result.stdout = stdout
    mock_result.stderr = stderr
    return mock_result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ap_enable_returns_status_up(device_client):
    """POST {"enabled": true} returns {"status": "up", "timestamp": "..."}."""
    client, app = device_client
    token = _get_token(client, app)

    mock_result = _make_mock_run(returncode=0, stdout="AP is up\n")
    captured_args: list[list[str]] = []

    def _capture_run(args, **kwargs):
        captured_args.append(list(args))
        return mock_result

    with patch("subprocess.run", side_effect=_capture_run):
        resp = client.post(
            "/api/device/wifi/ap",
            json={"enabled": True},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "up"
    assert "timestamp" in body
    assert len(captured_args) == 1
    assert captured_args[0][-1] == "up"


def test_ap_disable_returns_status_down(device_client):
    """POST {"enabled": false} returns {"status": "down", "timestamp": "..."}."""
    client, app = device_client
    token = _get_token(client, app)

    mock_result = _make_mock_run(returncode=0, stdout="AP is down\n")
    captured_args: list[list[str]] = []

    def _capture_run(args, **kwargs):
        captured_args.append(list(args))
        return mock_result

    with patch("subprocess.run", side_effect=_capture_run):
        resp = client.post(
            "/api/device/wifi/ap",
            json={"enabled": False},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "down"
    assert "timestamp" in body
    assert len(captured_args) == 1
    assert captured_args[0][-1] == "down"


def test_ap_toggle_script_not_found_returns_503(device_client):
    """FileNotFoundError from subprocess.run → 503."""
    client, app = device_client
    token = _get_token(client, app)

    with patch("subprocess.run", side_effect=FileNotFoundError):
        resp = client.post(
            "/api/device/wifi/ap",
            json={"enabled": True},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 503
    assert "ap-mode.sh not found" in resp.json()["error"]


def test_ap_toggle_script_nonzero_returns_500(device_client):
    """Non-zero returncode from ap-mode.sh → 500."""
    client, app = device_client
    token = _get_token(client, app)

    mock_result = _make_mock_run(returncode=1, stderr="nmcli error: device busy")

    with patch("subprocess.run", return_value=mock_result):
        resp = client.post(
            "/api/device/wifi/ap",
            json={"enabled": True},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 500
    assert "AP mode toggle failed" in resp.json()["error"]


def test_ap_toggle_requires_auth(device_client):
    """Request without Authorization header returns 401."""
    client, app = device_client

    resp = client.post(
        "/api/device/wifi/ap",
        json={"enabled": True},
    )
    assert resp.status_code == 401


def test_ap_toggle_invalid_body_returns_422(device_client):
    """Sending {"enabled": "yes"} (not a bool) returns 422."""
    client, app = device_client
    token = _get_token(client, app)

    resp = client.post(
        "/api/device/wifi/ap",
        json={"enabled": "maybe"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_ap_toggle_script_timeout_returns_500(device_client):
    client, app = device_client
    token = _get_token(client, app)

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=15)):
        resp = client.post(
            "/api/device/wifi/ap",
            json={"enabled": True},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 500
    assert "timed out" in resp.json()["error"].lower()
