"""Tests for the Wi-Fi credential provisioning endpoint.

POST /api/device/network/configure
"""

import os
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

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
# Tests
# ---------------------------------------------------------------------------


def _make_mock_proc(returncode: int = 0, stderr: str = "") -> AsyncMock:
    """Build an AsyncMock that behaves like an asyncio subprocess."""
    mock_proc = AsyncMock()
    mock_proc.returncode = returncode
    mock_proc.communicate = AsyncMock(return_value=(b"", stderr.encode()))
    mock_proc.kill = MagicMock()
    return mock_proc


def test_configure_wifi_success(device_client):
    """Valid SSID + WPA2 password returns 200 with status=connecting."""
    client, app = device_client
    token = _get_token(client, app)

    mock_proc = _make_mock_proc(returncode=0)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        resp = client.post(
            "/api/device/network/configure",
            json={"ssid": "HomeNetwork", "password": "securepass123"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "connecting"}


def test_configure_wifi_password_not_in_cmd_args(device_client):
    """Password must never appear in the subprocess argument list (stdin only)."""
    client, app = device_client
    token = _get_token(client, app)

    mock_proc = _make_mock_proc(returncode=0)
    captured_args: list[str] = []

    async def _capture_exec(*args: object, **kwargs: object) -> Any:
        captured_args.extend([str(arg) for arg in args])
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=_capture_exec):
        resp = client.post(
            "/api/device/network/configure",
            json={"ssid": "HomeNetwork", "password": "securepass123"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert (
        "securepass123" not in captured_args
    ), "Password must not appear in subprocess args; it should be sent via stdin"
    assert "--ask" in captured_args, "nmcli must be invoked with --ask for stdin prompting"


def test_configure_wifi_open_network(device_client):
    """Valid SSID with empty password (open network) returns 200."""
    client, app = device_client
    token = _get_token(client, app)

    mock_proc = _make_mock_proc(returncode=0)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        resp = client.post(
            "/api/device/network/configure",
            json={"ssid": "OpenCafe", "password": ""},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "connecting"}


def test_configure_wifi_unauthenticated(device_client):
    """Request without Authorization header returns 401."""
    client, app = device_client

    resp = client.post(
        "/api/device/network/configure",
        json={"ssid": "HomeNetwork", "password": "securepass123"},
    )
    assert resp.status_code == 401


def test_configure_wifi_ssid_too_long(device_client):
    """SSID longer than 32 characters returns 422."""
    client, app = device_client
    token = _get_token(client, app)

    resp = client.post(
        "/api/device/network/configure",
        json={"ssid": "A" * 33, "password": "securepass123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_configure_wifi_ssid_empty(device_client):
    """Empty SSID returns 422."""
    client, app = device_client
    token = _get_token(client, app)

    resp = client.post(
        "/api/device/network/configure",
        json={"ssid": "", "password": "securepass123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_configure_wifi_password_too_short(device_client):
    """Non-empty password shorter than 8 chars returns 422."""
    client, app = device_client
    token = _get_token(client, app)

    resp = client.post(
        "/api/device/network/configure",
        json={"ssid": "HomeNetwork", "password": "short"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_configure_wifi_subprocess_failure(device_client):
    """Subprocess failure (rc=1) is logged but endpoint still returns 200."""
    client, app = device_client
    token = _get_token(client, app)

    mock_proc = _make_mock_proc(returncode=1, stderr="Error: No network with SSID 'BadSSID' found.")

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        resp = client.post(
            "/api/device/network/configure",
            json={"ssid": "BadSSID", "password": "securepass123"},
            headers={"Authorization": f"Bearer {token}"},
        )

    # The endpoint fires-and-forgets; the failure is logged but not surfaced.
    assert resp.status_code == 200
    assert resp.json() == {"status": "connecting"}


def test_configure_wifi_rate_limited(device_client):
    """6th request within the window returns 429."""
    client, app = device_client
    token = _get_token(client, app)

    mock_proc = _make_mock_proc(returncode=0)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        for i in range(5):
            resp = client.post(
                "/api/device/network/configure",
                json={"ssid": "HomeNetwork", "password": "securepass123"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, f"Request {i + 1} should succeed"

        # 6th request should be rate-limited
        resp = client.post(
            "/api/device/network/configure",
            json={"ssid": "HomeNetwork", "password": "securepass123"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 429
