"""Tests for token store implementations."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from nomothetic.token_store import GremlinTokenStore, InMemoryTokenStore

# ============================================================================
# InMemoryTokenStore
# ============================================================================


class TestInMemoryTokenStore:
    """Tests for the in-memory token store."""

    @pytest.fixture
    def store(self):
        return InMemoryTokenStore()

    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, store):
        """store_token + get_email returns correct email."""
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await store.store_token("hash1", "alice@example.com", expires)
        assert await store.get_email("hash1") == "alice@example.com"

    @pytest.mark.asyncio
    async def test_get_email_unknown(self, store):
        """get_email returns None for unknown token hash."""
        assert await store.get_email("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_email_expired(self, store):
        """get_email returns None for expired token."""
        expired = datetime.now(timezone.utc) - timedelta(hours=1)
        await store.store_token("expired_hash", "bob@example.com", expired)
        assert await store.get_email("expired_hash") is None

    @pytest.mark.asyncio
    async def test_delete_token(self, store):
        """delete_token returns True and removes the token."""
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await store.store_token("del_hash", "alice@example.com", expires)
        assert await store.delete_token("del_hash") is True
        assert await store.get_email("del_hash") is None

    @pytest.mark.asyncio
    async def test_delete_token_unknown(self, store):
        """delete_token returns False for unknown hash."""
        assert await store.delete_token("noexist") is False

    @pytest.mark.asyncio
    async def test_delete_tokens_for_user(self, store):
        """delete_tokens_for_user deletes all tokens for email, returns count."""
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await store.store_token("h1", "alice@example.com", expires)
        await store.store_token("h2", "alice@example.com", expires)
        await store.store_token("h3", "bob@example.com", expires)
        count = await store.delete_tokens_for_user("alice@example.com")
        assert count == 2
        assert await store.get_email("h1") is None
        assert await store.get_email("h2") is None
        assert await store.get_email("h3") == "bob@example.com"

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, store):
        """cleanup_expired removes only expired tokens, returns count."""
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        await store.store_token("valid", "alice@example.com", future)
        await store.store_token("stale1", "bob@example.com", past)
        await store.store_token("stale2", "carol@example.com", past)
        count = await store.cleanup_expired()
        assert count == 2
        assert await store.get_email("valid") == "alice@example.com"


# ============================================================================
# GremlinTokenStore
# ============================================================================


class TestGremlinTokenStore:
    """Tests for the ArcadeDB-backed token store."""

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        db.execute_gremlin = AsyncMock()
        return db

    @pytest.fixture
    def store(self, mock_db):
        return GremlinTokenStore(mock_db)

    @pytest.mark.asyncio
    async def test_gremlin_store_and_retrieve(self, store, mock_db):
        """store_token sends addV; get_email returns email from elementMap."""
        mock_db.execute_gremlin.side_effect = [
            [],  # store_token result
            [
                {
                    "token_hash": "hash1",
                    "email": "alice@example.com",
                    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                }
            ],  # get_email result
        ]
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await store.store_token("hash1", "alice@example.com", expires)
        email = await store.get_email("hash1")
        assert email == "alice@example.com"
        # Verify addV was called
        first_call = mock_db.execute_gremlin.call_args_list[0][0][0]
        assert "addV('RefreshToken')" in first_call

    @pytest.mark.asyncio
    async def test_gremlin_get_email_not_found(self, store, mock_db):
        """get_email returns None when no vertex matches."""
        mock_db.execute_gremlin.return_value = []
        assert await store.get_email("unknown_hash") is None

    @pytest.mark.asyncio
    async def test_gremlin_delete_token(self, store, mock_db):
        """delete_token returns True and sends drop query."""
        mock_db.execute_gremlin.side_effect = [
            [1],  # count check
            [],  # drop result
        ]
        assert await store.delete_token("hash1") is True
        assert mock_db.execute_gremlin.call_count == 2
        drop_query = mock_db.execute_gremlin.call_args_list[1][0][0]
        assert "drop()" in drop_query

    @pytest.mark.asyncio
    async def test_gremlin_delete_token_not_found(self, store, mock_db):
        """delete_token returns False when count is 0."""
        mock_db.execute_gremlin.return_value = [0]
        assert await store.delete_token("nonexistent") is False

    @pytest.mark.asyncio
    async def test_gremlin_delete_tokens_for_user(self, store, mock_db):
        """delete_tokens_for_user sends count + drop queries."""
        mock_db.execute_gremlin.side_effect = [
            [3],  # count
            [],  # drop
        ]
        count = await store.delete_tokens_for_user("alice@example.com")
        assert count == 3
        assert mock_db.execute_gremlin.call_count == 2

    @pytest.mark.asyncio
    async def test_gremlin_cleanup_expired(self, store, mock_db):
        """cleanup_expired sends count + drop for expired tokens."""
        mock_db.execute_gremlin.side_effect = [
            [5],  # count
            [],  # drop
        ]
        count = await store.cleanup_expired()
        assert count == 5
        assert mock_db.execute_gremlin.call_count == 2
        count_query = mock_db.execute_gremlin.call_args_list[0][0][0]
        assert "lte(" in count_query
