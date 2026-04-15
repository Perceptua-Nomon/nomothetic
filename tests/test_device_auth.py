"""Tests for device-mode authentication endpoints."""

import os
from typing import cast
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from nomothetic.api import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def device_auth_client():
    """Test client with device auth enabled (default)."""
    with patch.dict(
        os.environ,
        {"NOMON_DEVICE_AUTH": "true", "NOMON_API_MODE": "device"},
        clear=False,
    ):
        app = create_app()
        client = TestClient(app)
        yield client, app


@pytest.fixture
def device_no_auth_client():
    """Test client with device auth disabled."""
    with patch.dict(
        os.environ,
        {"NOMON_DEVICE_AUTH": "false", "NOMON_API_MODE": "device"},
        clear=False,
    ):
        app = create_app()
        client = TestClient(app)
        yield client, app


def _get_pairing_secret(app) -> str:
    """Extract the pairing secret from app state."""
    return cast(str, app.state.pairing_state.secret)


def _pair(client, secret: str, display_name: str = "Test Owner"):
    """Perform a pairing request and return the response."""
    return client.post(
        "/api/device/auth/pair",
        json={"secret": secret, "display_name": display_name},
    )


def _get_token(client, app) -> str:
    """Pair and return the access token."""
    secret = _get_pairing_secret(app)
    resp = _pair(client, secret)
    return cast(str, resp.json()["access_token"])


# ============================================================================
# Status endpoint
# ============================================================================


def test_status_unpaired(device_auth_client):
    """Status shows unpaired with secret available before pairing."""
    client, app = device_auth_client
    resp = client.get("/api/device/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["paired"] is False
    assert data["pairing_available"] is True


def test_status_after_pairing(device_auth_client):
    """Status shows paired after successful pairing."""
    client, app = device_auth_client
    secret = _get_pairing_secret(app)
    _pair(client, secret)
    resp = client.get("/api/device/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["paired"] is True
    assert data["pairing_available"] is False


# ============================================================================
# Pair endpoint
# ============================================================================


def test_pair_success(device_auth_client):
    """Correct secret pairs the device and returns tokens."""
    client, app = device_auth_client
    secret = _get_pairing_secret(app)
    resp = _pair(client, secret)
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0


def test_pair_wrong_secret(device_auth_client):
    """Wrong secret returns 401."""
    client, app = device_auth_client
    resp = _pair(client, "wrong-secret-value")
    assert resp.status_code == 401


def test_pair_already_paired(device_auth_client):
    """Pairing again after success returns 409."""
    client, app = device_auth_client
    secret = _get_pairing_secret(app)
    _pair(client, secret)
    resp = _pair(client, secret)
    assert resp.status_code == 409


def test_pair_rate_limited(device_auth_client):
    """Pairing endpoint is rate-limited to 3 requests per minute."""
    client, app = device_auth_client
    for _ in range(3):
        client.post(
            "/api/device/auth/pair",
            json={"secret": "wrong", "display_name": "Attacker"},
        )
    resp = client.post(
        "/api/device/auth/pair",
        json={"secret": "wrong", "display_name": "Attacker"},
    )
    assert resp.status_code == 429


# ============================================================================
# Refresh endpoint
# ============================================================================


def test_refresh_success(device_auth_client):
    """Valid refresh token returns new tokens."""
    client, app = device_auth_client
    secret = _get_pairing_secret(app)
    pair_resp = _pair(client, secret)
    refresh_token = pair_resp.json()["refresh_token"]

    resp = client.post(
        "/api/device/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_refresh_invalid_token(device_auth_client):
    """Invalid refresh token returns 401."""
    client, app = device_auth_client
    resp = client.post(
        "/api/device/auth/refresh",
        json={"refresh_token": "invalid-refresh-token"},
    )
    assert resp.status_code == 401


# ============================================================================
# Me endpoint
# ============================================================================


def test_me_returns_profile(device_auth_client):
    """Authenticated /me returns the paired owner profile."""
    client, app = device_auth_client
    token = _get_token(client, app)
    resp = client.get(
        "/api/device/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "device-owner@local"
    assert data["display_name"] == "Test Owner"
    assert "timestamp" in data


def test_me_requires_auth(device_auth_client):
    """/me without token returns 401."""
    client, app = device_auth_client
    resp = client.get("/api/device/auth/me")
    assert resp.status_code == 401


# ============================================================================
# Device endpoints require auth
# ============================================================================


def test_device_endpoint_requires_token(device_auth_client):
    """Device API endpoint returns 401 without token."""
    client, app = device_auth_client
    resp = client.get("/api/camera/status")
    assert resp.status_code == 401


def test_device_endpoint_succeeds_with_token(device_auth_client):
    """Device API endpoint succeeds with valid token (may 500 due to no camera, but not 401)."""
    client, app = device_auth_client
    token = _get_token(client, app)
    resp = client.get(
        "/api/camera/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    # 500 (no camera) or 503 (no daemon) is acceptable — NOT 401
    assert resp.status_code != 401


def test_health_no_auth_required(device_auth_client):
    """Health endpoint is accessible without token."""
    client, app = device_auth_client
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


# ============================================================================
# Auth disabled mode
# ============================================================================


def test_no_auth_mode_health(device_no_auth_client):
    """Health works in no-auth mode."""
    client, app = device_no_auth_client
    resp = client.get("/")
    assert resp.status_code == 200


def test_no_auth_mode_no_pairing_endpoints(device_no_auth_client):
    """Pairing endpoints are not registered in no-auth mode."""
    client, app = device_no_auth_client
    resp = client.get("/api/device/auth/status")
    assert resp.status_code == 404


def test_no_auth_mode_endpoints_unauthenticated(device_no_auth_client):
    """Device endpoints work without token in no-auth mode."""
    client, app = device_no_auth_client
    resp = client.get("/api/camera/status")
    # 500 (no camera) is expected — NOT 401
    assert resp.status_code != 401
    assert resp.status_code != 403


# ============================================================================
# Token isolation (issuer check)
# ============================================================================


def test_central_token_rejected_on_device(device_auth_client):
    """A token with central issuer is rejected by device-mode auth."""
    client, app = device_auth_client
    from nomothetic.auth import AuthService

    # Create a separate AuthService with central issuer
    central_svc = AuthService(
        secret=app.state.pairing_state.jwt_secret,
        issuer="nomon-central",
    )
    central_token = central_svc.create_access_token("alice@example.com")

    resp = client.get(
        "/api/camera/status",
        headers={"Authorization": f"Bearer {central_token}"},
    )
    assert resp.status_code == 401
