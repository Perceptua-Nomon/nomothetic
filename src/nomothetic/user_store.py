"""User persistence layer with in-memory and ArcadeDB backends.

Protocol-based store abstraction. AuthService delegates user CRUD to a
UserStore implementation, enabling test/dev in-memory mode and production
ArcadeDB persistence behind the same interface.
"""

import logging
from typing import TYPE_CHECKING, Any, Optional, runtime_checkable

from typing_extensions import Protocol

from nomothetic.auth import UserRecord
from nomothetic.gremlin_utils import sanitize_gremlin_value as _sanitize_gremlin_value

if TYPE_CHECKING:
    from nomothetic.db import DatabaseClient

logger = logging.getLogger(__name__)


def _coerce_count(rows: list[Any]) -> int:
    if not rows:
        return 0
    first = rows[0]
    if isinstance(first, int):
        return first
    if isinstance(first, float):
        return int(first)
    if isinstance(first, dict):
        val = first.get("count", 0)
        if isinstance(val, (int, float)):
            return int(val)
    return 0


_ALLOWED_USER_UPDATE_FIELDS = frozenset({"display_name", "last_login_at", "active"})


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class UserStore(Protocol):
    """Abstract interface for user persistence."""

    async def get_user(self, email: str) -> Optional[UserRecord]:
        """Look up a user by email.

        Parameters
        ----------
        email : str
            Normalised (lower-case, stripped) email address.

        Returns
        -------
        UserRecord or None
        """
        ...  # pragma: no cover

    async def create_user(
        self,
        email: str,
        display_name: str,
        password_hash: str,
        created_at: str,
    ) -> UserRecord:
        """Persist a new user record.

        Parameters
        ----------
        email : str
            Normalised email.
        display_name : str
            User-visible name.
        password_hash : str
            bcrypt hash string.
        created_at : str
            ISO-8601 UTC timestamp.

        Returns
        -------
        UserRecord
            The newly created record.
        """
        ...  # pragma: no cover

    async def update_user(self, email: str, **fields: str) -> Optional[UserRecord]:
        """Update mutable fields on an existing user.

        Parameters
        ----------
        email : str
            Normalised email address.
        **fields : str
            Field names and new values to set.

        Returns
        -------
        UserRecord or None
            Updated record, or None if the user does not exist.
        """
        ...  # pragma: no cover

    async def user_exists(self, email: str) -> bool:
        """Check whether a user with the given email exists.

        Parameters
        ----------
        email : str
            Normalised email address.

        Returns
        -------
        bool
        """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryUserStore:
    """Dict-backed user store for testing and development.

    Thread-safety note: sufficient for single-process FastAPI (uvicorn)
    deployments; not suitable for multi-process workers.
    """

    def __init__(self) -> None:
        self._users: dict[str, UserRecord] = {}

    async def get_user(self, email: str) -> Optional[UserRecord]:
        """Return the user record for *email*, or ``None``."""
        return self._users.get(email)

    async def create_user(
        self,
        email: str,
        display_name: str,
        password_hash: str,
        created_at: str,
    ) -> UserRecord:
        """Create and store a new user record.

        Raises
        ------
        ValueError
            If the email is already registered.
        """
        if email in self._users:
            raise ValueError(f"Email already registered: {email}")
        record = UserRecord(
            email=email,
            display_name=display_name,
            password_hash=password_hash,
            created_at=created_at,
            active=True,
        )
        self._users[email] = record
        return record

    async def update_user(self, email: str, **fields: str) -> Optional[UserRecord]:
        """Update fields on an existing user record.

        Returns ``None`` if the user does not exist.
        """
        for key in fields:
            if key not in _ALLOWED_USER_UPDATE_FIELDS:
                raise ValueError(f"Cannot update field: {key!r}")
        user = self._users.get(email)
        if user is None:
            return None
        for key, value in fields.items():
            if hasattr(user, key):
                setattr(user, key, value)
        return user

    async def user_exists(self, email: str) -> bool:
        """Return ``True`` if a user with *email* exists."""
        return email in self._users


# ---------------------------------------------------------------------------
# Gremlin implementation
# ---------------------------------------------------------------------------


class GremlinUserStore:
    """ArcadeDB-backed user store using Gremlin traversals.

    Parameters
    ----------
    db : DatabaseClient
        An initialised database client.
    """

    def __init__(self, db: "DatabaseClient") -> None:
        self._db = db

    async def get_user(self, email: str) -> Optional[UserRecord]:
        """Fetch a user vertex by email."""
        safe_email = _sanitize_gremlin_value(email)
        query = f"g.V().hasLabel('User').has('email', '{safe_email}').elementMap()"
        rows = await self._db.execute_gremlin(query)
        if not rows:
            return None
        row = rows[0]
        return UserRecord(
            email=row["email"],
            display_name=row["display_name"],
            password_hash=row["password_hash"],
            created_at=row["created_at"],
            last_login_at=row.get("last_login_at"),
            active=row.get("active", True),
        )

    async def create_user(
        self,
        email: str,
        display_name: str,
        password_hash: str,
        created_at: str,
    ) -> UserRecord:
        """Insert a new User vertex via Gremlin.

        Raises
        ------
        ValueError
            If the email already exists.
        """
        safe_email = _sanitize_gremlin_value(email)
        safe_name = _sanitize_gremlin_value(display_name)
        safe_hash = _sanitize_gremlin_value(password_hash)
        safe_created = _sanitize_gremlin_value(created_at)

        if await self.user_exists(email):
            raise ValueError(f"Email already registered: {email}")

        query = (
            f"g.addV('User')"
            f".property('email', '{safe_email}')"
            f".property('display_name', '{safe_name}')"
            f".property('password_hash', '{safe_hash}')"
            f".property('created_at', '{safe_created}')"
            f".property('active', true)"
            f".elementMap()"
        )
        await self._db.execute_gremlin(query)
        return UserRecord(
            email=email,
            display_name=display_name,
            password_hash=password_hash,
            created_at=created_at,
            active=True,
        )

    async def update_user(self, email: str, **fields: str) -> Optional[UserRecord]:
        """Update properties on an existing User vertex."""
        for key in fields:
            if key not in _ALLOWED_USER_UPDATE_FIELDS:
                raise ValueError(f"Cannot update field: {key!r}")
        safe_email = _sanitize_gremlin_value(email)
        prop_chain = ""
        for key, value in fields.items():
            safe_val = _sanitize_gremlin_value(str(value))
            prop_chain += f".property('{key}', '{safe_val}')"

        if not prop_chain:
            return await self.get_user(email)

        query = f"g.V().hasLabel('User').has('email', '{safe_email}')" f"{prop_chain}.elementMap()"
        rows = await self._db.execute_gremlin(query)
        if not rows:
            return None
        row = rows[0]
        return UserRecord(
            email=row["email"],
            display_name=row["display_name"],
            password_hash=row["password_hash"],
            created_at=row["created_at"],
            last_login_at=row.get("last_login_at"),
            active=row.get("active", True),
        )

    async def user_exists(self, email: str) -> bool:
        """Return whether a User vertex with the given email exists."""
        safe_email = _sanitize_gremlin_value(email)
        query = f"g.V().hasLabel('User').has('email', '{safe_email}').count()"
        rows = await self._db.execute_gremlin(query)
        return _coerce_count(rows) > 0
