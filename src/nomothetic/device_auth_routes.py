"""Device-mode authentication endpoints.

Provides pairing, token refresh, and profile retrieval for the
single-owner device authentication model.  No registration or login
endpoints — pairing IS the initial authentication step.

See ADR-014 for design rationale.
"""

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from nomothetic.auth import (
    AuthService,
    TokenPayload,
    get_auth_service,
    jwt_required,
)
from nomothetic.pairing import PairingState
from nomothetic.rate_limit import pairing_rate_limit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PairingStatusResponse(BaseModel):
    """Current device pairing status."""

    paired: bool
    pairing_available: bool


class PairRequest(BaseModel):
    """Device pairing request body."""

    secret: str = Field(..., min_length=1, description="Pairing secret from device console")
    display_name: str = Field(..., min_length=1, max_length=100, description="Owner display name")


class PairResponse(BaseModel):
    """Successful pairing response with tokens."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    """Token refresh request body."""

    refresh_token: str = Field(..., description="Refresh token from pairing")


class TokenResponse(BaseModel):
    """Authentication token response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class DeviceUserResponse(BaseModel):
    """Paired device owner profile."""

    email: str
    display_name: str
    created_at: str
    last_login_at: str | None
    timestamp: str


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_device_auth_router() -> APIRouter:
    """Build and return the device auth API router.

    The ``PairingState`` and ``AuthService`` instances are read from
    ``request.app.state`` at call time, allowing test fixtures to inject
    fresh instances per test client.

    Returns
    -------
    APIRouter
        Router with pairing, refresh, and profile endpoints.
    """
    router = APIRouter(prefix="/api/device/auth", tags=["DeviceAuth"])

    def _get_pairing(request: Request) -> PairingState:
        pairing: PairingState | None = getattr(request.app.state, "pairing_state", None)
        if pairing is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Pairing not configured",
            )
        return pairing

    def _require_service() -> AuthService:
        svc = get_auth_service()
        if svc is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Auth service not configured",
            )
        return svc

    @router.get("/status", response_model=PairingStatusResponse)
    async def pairing_status(request: Request):
        """Return the current pairing status.

        Returns
        -------
        PairingStatusResponse
            Whether the device is paired and whether a pairing secret is available.
        """
        pairing = _get_pairing(request)
        return PairingStatusResponse(
            paired=pairing.is_paired(),
            pairing_available=pairing.secret is not None and not pairing.is_paired(),
        )

    @router.post(
        "/pair",
        response_model=PairResponse,
        status_code=200,
        dependencies=[Depends(pairing_rate_limit)],
    )
    async def pair(request_body: PairRequest, request: Request):
        """Pair with the device using the console secret.

        Creates the owner user account and issues JWT tokens.

        Parameters
        ----------
        request_body : PairRequest
            The pairing secret and owner display name.

        Returns
        -------
        PairResponse
            JWT access and refresh tokens.

        Raises
        ------
        HTTPException
            401 if the secret is wrong.
            409 if the device is already paired.
            429 if rate limit is exceeded.
        """
        pairing = _get_pairing(request)
        svc = _require_service()

        if pairing.is_paired():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Device is already paired",
            )

        if not pairing.verify_and_consume(request_body.secret):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid pairing secret",
            )

        # Create the device owner user
        owner_email = "device-owner@local"
        random_password = secrets.token_urlsafe(32)
        try:
            await svc.create_user(owner_email, random_password, request_body.display_name)
        except ValueError:
            # User already exists (e.g. from a previous pairing cycle) — continue
            pass

        pairing.owner_email = owner_email
        tokens = await svc.create_tokens(owner_email)

        logger.info("Device paired successfully for %s", request_body.display_name)

        return PairResponse(
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_type=tokens["token_type"],
            expires_in=tokens["expires_in"],
        )

    @router.post("/refresh", response_model=TokenResponse)
    async def refresh(request_body: RefreshRequest):
        """Rotate a refresh token and issue new tokens.

        Returns
        -------
        TokenResponse
            New JWT access and refresh tokens.

        Raises
        ------
        HTTPException
            401 on invalid or expired refresh token.
        """
        svc = _require_service()
        try:
            tokens = await svc.refresh_token(request_body.refresh_token)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
            ) from exc
        return TokenResponse(**tokens)

    @router.get("/me", response_model=DeviceUserResponse)
    async def me(claims: TokenPayload = Depends(jwt_required)):
        """Return the paired owner's profile.

        Returns
        -------
        DeviceUserResponse
            Owner email, display name, and timestamps.

        Raises
        ------
        HTTPException
            401 if the token is missing or invalid.
            404 if the user no longer exists.
        """
        svc = _require_service()
        user = await svc.get_user(claims.sub)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        return DeviceUserResponse(
            email=user.email,
            display_name=user.display_name,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    return router
