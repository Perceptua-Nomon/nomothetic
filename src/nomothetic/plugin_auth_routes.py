"""Plugin authentication endpoints: register, challenge, token.

Bootstrap auth for on-device autonomy plugins (see ADR-019 and
:mod:`nomothetic.plugin_auth`). These endpoints are intentionally **not** behind
``jwt_required`` — they are how a plugin obtains its first token — so each is
guarded by its own constraint instead:

* ``POST /api/plugin/register`` — localhost only (on-device deploy step).
* ``GET  /api/plugin/challenge`` — issues a single-use nonce for a registered
  plugin only.
* ``POST /api/plugin/token`` — issues a device JWT only on a valid signature over
  an unexpired nonce.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, timezone
from ipaddress import ip_address

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from nomothetic.auth import _PLUGIN_TOKEN_TTL, get_auth_service
from nomothetic.plugin_auth import (
    ChallengeStore,
    InvalidPluginName,
    KeyConflict,
    PluginAuthError,
    PluginKeyStore,
    verify_signature,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PluginRegisterRequest(BaseModel):
    """Register a plugin's Ed25519 public key (localhost only)."""

    plugin: str = Field(..., min_length=1, max_length=64, description="Plugin name")
    public_key: str = Field(..., min_length=1, description="PEM-encoded Ed25519 public key")


class PluginRegisterResponse(BaseModel):
    """Outcome of a registration attempt."""

    plugin: str
    status: str
    """``"registered"`` (newly stored) or ``"exists"`` (same key already present)."""
    timestamp: str


class PluginChallengeResponse(BaseModel):
    """A single-use challenge nonce for a registered plugin."""

    plugin: str
    nonce: str
    expires_in: float
    timestamp: str


class PluginTokenRequest(BaseModel):
    """Exchange a signed nonce for a device JWT."""

    plugin: str = Field(..., min_length=1, max_length=64)
    nonce: str = Field(..., min_length=1)
    signature: str = Field(..., min_length=1, description="Base64 Ed25519 signature over the nonce")


class PluginTokenResponse(BaseModel):
    """A short-lived device JWT for the authenticated plugin."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    timestamp: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_localhost(request: Request) -> bool:
    """Return ``True`` if the request originates from the loopback interface.

    Uses the real socket peer (``request.client.host``), not any forwarded
    header, so it cannot be spoofed by a remote client. This assumes nomothetic
    is *not* run behind a reverse proxy with ``--proxy-headers`` in device mode
    (it binds directly); if that ever changes, this loopback gate must become an
    explicit trust-boundary check (cf. ``rate_limit`` / the AP-subnet gate).
    """
    if request.client is None:
        return False
    try:
        return ip_address(request.client.host).is_loopback
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_plugin_auth_router() -> APIRouter:
    """Build the plugin auth router.

    The :class:`~nomothetic.plugin_auth.PluginKeyStore` and
    :class:`~nomothetic.plugin_auth.ChallengeStore` are read from
    ``request.app.state`` at call time (``plugin_key_store`` /
    ``plugin_challenge_store``), so test fixtures can inject fresh instances.

    Returns
    -------
    APIRouter
        Router with ``/register``, ``/challenge``, and ``/token`` endpoints.
    """
    router = APIRouter(prefix="/api/plugin", tags=["PluginAuth"])

    def _key_store(request: Request) -> PluginKeyStore:
        store: PluginKeyStore | None = getattr(request.app.state, "plugin_key_store", None)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Plugin auth not configured",
            )
        return store

    def _challenge_store(request: Request) -> ChallengeStore:
        store: ChallengeStore | None = getattr(request.app.state, "plugin_challenge_store", None)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Plugin auth not configured",
            )
        return store

    @router.post("/register", response_model=PluginRegisterResponse, status_code=200)
    async def register(body: PluginRegisterRequest, request: Request):
        """Register a plugin public key. Localhost only; key-stable.

        Raises
        ------
        HTTPException
            403 if the caller is not on the loopback interface.
            409 if a different key is already registered for the plugin.
            400 if the plugin name or public key is invalid.
        """
        if not _is_localhost(request):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Plugin registration is only available from localhost",
            )
        store = _key_store(request)
        try:
            # register() touches disk (mkstemp/write/rename); offload it.
            outcome = await asyncio.to_thread(store.register, body.plugin, body.public_key)
        except KeyConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except (InvalidPluginName, PluginAuthError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        logger.info("Plugin %r registration: %s", body.plugin, outcome)
        return PluginRegisterResponse(plugin=body.plugin, status=outcome, timestamp=_now())

    @router.get("/challenge", response_model=PluginChallengeResponse)
    async def challenge(plugin: str, request: Request):
        """Issue a single-use challenge nonce for a registered plugin.

        Raises
        ------
        HTTPException
            404 if the plugin is not registered.
            400 if the plugin name is invalid.
        """
        store = _key_store(request)
        try:
            known = await asyncio.to_thread(store.get_public_key, plugin)
        except InvalidPluginName as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if known is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"plugin {plugin!r} is not registered",
            )
        nonce, ttl = _challenge_store(request).issue(plugin)
        return PluginChallengeResponse(plugin=plugin, nonce=nonce, expires_in=ttl, timestamp=_now())

    @router.post("/token", response_model=PluginTokenResponse, status_code=200)
    async def token(body: PluginTokenRequest, request: Request):
        """Exchange a signed nonce for a device JWT.

        Raises
        ------
        HTTPException
            401 if the plugin is unknown, the nonce is invalid/expired, or the
            signature does not verify.
            400 if the signature is not valid base64.
            503 if the auth service is unavailable.
        """
        store = _key_store(request)
        try:
            public_key = await asyncio.to_thread(store.get_public_key, body.plugin)
        except InvalidPluginName as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if public_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication failed",
            )

        # Consume the nonce first (single-use) regardless of signature outcome,
        # so a stolen nonce cannot be retried with guessed signatures.
        if not _challenge_store(request).consume(body.plugin, body.nonce):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication failed",
            )

        try:
            signature = base64.b64decode(body.signature, validate=True)
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="signature is not valid base64",
            ) from exc

        # Ed25519 verification is CPU work; keep it off the event loop.
        verified = await asyncio.to_thread(
            verify_signature, public_key, body.nonce.encode("utf-8"), signature
        )
        if not verified:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication failed",
            )

        svc = get_auth_service()
        if svc is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Auth service not configured",
            )

        access_token = svc.create_plugin_token(body.plugin)
        logger.info("Issued device JWT to plugin %r", body.plugin)
        return PluginTokenResponse(
            access_token=access_token,
            expires_in=int(_PLUGIN_TOKEN_TTL.total_seconds()),
            timestamp=_now(),
        )

    return router
