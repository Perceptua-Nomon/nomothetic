"""Tests for auth and fleet endpoints (central-mode API).

These tests create the FastAPI app in central mode and exercise the
full register → login → manage devices → refresh flow.
"""

import base64
import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

_TEST_SECRET = "test-secret-key-that-is-at-least-32-bytes-long!"


def _make_registration_proof(vin: str) -> str:
    """Generate a structurally valid registration proof for use in tests.

    The central API validates proof structure and expiry but NOT the
    signature (it cannot — separate JWT secrets per service). This helper
    produces a three-part JWT-like token that passes those checks.
    """
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        .rstrip(b"=")
        .decode()
    )
    payload_data = {
        "iss": "nomon-device",
        "sub": vin,
        "aud": "nomon-fleet",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "jti": "test-jti-fixed",
    }
    payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).rstrip(b"=").decode()
    # Signature is not verified by the central API
    return f"{header}.{payload}.test_sig_not_verified"


@pytest.fixture
def central_client():
    """Create a test client with the API in central mode."""
    with patch.dict(
        os.environ,
        {
            "NOMON_API_MODE": "central",
            "NOMON_JWT_SECRET": _TEST_SECRET,
            # Ensure tests use in-memory stores even when developer shell has
            # ARCADEDB_* variables exported.
            "ARCADEDB_HOST": "",
        },
    ):
        from nomothetic.api import create_app

        app = create_app()
        yield TestClient(app)


# ============================================================================
# Health
# ============================================================================


def test_central_health(central_client):
    """Health endpoint works in central mode and reports mode."""
    response = central_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["mode"] == "central"


def test_device_endpoints_not_registered(central_client):
    """Device-mode endpoints are not available in central mode."""
    response = central_client.get("/api/camera/status")
    assert response.status_code in (404, 405)


# ============================================================================
# Registration
# ============================================================================


