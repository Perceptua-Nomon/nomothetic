"""Tests for plugin authentication (Ed25519 challenge-response).

Covers the PluginKeyStore / ChallengeStore units and the
register/challenge/token route flow. No Pi hardware required.
"""

import base64
import os
import time
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from nomothetic.api import create_app
from nomothetic.plugin_auth import (
    ChallengeStore,
    InvalidPluginName,
    KeyConflict,
    PluginAuthError,
    PluginKeyStore,
    verify_signature,
)

# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def _keypair() -> tuple[Ed25519PrivateKey, str]:
    """Return (private_key, public_key_pem)."""
    priv = Ed25519PrivateKey.generate()
    pem = (
        priv.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return priv, pem


def _sign(priv: Ed25519PrivateKey, nonce: str) -> str:
    return base64.b64encode(priv.sign(nonce.encode("utf-8"))).decode()


# ---------------------------------------------------------------------------
# PluginKeyStore
# ---------------------------------------------------------------------------


def test_register_new_key(tmp_path):
    store = PluginKeyStore(str(tmp_path))
    _, pem = _keypair()
    assert store.register("autonomon", pem) == "registered"
    assert store.get_public_key("autonomon") is not None


def test_register_same_key_is_idempotent(tmp_path):
    store = PluginKeyStore(str(tmp_path))
    _, pem = _keypair()
    assert store.register("autonomon", pem) == "registered"
    assert store.register("autonomon", pem) == "exists"


def test_register_different_key_conflicts(tmp_path):
    store = PluginKeyStore(str(tmp_path))
    _, pem1 = _keypair()
    _, pem2 = _keypair()
    store.register("autonomon", pem1)
    with pytest.raises(KeyConflict):
        store.register("autonomon", pem2)


def test_register_invalid_pem_raises(tmp_path):
    store = PluginKeyStore(str(tmp_path))
    with pytest.raises(PluginAuthError):
        store.register("autonomon", "not a pem")


def test_register_rsa_key_rejected(tmp_path):
    # A valid PEM public key that is not Ed25519 must be rejected.
    from cryptography.hazmat.primitives.asymmetric import rsa

    rsa_pub = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    store = PluginKeyStore(str(tmp_path))
    with pytest.raises(PluginAuthError):
        store.register("autonomon", rsa_pub)


@pytest.mark.parametrize("bad", ["../escape", "Has Space", "UPPER", "with/slash", ""])
def test_invalid_plugin_names_rejected(tmp_path, bad):
    store = PluginKeyStore(str(tmp_path))
    _, pem = _keypair()
    with pytest.raises(InvalidPluginName):
        store.register(bad, pem)


def test_get_unregistered_returns_none(tmp_path):
    store = PluginKeyStore(str(tmp_path))
    assert store.get_public_key("autonomon") is None


# ---------------------------------------------------------------------------
# ChallengeStore
# ---------------------------------------------------------------------------


def test_challenge_issue_and_consume():
    cs = ChallengeStore(ttl_seconds=10)
    nonce, ttl = cs.issue("autonomon")
    assert ttl == 10
    assert cs.consume("autonomon", nonce) is True


def test_challenge_is_single_use():
    cs = ChallengeStore(ttl_seconds=10)
    nonce, _ = cs.issue("autonomon")
    assert cs.consume("autonomon", nonce) is True
    assert cs.consume("autonomon", nonce) is False


def test_challenge_wrong_nonce_rejected():
    cs = ChallengeStore(ttl_seconds=10)
    cs.issue("autonomon")
    assert cs.consume("autonomon", "wrong") is False


def test_challenge_expired_rejected():
    cs = ChallengeStore(ttl_seconds=0.0)
    nonce, _ = cs.issue("autonomon")
    time.sleep(0.01)
    assert cs.consume("autonomon", nonce) is False


def test_challenge_unknown_plugin_rejected():
    cs = ChallengeStore()
    assert cs.consume("nobody", "x") is False


def test_challenge_multiple_outstanding_per_plugin():
    # Concurrent acquisition: two issued nonces for the same plugin must NOT
    # evict each other — both remain independently consumable.
    cs = ChallengeStore(ttl_seconds=10)
    n1, _ = cs.issue("autonomon")
    n2, _ = cs.issue("autonomon")
    assert n1 != n2
    assert cs.consume("autonomon", n1) is True
    assert cs.consume("autonomon", n2) is True


def test_challenge_nonce_bound_to_plugin():
    # A nonce issued for one plugin cannot be consumed under another plugin name.
    cs = ChallengeStore(ttl_seconds=10)
    nonce, _ = cs.issue("autonomon")
    assert cs.consume("other", nonce) is False


def test_challenge_caps_outstanding_nonces():
    cs = ChallengeStore(ttl_seconds=100, max_outstanding=3)
    nonces = [cs.issue("autonomon")[0] for _ in range(5)]
    # Never exceeds the cap; the oldest were evicted.
    assert len(cs._pending) == 3
    # The last 3 issued are still valid; the first 2 were evicted.
    assert cs.consume("autonomon", nonces[-1]) is True
    assert cs.consume("autonomon", nonces[0]) is False


# ---------------------------------------------------------------------------
# verify_signature
# ---------------------------------------------------------------------------


def test_verify_signature_valid():
    priv, _ = _keypair()
    msg = b"some-nonce"
    sig = priv.sign(msg)
    assert verify_signature(priv.public_key(), msg, sig) is True


def test_verify_signature_invalid():
    priv, _ = _keypair()
    other, _ = _keypair()
    msg = b"some-nonce"
    sig = other.sign(msg)
    assert verify_signature(priv.public_key(), msg, sig) is False


# ---------------------------------------------------------------------------
# Route flow
# ---------------------------------------------------------------------------


@pytest.fixture
def plugin_client(tmp_path):
    """Device-auth app with an isolated key store and a loopback client."""
    with patch.dict(
        os.environ,
        {"NOMON_DEVICE_AUTH": "true", "NOMON_API_MODE": "device"},
        clear=False,
    ):
        app = create_app()
        # Replace the default (/var/lib/...) store with a tmp one for the test.
        app.state.plugin_key_store = PluginKeyStore(str(tmp_path))
        app.state.plugin_challenge_store = ChallengeStore()
        client = TestClient(app, client=("127.0.0.1", 50000))
        yield client, app


@pytest.fixture
def remote_client(tmp_path):
    """Same app, but requests appear to come from a non-loopback address."""
    with patch.dict(
        os.environ,
        {"NOMON_DEVICE_AUTH": "true", "NOMON_API_MODE": "device"},
        clear=False,
    ):
        app = create_app()
        app.state.plugin_key_store = PluginKeyStore(str(tmp_path))
        app.state.plugin_challenge_store = ChallengeStore()
        client = TestClient(app, client=("10.0.0.5", 40000))
        yield client, app


def _register(client, plugin, pem):
    return client.post("/api/plugin/register", json={"plugin": plugin, "public_key": pem})


def test_register_from_localhost_ok(plugin_client):
    client, _ = plugin_client
    _, pem = _keypair()
    resp = _register(client, "autonomon", pem)
    assert resp.status_code == 200
    assert resp.json()["status"] == "registered"


def test_register_from_remote_forbidden(remote_client):
    client, _ = remote_client
    _, pem = _keypair()
    resp = _register(client, "autonomon", pem)
    assert resp.status_code == 403


def test_register_conflict_returns_409(plugin_client):
    client, _ = plugin_client
    _, pem1 = _keypair()
    _, pem2 = _keypair()
    _register(client, "autonomon", pem1)
    resp = _register(client, "autonomon", pem2)
    assert resp.status_code == 409


def test_challenge_unregistered_404(plugin_client):
    client, _ = plugin_client
    resp = client.get("/api/plugin/challenge", params={"plugin": "autonomon"})
    assert resp.status_code == 404


def test_full_token_flow_grants_device_access(plugin_client):
    client, _ = plugin_client
    priv, pem = _keypair()
    _register(client, "autonomon", pem)

    ch = client.get("/api/plugin/challenge", params={"plugin": "autonomon"})
    assert ch.status_code == 200
    nonce = ch.json()["nonce"]

    tok = client.post(
        "/api/plugin/token",
        json={"plugin": "autonomon", "nonce": nonce, "signature": _sign(priv, nonce)},
    )
    assert tok.status_code == 200
    body = tok.json()
    access_token = body["access_token"]
    assert access_token
    assert body["timestamp"]  # all REST responses carry a UTC timestamp

    # The plugin token must authenticate against jwt_required-protected routes.
    # /me passes auth (sub="plugin:autonomon") then 404s on user lookup — a 404
    # (not 401) proves the token authenticated successfully.
    me = client.get("/api/device/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me.status_code == 404


def test_token_bad_signature_rejected(plugin_client):
    client, _ = plugin_client
    priv, pem = _keypair()
    other, _ = _keypair()
    _register(client, "autonomon", pem)

    nonce = client.get("/api/plugin/challenge", params={"plugin": "autonomon"}).json()["nonce"]
    tok = client.post(
        "/api/plugin/token",
        json={"plugin": "autonomon", "nonce": nonce, "signature": _sign(other, nonce)},
    )
    assert tok.status_code == 401


def test_token_replayed_nonce_rejected(plugin_client):
    client, _ = plugin_client
    priv, pem = _keypair()
    _register(client, "autonomon", pem)

    nonce = client.get("/api/plugin/challenge", params={"plugin": "autonomon"}).json()["nonce"]
    sig = _sign(priv, nonce)
    first = client.post(
        "/api/plugin/token",
        json={"plugin": "autonomon", "nonce": nonce, "signature": sig},
    )
    assert first.status_code == 200
    replay = client.post(
        "/api/plugin/token",
        json={"plugin": "autonomon", "nonce": nonce, "signature": sig},
    )
    assert replay.status_code == 401


def test_token_unregistered_plugin_rejected(plugin_client):
    client, _ = plugin_client
    priv, _ = _keypair()
    # No registration. Challenge would 404, but a forged nonce must also fail.
    tok = client.post(
        "/api/plugin/token",
        json={"plugin": "ghost", "nonce": "forged", "signature": _sign(priv, "forged")},
    )
    assert tok.status_code == 401
