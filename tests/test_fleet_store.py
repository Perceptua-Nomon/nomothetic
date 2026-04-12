"""Tests for fleet store implementations."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from nomothetic.fleet_store import (
    GremlinFleetStore,
    InMemoryFleetStore,
    _sanitize_gremlin_value,
)

# ============================================================================
# InMemoryFleetStore
# ============================================================================


class TestInMemoryFleetStore:
    """Tests for the in-memory fleet store."""

    @pytest.fixture
    def store(self):
        return InMemoryFleetStore()

    @pytest.mark.asyncio
    async def test_register_device(self, store):
        """Registering a device returns a DeviceItem."""
        item = await store.register_device("alice@example.com", "NOMON001", "explorer-v1")
        assert item.vin == "NOMON001"
        assert item.model == "explorer-v1"
        assert item.role == "owner"
        assert item.registered_at is not None

    @pytest.mark.asyncio
    async def test_get_devices(self, store):
        """get_devices returns all devices for the owner."""
        await store.register_device("alice@example.com", "N001", "explorer-v1")
        await store.register_device("alice@example.com", "N002", "scout-v2")
        devices = await store.get_devices("alice@example.com")
        assert len(devices) == 2
        vins = {d.vin for d in devices}
        assert vins == {"N001", "N002"}

    @pytest.mark.asyncio
    async def test_get_devices_empty(self, store):
        """get_devices returns empty list for unknown owner."""
        assert await store.get_devices("nobody@example.com") == []

    @pytest.mark.asyncio
    async def test_get_device(self, store):
        """get_device returns a single device by VIN."""
        await store.register_device("alice@example.com", "N001", "explorer-v1")
        item = await store.get_device("alice@example.com", "N001")
        assert item is not None
        assert item.vin == "N001"

    @pytest.mark.asyncio
    async def test_get_device_not_found(self, store):
        """get_device returns None for unknown VIN."""
        assert await store.get_device("alice@example.com", "NOEXIST") is None

    @pytest.mark.asyncio
    async def test_remove_device(self, store):
        """remove_device returns True and removes the device."""
        await store.register_device("alice@example.com", "N001", "explorer-v1")
        assert await store.remove_device("alice@example.com", "N001") is True
        assert await store.get_device("alice@example.com", "N001") is None

    @pytest.mark.asyncio
    async def test_remove_device_not_found(self, store):
        """remove_device returns False for unknown VIN."""
        assert await store.remove_device("alice@example.com", "NOEXIST") is False

    @pytest.mark.asyncio
    async def test_device_exists(self, store):
        """device_exists returns True after registration."""
        await store.register_device("alice@example.com", "N001", "explorer-v1")
        assert await store.device_exists("N001") is True
        assert await store.device_exists("NOEXIST") is False

    @pytest.mark.asyncio
    async def test_register_duplicate_raises(self, store):
        """Registering a duplicate VIN for the same owner raises ValueError."""
        await store.register_device("alice@example.com", "DUP001", "explorer-v1")
        with pytest.raises(ValueError, match="already registered"):
            await store.register_device("alice@example.com", "DUP001", "scout-v2")


# ============================================================================
# Sanitization
# ============================================================================


def test_sanitize_clean_vin():
    """Clean VINs pass through unchanged."""
    assert _sanitize_gremlin_value("NOMON-001_alpha") == "NOMON-001_alpha"


def test_sanitize_quote_rejected():
    """Values with single quotes are rejected."""
    with pytest.raises(ValueError, match="Unsafe"):
        _sanitize_gremlin_value("VIN'inject")


def test_sanitize_rejects_null_bytes():
    """Values with null bytes are rejected."""
    with pytest.raises(ValueError, match="Control characters"):
        _sanitize_gremlin_value("hello\x00world")


def test_sanitize_rejects_control_chars():
    """Values with control characters are rejected."""
    with pytest.raises(ValueError, match="Control characters"):
        _sanitize_gremlin_value("hello\x01world")


# ============================================================================
# GremlinFleetStore
# ============================================================================


class TestGremlinFleetStore:
    """Tests for the ArcadeDB-backed fleet store."""

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        db.execute_gremlin = AsyncMock()
        return db

    @pytest.fixture
    def store(self, mock_db):
        return GremlinFleetStore(mock_db)

    @pytest.mark.asyncio
    async def test_get_devices(self, store, mock_db):
        """get_devices returns DeviceItems from Gremlin results."""
        mock_db.execute_gremlin.return_value = [
            {
                "e": {"registered_at": "2026-01-01T00:00:00+00:00", "role": "owner"},
                "v": {"vin": "N001", "model": "explorer-v1"},
            }
        ]
        devices = await store.get_devices("alice@example.com")
        assert len(devices) == 1
        assert devices[0].vin == "N001"
        query = mock_db.execute_gremlin.call_args[0][0]
        assert "hasLabel('User')" in query
        assert "OwnsDevice" in query

    @pytest.mark.asyncio
    async def test_get_devices_empty(self, store, mock_db):
        """get_devices returns empty list when no edges exist."""
        mock_db.execute_gremlin.return_value = []
        assert await store.get_devices("nobody@example.com") == []

    @pytest.mark.asyncio
    async def test_get_device_found(self, store, mock_db):
        """get_device returns a DeviceItem when the edge exists."""
        mock_db.execute_gremlin.return_value = [
            {
                "e": {"registered_at": "2026-01-01T00:00:00+00:00", "role": "owner"},
                "v": {"vin": "N001", "model": "explorer-v1"},
            }
        ]
        item = await store.get_device("alice@example.com", "N001")
        assert item is not None
        assert item.vin == "N001"

    @pytest.mark.asyncio
    async def test_get_device_not_found(self, store, mock_db):
        """get_device returns None when no matching edge exists."""
        mock_db.execute_gremlin.return_value = []
        assert await store.get_device("alice@example.com", "NOEXIST") is None

    @pytest.mark.asyncio
    async def test_register_device(self, store, mock_db):
        """register_device creates Vehicle vertex and OwnsDevice edge."""
        # get_device check (duplicate) → not found, then vehicle upsert, then edge create
        mock_db.execute_gremlin.side_effect = [
            [],  # get_device → not found
            [{"vin": "N001"}],  # create/get vehicle
            [],  # create edge
        ]
        item = await store.register_device("alice@example.com", "N001", "explorer-v1")
        assert item.vin == "N001"
        assert item.role == "owner"
        assert mock_db.execute_gremlin.call_count == 3

    @pytest.mark.asyncio
    async def test_register_device_duplicate(self, store, mock_db):
        """register_device raises ValueError when device already owned."""
        mock_db.execute_gremlin.return_value = [
            {
                "e": {"registered_at": "2026-01-01T00:00:00+00:00", "role": "owner"},
                "v": {"vin": "DUP001", "model": "explorer-v1"},
            }
        ]
        with pytest.raises(ValueError, match="already registered"):
            await store.register_device("alice@example.com", "DUP001", "scout-v2")

    @pytest.mark.asyncio
    async def test_remove_device(self, store, mock_db):
        """remove_device drops the OwnsDevice edge."""
        # First call: get_device (exists check)
        # Second call: drop edge
        mock_db.execute_gremlin.side_effect = [
            [
                {
                    "e": {"registered_at": "2026-01-01T00:00:00+00:00", "role": "owner"},
                    "v": {"vin": "N001", "model": "explorer-v1"},
                }
            ],
            [],  # drop result
        ]
        assert await store.remove_device("alice@example.com", "N001") is True

    @pytest.mark.asyncio
    async def test_remove_device_not_found(self, store, mock_db):
        """remove_device returns False when edge doesn't exist."""
        mock_db.execute_gremlin.return_value = []
        assert await store.remove_device("alice@example.com", "NOEXIST") is False

    @pytest.mark.asyncio
    async def test_device_exists_true(self, store, mock_db):
        """device_exists returns True when count > 0."""
        mock_db.execute_gremlin.return_value = [1]
        assert await store.device_exists("N001") is True

    @pytest.mark.asyncio
    async def test_device_exists_false(self, store, mock_db):
        """device_exists returns False when count is 0."""
        mock_db.execute_gremlin.return_value = [0]
        assert await store.device_exists("NOEXIST") is False

    @pytest.mark.asyncio
    async def test_register_unsafe_vin_rejected(self, store, mock_db):
        """register_device rejects VINs with unsafe characters."""
        with pytest.raises(ValueError, match="Unsafe"):
            await store.register_device("alice@example.com", "VIN'bad", "model")
