"""Tests for user store implementations."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from nomothetic.user_store import (
    InMemoryUserStore,
    SqlUserStore,
)

# ============================================================================
# InMemoryUserStore
# ============================================================================


class TestInMemoryUserStore:
    """Tests for the in-memory user store."""

    @pytest.fixture
    def store(self):
        return InMemoryUserStore()

    @pytest.mark.asyncio
    async def test_create_user(self, store):
        """Creating a user returns a UserRecord."""
        user = await store.create_user(
            email="alice@example.com",
            display_name="Alice",
            password_hash="hashed",
            created_at="2026-01-01T00:00:00+00:00",
        )
        assert user.email == "alice@example.com"
        assert user.display_name == "Alice"
        assert user.password_hash == "hashed"
        assert user.active is True

    @pytest.mark.asyncio
    async def test_get_user(self, store):
        """get_user returns the stored record."""
        await store.create_user("bob@example.com", "Bob", "hashed", "2026-01-01T00:00:00+00:00")
        user = await store.get_user("bob@example.com")
        assert user is not None
        assert user.email == "bob@example.com"

    @pytest.mark.asyncio
    async def test_get_user_not_found(self, store):
        """get_user returns None for unknown email."""
        assert await store.get_user("nobody@example.com") is None

    @pytest.mark.asyncio
    async def test_user_exists(self, store):
        """user_exists returns True after creation."""
        await store.create_user("carol@example.com", "Carol", "hashed", "2026-01-01T00:00:00+00:00")
        assert await store.user_exists("carol@example.com") is True
        assert await store.user_exists("nobody@example.com") is False

    @pytest.mark.asyncio
    async def test_create_duplicate_raises(self, store):
        """Creating a user with an existing email raises ValueError."""
        await store.create_user("dup@example.com", "Dup", "hashed", "2026-01-01T00:00:00+00:00")
        with pytest.raises(ValueError, match="already registered"):
            await store.create_user(
                "dup@example.com", "Dup2", "hashed2", "2026-01-01T00:00:00+00:00"
            )

    @pytest.mark.asyncio
    async def test_update_user(self, store):
        """update_user modifies the specified fields."""
        await store.create_user("update@example.com", "Old", "hashed", "2026-01-01T00:00:00+00:00")
        updated = await store.update_user(
            "update@example.com", last_login_at="2026-06-01T00:00:00+00:00"
        )
        assert updated is not None
        assert updated.last_login_at == "2026-06-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_update_user_not_found(self, store):
        """update_user returns None for unknown email."""
        assert await store.update_user("nobody@example.com", display_name="X") is None


# ============================================================================
# Update field whitelist
# ============================================================================


class TestUpdateUserFieldWhitelist:
    """Tests for field whitelist enforcement in update_user."""

    @pytest.fixture
    def inmemory_store(self):
        return InMemoryUserStore()

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        db.execute_sql = AsyncMock()
        return db

    @pytest.fixture
    def sql_store(self, mock_db):
        return SqlUserStore(mock_db)

    @pytest.mark.asyncio
    async def test_update_user_rejects_email_key_inmemory(self, inmemory_store):
        """InMemoryUserStore rejects updates to 'email' (protected by signature)."""
        await inmemory_store.create_user("a@b.com", "A", "h", "2026-01-01T00:00:00+00:00")
        with pytest.raises(TypeError):
            await inmemory_store.update_user("a@b.com", **{"email": "evil@b.com"})

    @pytest.mark.asyncio
    async def test_update_user_rejects_email_key_sql(self, sql_store):
        """SqlUserStore rejects updates to 'email' (protected by signature)."""
        with pytest.raises(TypeError):
            await sql_store.update_user("a@b.com", **{"email": "evil@b.com"})

    @pytest.mark.asyncio
    async def test_update_user_rejects_password_hash_key_inmemory(self, inmemory_store):
        """InMemoryUserStore rejects updates to 'password_hash'."""
        await inmemory_store.create_user("a@b.com", "A", "h", "2026-01-01T00:00:00+00:00")
        with pytest.raises(ValueError, match="Cannot update field"):
            await inmemory_store.update_user("a@b.com", password_hash="evil")

    @pytest.mark.asyncio
    async def test_update_user_rejects_password_hash_key_sql(self, sql_store):
        """SqlUserStore rejects updates to 'password_hash'."""
        with pytest.raises(ValueError, match="Cannot update field"):
            await sql_store.update_user("a@b.com", password_hash="evil")

    @pytest.mark.asyncio
    async def test_update_user_rejects_unknown_key_inmemory(self, inmemory_store):
        """InMemoryUserStore rejects unknown fields like __class__."""
        await inmemory_store.create_user("a@b.com", "A", "h", "2026-01-01T00:00:00+00:00")
        with pytest.raises(ValueError, match="Cannot update field"):
            await inmemory_store.update_user("a@b.com", **{"__class__": "X"})

    @pytest.mark.asyncio
    async def test_update_user_rejects_unknown_key_sql(self, sql_store):
        """SqlUserStore rejects unknown fields like __class__."""
        with pytest.raises(ValueError, match="Cannot update field"):
            await sql_store.update_user("a@b.com", **{"__class__": "X"})

    @pytest.mark.asyncio
    async def test_update_user_allows_display_name_inmemory(self, inmemory_store):
        """InMemoryUserStore allows updating display_name."""
        await inmemory_store.create_user("a@b.com", "A", "h", "2026-01-01T00:00:00+00:00")
        result = await inmemory_store.update_user("a@b.com", display_name="B")
        assert result is not None
        assert result.display_name == "B"

    @pytest.mark.asyncio
    async def test_update_user_allows_display_name_sql(self, sql_store, mock_db):
        """SqlUserStore allows updating display_name."""
        mock_db.execute_sql.side_effect = [
            [],  # UPDATE result
            [
                {
                    "email": "a@b.com",
                    "display_name": "B",
                    "password_hash": "h",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "active": True,
                }
            ],  # get_user SELECT
        ]
        result = await sql_store.update_user("a@b.com", display_name="B")
        assert result is not None
        assert result.display_name == "B"

    @pytest.mark.asyncio
    async def test_update_user_allows_last_login_at_inmemory(self, inmemory_store):
        """InMemoryUserStore allows updating last_login_at."""
        await inmemory_store.create_user("a@b.com", "A", "h", "2026-01-01T00:00:00+00:00")
        result = await inmemory_store.update_user(
            "a@b.com", last_login_at="2026-06-01T00:00:00+00:00"
        )
        assert result is not None
        assert result.last_login_at == "2026-06-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_update_user_allows_last_login_at_sql(self, sql_store, mock_db):
        """SqlUserStore allows updating last_login_at."""
        mock_db.execute_sql.side_effect = [
            [],  # UPDATE result
            [
                {
                    "email": "a@b.com",
                    "display_name": "A",
                    "password_hash": "h",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "last_login_at": "2026-06-01T00:00:00+00:00",
                    "active": True,
                }
            ],  # get_user SELECT
        ]
        result = await sql_store.update_user("a@b.com", last_login_at="2026-06-01T00:00:00+00:00")
        assert result is not None


# ============================================================================
# SqlUserStore
# ============================================================================


class TestSqlUserStore:
    """Tests for the ArcadeDB-backed user store."""

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        db.execute_sql = AsyncMock()
        return db

    @pytest.fixture
    def store(self, mock_db):
        return SqlUserStore(mock_db)

    @pytest.mark.asyncio
    async def test_get_user_found(self, store, mock_db):
        """get_user returns a UserRecord when the row exists."""
        mock_db.execute_sql.return_value = [
            {
                "email": "alice@example.com",
                "display_name": "Alice",
                "password_hash": "hashed",
                "created_at": "2026-01-01T00:00:00+00:00",
                "active": True,
            }
        ]
        user = await store.get_user("alice@example.com")
        assert user is not None
        assert user.email == "alice@example.com"
        query = mock_db.execute_sql.call_args[0][0]
        assert "FROM User" in query

    @pytest.mark.asyncio
    async def test_get_user_not_found(self, store, mock_db):
        """get_user returns None when no row matches."""
        mock_db.execute_sql.return_value = []
        assert await store.get_user("nobody@example.com") is None

    @pytest.mark.asyncio
    async def test_create_user(self, store, mock_db):
        """create_user sends INSERT SQL and returns a UserRecord."""
        mock_db.execute_sql.side_effect = [
            [{"count": 0}],  # user_exists -> count = 0
            [],  # INSERT result
        ]
        user = await store.create_user(
            "new@example.com", "New", "hashed", "2026-01-01T00:00:00+00:00"
        )
        assert user.email == "new@example.com"
        assert mock_db.execute_sql.call_count == 2
        create_query = mock_db.execute_sql.call_args_list[1][0][0]
        assert "INSERT INTO User" in create_query

    @pytest.mark.asyncio
    async def test_create_user_duplicate(self, store, mock_db):
        """create_user raises ValueError when email already exists."""
        mock_db.execute_sql.return_value = [{"count": 1}]
        with pytest.raises(ValueError, match="already registered"):
            await store.create_user("dup@example.com", "Dup", "hashed", "2026-01-01T00:00:00+00:00")

    @pytest.mark.asyncio
    async def test_user_exists_true(self, store, mock_db):
        """user_exists returns True when count > 0."""
        mock_db.execute_sql.return_value = [{"count": 1}]
        assert await store.user_exists("alice@example.com") is True

    @pytest.mark.asyncio
    async def test_user_exists_false(self, store, mock_db):
        """user_exists returns False when count is 0."""
        mock_db.execute_sql.return_value = [{"count": 0}]
        assert await store.user_exists("nobody@example.com") is False

    @pytest.mark.asyncio
    async def test_update_user(self, store, mock_db):
        """update_user sends UPDATE SQL then fetches via get_user."""
        mock_db.execute_sql.side_effect = [
            [],  # UPDATE result
            [
                {
                    "email": "alice@example.com",
                    "display_name": "Alice",
                    "password_hash": "hashed",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "last_login_at": "2026-06-01T00:00:00+00:00",
                    "active": True,
                }
            ],  # get_user SELECT
        ]
        user = await store.update_user(
            "alice@example.com", last_login_at="2026-06-01T00:00:00+00:00"
        )
        assert user is not None
        update_query = mock_db.execute_sql.call_args_list[0][0][0]
        assert "UPDATE User" in update_query

    @pytest.mark.asyncio
    async def test_update_user_not_found(self, store, mock_db):
        """update_user returns None when user not found."""
        mock_db.execute_sql.side_effect = [
            [],  # UPDATE result
            [],  # get_user returns nothing
        ]
        assert await store.update_user("nobody@example.com", display_name="X") is None
