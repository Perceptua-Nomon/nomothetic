"""JWT authentication module for central-mode deployment.

Provides ``AuthService`` for user creation, authentication, and token
management, plus a ``jwt_required`` FastAPI dependency for route protection.
See ADR-010 for design rationale.
"""

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

if TYPE_CHECKING:
    from nomothetic.token_store import TokenStore
    from nomothetic.user_store import UserStore

try:
    import bcrypt

    _BCRYPT_AVAILABLE = True
except ImportError:  # pragma: no cover
    bcrypt = None  # type: ignore[assignment]
    _BCRYPT_AVAILABLE = False

try:
    from authlib.jose import jwt as _authlib_jwt
    from authlib.jose.errors import ExpiredTokenError, JoseError

    _JWT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _authlib_jwt = None  # type: ignore[assignment]
    _JWT_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ACCESS_TOKEN_TTL = timedelta(minutes=15)
_REFRESH_TOKEN_TTL = timedelta(days=7)
_MIN_SECRET_LENGTH = 32
_JWT_ALGORITHM = "HS256"
_JWT_ISSUER = "nomon-central"

# ---------------------------------------------------------------------------
# Bearer token extraction
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class TokenPayload(BaseModel):
    """Decoded JWT access-token claims."""

    sub: str
    exp: int
    iat: int
    iss: str


class UserRecord(BaseModel):
    """Minimal user record for in-memory operations."""

    email: str
    display_name: str
    password_hash: str
    created_at: str
    last_login_at: Optional[str] = None
    active: bool = True


# ---------------------------------------------------------------------------
# AuthService
# ---------------------------------------------------------------------------