def test_register_success(central_client):
    """User registration returns tokens and user profile."""
    response = central_client.post(
        "/api/auth/register",
        json={
            "email": "alice@example.com",
            "password": "password123",
            "display_name": "Alice",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["email"] == "alice@example.com"
    assert data["user"]["display_name"] == "Alice"


def test_register_duplicate_email(central_client):
    """Duplicate email registration returns 409."""
    payload = {
        "email": "dup@example.com",
        "password": "password123",
        "display_name": "Dup",
    }
    central_client.post("/api/auth/register", json=payload)
    response = central_client.post("/api/auth/register", json=payload)
    assert response.status_code == 409


def test_register_invalid_email(central_client):
    """Invalid email returns 422."""
    response = central_client.post(
        "/api/auth/register",
        json={
            "email": "not-an-email",
            "password": "password123",
            "display_name": "Test",
        },
    )
    assert response.status_code == 422


def test_register_short_password(central_client):
    """Password shorter than 8 chars returns 422."""
    response = central_client.post(
        "/api/auth/register",
        json={
            "email": "short@example.com",
            "password": "abc",
            "display_name": "Short",
        },
    )
    assert response.status_code == 422


# ============================================================================
# Login
# ============================================================================


def test_login_success(central_client):
    """Successful login returns tokens."""
    central_client.post(
        "/api/auth/register",
        json={
            "email": "login@example.com",
            "password": "password123",
            "display_name": "Login",
        },
    )
    response = central_client.post(
        "/api/auth/login",
        json={
            "email": "login@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 900


def test_login_wrong_password(central_client):
    """Wrong password returns 401."""
    central_client.post(
        "/api/auth/register",
        json={
            "email": "wrong@example.com",
            "password": "password123",
            "display_name": "Wrong",
        },
    )
    response = central_client.post(
        "/api/auth/login",
        json={
            "email": "wrong@example.com",
            "password": "incorrect",
        },
    )
    assert response.status_code == 401


def test_login_unknown_email(central_client):
    """Unknown email returns 401."""
    response = central_client.post(
        "/api/auth/login",
        json={
            "email": "nobody@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 401


# ============================================================================
# Token refresh
# ============================================================================


def test_refresh_success(central_client):
    """Refresh token returns new token pair."""
    reg = central_client.post(
        "/api/auth/register",
        json={
            "email": "refresh@example.com",
            "password": "password123",
            "display_name": "Refresh",
        },
    )
    refresh_token = reg.json()["refresh_token"]
    response = central_client.post(
        "/api/auth/refresh",
        json={
            "refresh_token": refresh_token,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_refresh_invalid_token(central_client):
    """Invalid refresh token returns 401."""
    response = central_client.post(
        "/api/auth/refresh",
        json={
            "refresh_token": "bogus-token",
        },
    )
    assert response.status_code == 401


def test_refresh_rotation_invalidates_old(central_client):
    """After refresh, the old token is invalid."""
    reg = central_client.post(
        "/api/auth/register",
        json={
            "email": "rotate@example.com",
            "password": "password123",
            "display_name": "Rotate",
        },
    )
    old_refresh = reg.json()["refresh_token"]
    central_client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    response = central_client.post(
        "/api/auth/refresh",
        json={
            "refresh_token": old_refresh,
        },
    )
    assert response.status_code == 401


# ============================================================================
# Profile (GET /api/auth/me)
# ============================================================================


def test_me_authenticated(central_client):
    """Authenticated user can retrieve their profile."""
    reg = central_client.post(
        "/api/auth/register",
        json={
            "email": "me@example.com",
            "password": "password123",
            "display_name": "Me",
        },
    )
    token = reg.json()["access_token"]
    response = central_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "me@example.com"
    assert data["display_name"] == "Me"


def test_me_unauthenticated(central_client):
    """Unauthenticated request to /me returns 401."""
    response = central_client.get("/api/auth/me")
    assert response.status_code == 401


def test_me_invalid_token(central_client):
    """Invalid bearer token returns 401."""
    response = central_client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert response.status_code == 401


# ============================================================================
# Profile edit (PATCH /api/auth/me) and password change
# ============================================================================


def _register_profile_user(client, email="profile@example.com", password="password123"):
    """Register a user and return (access_token, refresh_token)."""
    reg = client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "display_name": "Before"},
    )
    body = reg.json()
    return body["access_token"], body["refresh_token"]


def test_update_display_name(central_client):
    """PATCH /api/auth/me updates the display name."""
    token, _ = _register_profile_user(central_client)
    response = central_client.patch(
        "/api/auth/me",
        json={"display_name": "After"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "After"
    # Persisted: a fresh GET reflects the new name.
    me = central_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["display_name"] == "After"


def test_update_display_name_requires_auth(central_client):
    """PATCH /api/auth/me without a token returns 401."""
    response = central_client.patch("/api/auth/me", json={"display_name": "Nope"})
    assert response.status_code == 401


def test_change_password_success(central_client):
    """Changing the password works and the new password authenticates."""
    token, _ = _register_profile_user(central_client, email="pw@example.com")
    response = central_client.post(
        "/api/auth/change-password",
        json={"current_password": "password123", "new_password": "newpassword456"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    # New password logs in; old one does not.
    ok = central_client.post(
        "/api/auth/login",
        json={"email": "pw@example.com", "password": "newpassword456"},
    )
    assert ok.status_code == 200
    bad = central_client.post(
        "/api/auth/login",
        json={"email": "pw@example.com", "password": "password123"},
    )
    assert bad.status_code == 401


def test_change_password_wrong_current(central_client):
    """A wrong current password returns 401."""
    token, _ = _register_profile_user(central_client, email="pw2@example.com")
    response = central_client.post(
        "/api/auth/change-password",
        json={"current_password": "wrongpassword", "new_password": "newpassword456"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_change_password_too_short(central_client):
    """A new password shorter than 8 chars returns 422."""
    token, _ = _register_profile_user(central_client, email="pw3@example.com")
    response = central_client.post(
        "/api/auth/change-password",
        json={"current_password": "password123", "new_password": "short"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


def test_change_password_revokes_refresh_tokens(central_client):
    """After a password change, prior refresh tokens are revoked."""
    token, refresh = _register_profile_user(central_client, email="pw4@example.com")
    central_client.post(
        "/api/auth/change-password",
        json={"current_password": "password123", "new_password": "newpassword456"},
        headers={"Authorization": f"Bearer {token}"},
    )
    response = central_client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert response.status_code == 401


# ============================================================================
# Fleet — Device registration
# ============================================================================


def _register_and_auth(client):
    """Helper: register a user and return the access token."""
    reg = client.post(
        "/api/auth/register",
        json={
            "email": "fleet@example.com",
            "password": "password123",
            "display_name": "Fleet",
        },
    )
    return reg.json()["access_token"]


def test_register_device(central_client):
    """Register a device under the authenticated user."""
    token = _register_and_auth(central_client)
    response = central_client.post(
        "/api/fleet/devices",
        json={
            "vin": "NOMON001",
            "model": "explorer-v1",
            "registration_proof": _make_registration_proof("NOMON001"),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["vin"] == "NOMON001"
    assert data["model"] == "explorer-v1"


def test_register_duplicate_device(central_client):
    """Duplicate device registration returns 409."""
    token = _register_and_auth(central_client)
    payload = {
        "vin": "DUP001",
        "model": "explorer-v1",
        "registration_proof": _make_registration_proof("DUP001"),
    }
    central_client.post(
        "/api/fleet/devices",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    response = central_client.post(
        "/api/fleet/devices",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409


def test_list_devices(central_client):
    """List devices for the authenticated user."""
    token = _register_and_auth(central_client)
    central_client.post(
        "/api/fleet/devices",
        json={
            "vin": "LIST001",
            "model": "explorer-v1",
            "registration_proof": _make_registration_proof("LIST001"),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    response = central_client.get(
        "/api/fleet/devices",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["devices"]) == 1
    assert data["devices"][0]["vin"] == "LIST001"


def test_get_device_detail(central_client):
    """Retrieve detail for a single device."""
    token = _register_and_auth(central_client)
    central_client.post(
        "/api/fleet/devices",
        json={
            "vin": "DET001",
            "model": "explorer-v1",
            "registration_proof": _make_registration_proof("DET001"),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    response = central_client.get(
        "/api/fleet/devices/DET001",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["vin"] == "DET001"
    assert data["role"] == "owner"


def test_get_device_not_found(central_client):
    """Nonexistent device returns 404."""
    token = _register_and_auth(central_client)
    response = central_client.get(
        "/api/fleet/devices/NOTREAL",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


# ============================================================================
# Fleet — Telemetry history
# ============================================================================


def _seed_reading(vin: str, recorded_at: str, battery: float = 8.0) -> None:
    """Record one telemetry reading into the active in-memory store.

    Uses a private event loop (not :func:`asyncio.run`, which would clear the
    process-global current loop and disturb pytest-asyncio's loop management
    for subsequent async tests).
    """
    import asyncio

    from nomothetic.fleet_routes import get_telemetry_store
    from nomothetic.telemetry_store import TelemetryReadingItem

    store = get_telemetry_store()
    assert store is not None
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            store.record_reading(
                vin,
                TelemetryReadingItem(
                    battery_voltage=battery,
                    cpu_temp_c=45.0,
                    uptime_seconds=100,
                    recorded_at=recorded_at,
                ),
            )
        )
    finally:
        loop.close()


def _register_device(client, token: str, vin: str) -> None:
    client.post(
        "/api/fleet/devices",
        json={
            "vin": vin,
            "model": "explorer-v1",
            "registration_proof": _make_registration_proof(vin),
        },
        headers={"Authorization": f"Bearer {token}"},
    )


def test_device_telemetry_history(central_client):
    """Telemetry history returns recorded readings newest-first."""
    token = _register_and_auth(central_client)
    _register_device(central_client, token, "TEL001")
    _seed_reading("TEL001", "2026-01-01T00:00:00+00:00", battery=8.1)
    _seed_reading("TEL001", "2026-01-01T00:01:00+00:00", battery=8.0)

    response = central_client.get(
        "/api/fleet/devices/TEL001/telemetry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["vin"] == "TEL001"
    assert len(data["readings"]) == 2
    assert data["readings"][0]["recorded_at"] == "2026-01-01T00:01:00+00:00"


def test_device_telemetry_history_empty(central_client):
    """A registered device with no readings returns an empty list."""
    token = _register_and_auth(central_client)
    _register_device(central_client, token, "TEL002")
    response = central_client.get(
        "/api/fleet/devices/TEL002/telemetry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["readings"] == []


def test_device_telemetry_history_not_owned(central_client):
    """Telemetry for an unregistered/unowned device returns 404."""
    token = _register_and_auth(central_client)
    response = central_client.get(
        "/api/fleet/devices/NOTMINE/telemetry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_device_detail_includes_latest_telemetry(central_client):
    """Device detail surfaces the latest telemetry reading when present."""
    token = _register_and_auth(central_client)
    _register_device(central_client, token, "TEL003")
    _seed_reading("TEL003", "2026-01-01T00:00:00+00:00", battery=7.7)

    response = central_client.get(
        "/api/fleet/devices/TEL003",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    latest = response.json()["latest_telemetry"]
    assert latest is not None
    assert latest["battery_voltage"] == 7.7


def test_remove_device(central_client):
    """Remove a device from the user's fleet."""
    token = _register_and_auth(central_client)
    central_client.post(
        "/api/fleet/devices",
        json={
            "vin": "REM001",
            "model": "explorer-v1",
            "registration_proof": _make_registration_proof("REM001"),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    response = central_client.delete(
        "/api/fleet/devices/REM001",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["removed"] is True

    # Verify it's gone
    get_resp = central_client.get(
        "/api/fleet/devices/REM001",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_resp.status_code == 404


def test_remove_device_not_found(central_client):
    """Removing a nonexistent device returns 404."""
    token = _register_and_auth(central_client)
    response = central_client.delete(
        "/api/fleet/devices/GONE001",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_fleet_unauthenticated(central_client):
    """Fleet endpoints require authentication."""
    response = central_client.get("/api/fleet/devices")
    assert response.status_code in (401, 403)


def test_register_device_invalid_proof(central_client):
    """Registration with an invalid or missing proof returns 400."""
    token = _register_and_auth(central_client)
    response = central_client.post(
        "/api/fleet/devices",
        json={"vin": "BAD001", "model": "nomon", "registration_proof": "not.a.valid.proof"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


def test_register_device_expired_proof(central_client):
    """Registration with an expired proof returns 400."""
    import base64 as _b64
    import json as _json

    header = _b64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    payload_data = {
        "iss": "nomon-device",
        "sub": "EXP001",
        "aud": "nomon-fleet",
        "exp": 1000000,
        "iat": 999000,
    }  # exp far in the past
    payload = _b64.urlsafe_b64encode(_json.dumps(payload_data).encode()).rstrip(b"=").decode()
    expired_proof = f"{header}.{payload}.fake_sig"

    token = _register_and_auth(central_client)
    response = central_client.post(
        "/api/fleet/devices",
        json={"vin": "EXP001", "model": "nomon", "registration_proof": expired_proof},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


# ============================================================================
# Rate limiting (integration)
# ============================================================================


def test_register_rate_limit_429(central_client):
    """Registration endpoint returns 429 after exceeding rate limit."""
    for i in range(10):
        central_client.post(
            "/api/auth/register",
            json={
                "email": f"ratelimit{i}@example.com",
                "password": "password123",
                "display_name": f"Rate{i}",
            },
        )
    # 11th request should be rate-limited
    response = central_client.post(
        "/api/auth/register",
        json={
            "email": "ratelimit_extra@example.com",
            "password": "password123",
            "display_name": "Blocked",
        },
    )
    assert response.status_code == 429


# ============================================================================
# Logout
# ============================================================================


def test_logout_success(central_client):
    """Logout revokes the refresh token; subsequent refresh fails."""
    reg = central_client.post(
        "/api/auth/register",
        json={
            "email": "logout@example.com",
            "password": "password123",
            "display_name": "Logout",
        },
    )
    data = reg.json()
    access = data["access_token"]
    refresh = data["refresh_token"]

    # Logout
    resp = central_client.post(
        "/api/auth/logout",
        json={"refresh_token": refresh},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # Refresh with the revoked token should fail
    refresh_resp = central_client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh},
    )
    assert refresh_resp.status_code == 401


def test_logout_requires_auth(central_client):
    """POST /api/auth/logout without JWT returns 401."""
    resp = central_client.post(
        "/api/auth/logout",
        json={"refresh_token": "some-token"},
    )
    assert resp.status_code == 401


def test_logout_invalid_token(central_client):
    """POST /api/auth/logout with a fake refresh token still returns 200."""
    reg = central_client.post(
        "/api/auth/register",
        json={
            "email": "logout2@example.com",
            "password": "password123",
            "display_name": "Logout2",
        },
    )
    access = reg.json()["access_token"]

    resp = central_client.post(
        "/api/auth/logout",
        json={"refresh_token": "completely-fake-token"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
