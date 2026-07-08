"""Refresh token persistence layer with in-memory and ArcadeDB backends.

Protocol-based store following the same pattern as UserStore/FleetStore.
AuthService delegates token CRUD to a TokenStore implementation.
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional, runtime_checkable

from typing_extensions import Protocol

from nomothetic.db_utils import coerce_count as _coerce_count
from nomothetic.db_utils import to_db_datetime

if TYPE_CHECKING:
    from nomothetic.db import DatabaseClient

logger = logging.getLogger(__name__)


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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


class SqlTokenStore:
    """ArcadeDB-backed token store using parameterized SQL queries."""

    def __init__(self, db: "DatabaseClient") -> None:
        self._db = db

    async def store_token(self, token_hash: str, email: str, expires_at: datetime) -> None:
        """Persist a new refresh token."""
        query = (
            "INSERT INTO RefreshToken SET token_hash = :token_hash,"
            " email = :email, created_at = :created_at, expires_at = :expires_at"
        )
        await self._db.execute_sql(
            query,
            {
                "token_hash": token_hash,
                "email": email,
                "created_at": to_db_datetime(datetime.now(timezone.utc)),
                "expires_at": to_db_datetime(expires_at),
            },
        )

    async def get_email(self, token_hash: str) -> Optional[str]:
        """Look up the owning email for a token hash.

        Returns None if the token is not found or expired.
        """
        query = "SELECT email, expires_at FROM RefreshToken WHERE token_hash = :token_hash"
        rows = await self._db.execute_sql(query, {"token_hash": token_hash})
        if not rows:
            return None
        row = rows[0]
        expires_at = _coerce_datetime(row.get("expires_at"))
        now = datetime.now(timezone.utc)
        if expires_at is not None and expires_at <= now:
            await self.delete_token(token_hash)
            return None
        return row.get("email")

    async def delete_token(self, token_hash: str) -> bool:
        """Delete a single token. Returns True if it existed."""
        check = "SELECT count(*) as count FROM RefreshToken WHERE token_hash = :token_hash"
        rows = await self._db.execute_sql(check, {"token_hash": token_hash})
        if _coerce_count(rows) == 0:
            return False
        delete = "DELETE FROM RefreshToken WHERE token_hash = :token_hash"
        await self._db.execute_sql(delete, {"token_hash": token_hash})
        return True

    async def delete_tokens_for_user(self, email: str) -> int:
        """Delete all tokens for a user. Returns count deleted."""
        count_q = "SELECT count(*) as count FROM RefreshToken WHERE email = :email"
        rows = await self._db.execute_sql(count_q, {"email": email})
        count = _coerce_count(rows)
        if count > 0:
            delete = "DELETE FROM RefreshToken WHERE email = :email"
            await self._db.execute_sql(delete, {"email": email})
        return count

    async def cleanup_expired(self) -> int:
        """Remove all expired tokens. Returns count removed."""
        now = to_db_datetime(datetime.now(timezone.utc))
        count_q = "SELECT count(*) as count FROM RefreshToken WHERE expires_at <= :now"
        rows = await self._db.execute_sql(count_q, {"now": now})
        count = _coerce_count(rows)
        if count > 0:
            delete = "DELETE FROM RefreshToken WHERE expires_at <= :now"
            await self._db.execute_sql(delete, {"now": now})
        return count