class AuthService:
    """Self-hosted JWT authentication service.

    Parameters
    ----------
    secret : str, optional
        JWT signing secret.  Falls back to ``NOMON_JWT_SECRET`` env var.

    Raises
    ------
    ValueError
        If the secret is missing or shorter than 32 bytes.
    RuntimeError
        If required dependencies (``authlib``, ``bcrypt``) are not installed.
    """

    def __init__(
        self,
        secret: Optional[str] = None,
        user_store: Optional["UserStore"] = None,
        token_store: Optional["TokenStore"] = None,
        issuer: str = "nomon-central",
    ) -> None:
        if not _JWT_AVAILABLE or not _BCRYPT_AVAILABLE:
            raise RuntimeError(
                "Auth dependencies missing.  Install with: pip install nomothetic[auth]"
            )

        resolved = secret or os.environ.get("NOMON_JWT_SECRET")
        if not resolved or len(resolved) < _MIN_SECRET_LENGTH:
            raise ValueError(
                "NOMON_JWT_SECRET must be set and at least " f"{_MIN_SECRET_LENGTH} characters long"
            )
        self._secret: str = resolved
        self._issuer: str = issuer

        if user_store is None:
            from nomothetic.user_store import InMemoryUserStore

            user_store = InMemoryUserStore()
        self._user_store = user_store

        if token_store is None:
            from nomothetic.token_store import InMemoryTokenStore

            token_store = InMemoryTokenStore()
        self._token_store = token_store

    # -- password hashing ---------------------------------------------------

    def hash_password(self, password: str) -> str:
        """Hash a plaintext password with bcrypt.

        Parameters
        ----------
        password : str
            The plaintext password (must be non-empty).

        Returns
        -------
        str
            bcrypt hash string.
        """
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()

    def verify_password(self, password: str, password_hash: str) -> bool:
        """Verify a plaintext password against a bcrypt hash.

        Parameters
        ----------
        password : str
            Plaintext candidate.
        password_hash : str
            Stored bcrypt hash.

        Returns
        -------
        bool
            True if the password matches.
        """
        return bcrypt.checkpw(password.encode(), password_hash.encode())

    # -- user management ----------------------------------------------------

    async def create_user(self, email: str, password: str, display_name: str) -> UserRecord:
        """Create a new user account.

        Parameters
        ----------
        email : str
            Unique email address.
        password : str
            Plaintext password (hashed before storage).
        display_name : str
            User-visible name.

        Returns
        -------
        UserRecord
            The newly created user.

        Raises
        ------
        ValueError
            If the email is already registered.
        """
        normalised = email.strip().lower()
        if await self._user_store.user_exists(normalised):
            raise ValueError(f"Email already registered: {normalised}")

        return await self._user_store.create_user(
            email=normalised,
            display_name=display_name,
            password_hash=self.hash_password(password),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    async def authenticate(self, email: str, password: str) -> Optional[UserRecord]:
        """Authenticate a user by email and password.

        Parameters
        ----------
        email : str
            Login email.
        password : str
            Plaintext password.

        Returns
        -------
        UserRecord or None
            The user if credentials are valid, otherwise None.
        """
        normalised = email.strip().lower()
        user = await self._user_store.get_user(normalised)
        if user is None or not user.active:
            return None
        if not self.verify_password(password, user.password_hash):
            return None
        now = datetime.now(timezone.utc)
        user.last_login_at = now.isoformat()
        await self._user_store.update_user(
            normalised, last_login_at=now.strftime("%Y-%m-%d %H:%M:%S")
        )
        return user

    async def get_user(self, email: str) -> Optional[UserRecord]:
        """Look up a user by email.

        Parameters
        ----------
        email : str
            The email to look up.

        Returns
        -------
        UserRecord or None
        """
        return await self._user_store.get_user(email.strip().lower())

    # -- token creation -----------------------------------------------------

    def create_access_token(self, email: str) -> str:
        """Create a short-lived JWT access token.

        Parameters
        ----------
        email : str
            The ``sub`` claim (user email).

        Returns
        -------
        str
            Encoded JWT string.
        """
        now = datetime.now(timezone.utc)
        payload = {
            "sub": email,
            "iat": int(now.timestamp()),
            "exp": int((now + _ACCESS_TOKEN_TTL).timestamp()),
            "iss": self._issuer,
        }
        header = {"alg": _JWT_ALGORITHM}
        token = _authlib_jwt.encode(header, payload, self._secret)
        return token.decode("utf-8") if isinstance(token, bytes) else str(token)

    def create_registration_proof(self, vin: str, ttl_seconds: int = 300) -> str:
        """Create a short-lived registration proof token for VIN ownership.

        The proof is a JWT signed with this service's secret. The caller
        submits it to the central fleet API alongside the VIN. The fleet API
        validates structural integrity (expiry, VIN binding, audience) but
        **cannot verify the signature** because the device and central services
        use separate secrets.

        Full cryptographic ownership verification requires asymmetric device
        certificates; this is planned for a future phase. The current proof
        provides time-binding and VIN-binding that raises the bar for VIN
        squatting attacks without requiring shared secrets.

        Parameters
        ----------
        vin : str
            Vehicle identification number to bind to this proof.
        ttl_seconds : int
            Validity window in seconds (default 300 = 5 minutes).

        Returns
        -------
        str
            Encoded JWT proof string.
        """
        import uuid
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        payload = {
            "iss": self._issuer,
            "sub": vin,
            "aud": "nomon-fleet",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
            "jti": str(uuid.uuid4()),
        }
        header = {"alg": _JWT_ALGORITHM}
        token = _authlib_jwt.encode(header, payload, self._secret)
        return token.decode("utf-8") if isinstance(token, bytes) else str(token)

    async def create_refresh_token(self, email: str) -> str:
        """Create an opaque refresh token and store its hash.

        Parameters
        ----------
        email : str
            The owning user's email.

        Returns
        -------
        str
            The raw refresh token (returned to the client).
        """
        raw = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        expires_at = datetime.now(timezone.utc) + _REFRESH_TOKEN_TTL
        await self._token_store.store_token(token_hash, email.strip().lower(), expires_at)
        return raw

    async def create_tokens(self, email: str) -> dict:
        """Create both access and refresh tokens.

        Parameters
        ----------
        email : str
            The user email.

        Returns
        -------
        dict
            ``access_token``, ``refresh_token``, ``token_type``, ``expires_in``.
        """
        return {
            "access_token": self.create_access_token(email),
            "refresh_token": await self.create_refresh_token(email),
            "token_type": "bearer",
            "expires_in": int(_ACCESS_TOKEN_TTL.total_seconds()),
        }

    def update_signing_secret(self, secret: str) -> None:
        """Replace the JWT signing secret used for access tokens."""
        if len(secret) < _MIN_SECRET_LENGTH:
            raise ValueError(
                f"JWT signing secret must be at least {_MIN_SECRET_LENGTH} characters long"
            )
        self._secret = secret

    # -- token verification -------------------------------------------------

    def verify_token(self, token: str) -> TokenPayload:
        """Decode and validate a JWT access token.

        Parameters
        ----------
        token : str
            The encoded JWT.

        Returns
        -------
        TokenPayload
            Decoded claims.

        Raises
        ------
        ValueError
            If the token is expired, malformed, or has an invalid issuer.
        """
        try:
            claims = _authlib_jwt.decode(
                token,
                self._secret,
                claims_options={
                    "iss": {"essential": True, "value": self._issuer},
                },
            )
            claims.validate()
        except ExpiredTokenError as exc:
            raise ValueError("Token has expired") from exc
        except JoseError as exc:
            raise ValueError(f"Invalid token: {exc}") from exc
        return TokenPayload(**dict(claims))

    async def refresh_token(self, raw_refresh: str) -> dict:
        """Rotate a refresh token and issue new tokens.

        The old refresh token is invalidated.

        Parameters
        ----------
        raw_refresh : str
            The refresh token previously issued to the client.

        Returns
        -------
        dict
            New ``access_token``, ``refresh_token``, ``token_type``, ``expires_in``.

        Raises
        ------
        ValueError
            If the refresh token is unknown or expired.
        """
        token_hash = hashlib.sha256(raw_refresh.encode()).hexdigest()
        email = await self._token_store.get_email(token_hash)
        if email is None:
            raise ValueError("Invalid or expired refresh token")
        # Invalidate old token
        await self._token_store.delete_token(token_hash)
        user = await self._user_store.get_user(email)
        if user is None or not user.active:
            raise ValueError("User account is inactive or deleted")
        return await self.create_tokens(email)

    async def revoke_refresh_token(self, raw_refresh: str) -> bool:
        """Revoke a refresh token. Returns True if it existed."""
        token_hash = hashlib.sha256(raw_refresh.encode()).hexdigest()
        return await self._token_store.delete_token(token_hash)

    async def revoke_all_tokens(self, email: str) -> int:
        """Revoke all refresh tokens for a user.

        Called during re-pairing to invalidate any sessions from the
        previous pairing cycle.

        Parameters
        ----------
        email : str
            The user whose tokens should be revoked.

        Returns
        -------
        int
            Number of tokens revoked.
        """
        return await self._token_store.delete_tokens_for_user(email.strip().lower())


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

# Module-level reference set by create_app() at startup.
_auth_service: Optional[AuthService] = None


def set_auth_service(service: AuthService) -> None:
    """Store the AuthService instance for use by ``jwt_required``."""
    global _auth_service
    _auth_service = service


def get_auth_service() -> Optional[AuthService]:
    """Return the current AuthService instance (if configured)."""
    return _auth_service


async def jwt_required(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> TokenPayload:
    """FastAPI dependency that enforces a valid JWT access token.

    Extracts the ``Authorization: Bearer <token>`` header, decodes and
    validates the JWT, and returns the decoded claims.

    Returns
    -------
    TokenPayload
        Decoded token claims (``sub``, ``exp``, ``iat``, ``iss``).

    Raises
    ------
    HTTPException
        401 if the token is missing, expired, or invalid.
    """
    if _auth_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service not configured",
        )
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return _auth_service.verify_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
