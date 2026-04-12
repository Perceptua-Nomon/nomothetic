"""Refresh token persistence layer with in-memory and ArcadeDB backends.

Protocol-based store following the same pattern as UserStore/FleetStore.
AuthService delegates token CRUD to a TokenStore implementation.
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional, runtime_checkable

from typing_extensions import Protocol

from nomothetic.gremlin_utils import sanitize_gremlin_value as _sanitize_gremlin_value

if TYPE_CHECKING:
    from nomothetic.db import DatabaseClient

logger = logging.getLogger(__name__)


@runtime_checkable
class TokenStore(Protocol):
    """Abstract interface for refresh token persistence."""

    async def store_token(self, token_hash: str, email: str, expires_at: datetime) -> None:
        """Persist a new refresh token."""
        ...  # pragma: no cover

    async def get_email(self, token_hash: str) -> Optional[str]:
        """Look up the owning email for a token hash.
        Returns None if the token is not found or expired."""
        ...  # pragma: no cover

    async def delete_token(self, token_hash: str) -> bool:
        """Delete a single token. Returns True if it existed."""
        ...  # pragma: no cover

    async def delete_tokens_for_user(self, email: str) -> int:
        """Delete all tokens for a user. Returns count deleted."""
        ...  # pragma: no cover

    async def cleanup_expired(self) -> int:
        """Remove all expired tokens. Returns count removed."""
        ...  # pragma: no cover


class InMemoryTokenStore:
    """Dict-backed token store for testing and development."""

    def __init__(self) -> None:
        # token_hash -> (email, expires_at)
        self._tokens: dict[str, tuple[str, datetime]] = {}

    async def store_token(self, token_hash: str, email: str, expires_at: datetime) -> None:
        self._tokens[token_hash] = (email, expires_at)

    async def get_email(self, token_hash: str) -> Optional[str]:
        entry = self._tokens.get(token_hash)
        if entry is None:
            return None
        email, expires_at = entry
        if expires_at <= datetime.now(timezone.utc):
            # Expired — clean up lazily
            del self._tokens[token_hash]
            return None
        return email

    async def delete_token(self, token_hash: str) -> bool:
        return self._tokens.pop(token_hash, None) is not None

    async def delete_tokens_for_user(self, email: str) -> int:
        to_delete = [h for h, (e, _) in self._tokens.items() if e == email]
        for h in to_delete:
            del self._tokens[h]
        return len(to_delete)

    async def cleanup_expired(self) -> int:
        now = datetime.now(timezone.utc)
        expired = [h for h, (_, exp) in self._tokens.items() if exp <= now]
        for h in expired:
            del self._tokens[h]
        return len(expired)


class GremlinTokenStore:
    """ArcadeDB-backed token store using Gremlin traversals."""

    def __init__(self, db: "DatabaseClient") -> None:
        self._db = db

    async def store_token(self, token_hash: str, email: str, expires_at: datetime) -> None:
        safe_hash = _sanitize_gremlin_value(token_hash)
        safe_email = _sanitize_gremlin_value(email)
        safe_expires = _sanitize_gremlin_value(expires_at.isoformat())
        safe_created = _sanitize_gremlin_value(datetime.now(timezone.utc).isoformat())
        query = (
            f"g.addV('RefreshToken')"
            f".property('token_hash', '{safe_hash}')"
            f".property('email', '{safe_email}')"
            f".property('created_at', '{safe_created}')"
            f".property('expires_at', '{safe_expires}')"
        )
        await self._db.execute_gremlin(query)

    async def get_email(self, token_hash: str) -> Optional[str]:
        safe_hash = _sanitize_gremlin_value(token_hash)
        now = datetime.now(timezone.utc).isoformat()
        # Look up by hash; check expiry in application code for portability
        query = f"g.V().hasLabel('RefreshToken').has('token_hash', '{safe_hash}').elementMap()"
        rows = await self._db.execute_gremlin(query)
        if not rows:
            return None
        row = rows[0]
        expires_at_str = row.get("expires_at", "")
        if expires_at_str and expires_at_str <= now:
            # Expired — clean up
            await self.delete_token(token_hash)
            return None
        return row.get("email")

    async def delete_token(self, token_hash: str) -> bool:
        safe_hash = _sanitize_gremlin_value(token_hash)
        check = f"g.V().hasLabel('RefreshToken').has('token_hash', '{safe_hash}').count()"
        rows = await self._db.execute_gremlin(check)
        if not rows or rows[0] == 0:
            return False
        drop = f"g.V().hasLabel('RefreshToken').has('token_hash', '{safe_hash}').drop().iterate()"
        await self._db.execute_gremlin(drop)
        return True

    async def delete_tokens_for_user(self, email: str) -> int:
        safe_email = _sanitize_gremlin_value(email)
        count_q = f"g.V().hasLabel('RefreshToken').has('email', '{safe_email}').count()"
        rows = await self._db.execute_gremlin(count_q)
        count = rows[0] if rows else 0
        if count > 0:
            drop = f"g.V().hasLabel('RefreshToken').has('email', '{safe_email}').drop().iterate()"
            await self._db.execute_gremlin(drop)
        return count

    async def cleanup_expired(self) -> int:
        now = _sanitize_gremlin_value(datetime.now(timezone.utc).isoformat())
        count_q = f"g.V().hasLabel('RefreshToken').has('expires_at', lte('{now}')).count()"
        rows = await self._db.execute_gremlin(count_q)
        count = rows[0] if rows else 0
        if count > 0:
            drop = (
                f"g.V().hasLabel('RefreshToken').has('expires_at', lte('{now}')).drop().iterate()"
            )
            await self._db.execute_gremlin(drop)
        return count
