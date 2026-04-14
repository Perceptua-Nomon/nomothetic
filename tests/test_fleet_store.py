"""Tests for fleet store implementations."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from nomothetic.fleet_store import (
    InMemoryFleetStore,
    SqlFleetStore,
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
# SqlFleetStore
# ============================================================================


class TestSqlFleetStore:
    """Tests for the ArcadeDB-backed fleet store."""

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        db.execute_sql = AsyncMock()
        return db

    @pytest.fixture
    def store(self, mock_db):
        return SqlFleetStore(mock_db)

    @pytest.mark.asyncio
    async def test_get_devices(self, store, mock_db):
        """get_devices returns DeviceItems from SQL results."""
        mock_db.execute_sql.return_value = [
            {
                "vin": "N001",
                "model": "explorer-v1",
                "registered_at": "2026-01-01T00:00:00+00:00",
                "role": "owner",
            }
        ]
        devices = await store.get_devices("alice@example.com")
        assert len(devices) == 1
        assert devices[0].vin == "N001"
        query = mock_db.execute_sql.call_args[0][0]
        assert "FROM OwnsDevice" in query

    @pytest.mark.asyncio
    async def test_get_devices_empty(self, store, mock_db):
        """get_devices returns empty list when no edges exist."""
        mock_db.execute_sql.return_value = []
        assert await store.get_devices("nobody@example.com") == []

    @pytest.mark.asyncio
    async def test_get_device_found(self, store, mock_db):
        """get_device returns a DeviceItem when the edge exists."""
        mock_db.execute_sql.return_value = [
            {
                "vin": "N001",
                "model": "explorer-v1",
                "registered_at": "2026-01-01T00:00:00+00:00",
                "role": "owner",
            }
        ]
        item = await store.get_device("alice@example.com", "N001")
        assert item is not None
        assert item.vin == "N001"

    @pytest.mark.asyncio
    async def test_get_device_not_found(self, store, mock_db):
        """get_device returns None when no matching edge exists."""
        mock_db.execute_sql.return_value = []
        assert await store.get_device("alice@example.com", "NOEXIST") is None

    @pytest.mark.asyncio
    async def test_register_device(self, store, mock_db):
        """register_device creates Vehicle record and OwnsDevice edge."""
        # get_device check → not found, vehicle count → 0, INSERT vehicle, CREATE EDGE
        mock_db.execute_sql.side_effect = [
            [],  # get_device → not found
            [{"count": 0}],  # vehicle count
            [],  # INSERT INTO Vehicle
            [],  # CREATE EDGE
        ]
        item = await store.register_device("alice@example.com", "N001", "explorer-v1")
        assert item.vin == "N001"
        assert item.role == "owner"
        assert mock_db.execute_sql.call_count == 4
        insert_query = mock_db.execute_sql.call_args_list[2][0][0]
        assert "INSERT INTO Vehicle" in insert_query
        edge_query = mock_db.execute_sql.call_args_list[3][0][0]
        assert "CREATE EDGE" in edge_query

    @pytest.mark.asyncio
    async def test_register_device_existing_vehicle(self, store, mock_db):
        """register_device skips vehicle INSERT when vehicle already exists."""
        mock_db.execute_sql.side_effect = [
            [],  # get_device → not found
            [{"count": 1}],  # vehicle already exists
            [],  # CREATE EDGE
        ]
        item = await store.register_device("alice@example.com", "N001", "explorer-v1")
        assert item.vin == "N001"
        assert mock_db.execute_sql.call_count == 3

    @pytest.mark.asyncio
    async def test_register_device_duplicate(self, store, mock_db):
        """register_device raises ValueError when device already owned."""
        mock_db.execute_sql.return_value = [
            {
                "vin": "DUP001",
                "model": "explorer-v1",
                "registered_at": "2026-01-01T00:00:00+00:00",
                "role": "owner",
            }
        ]
        with pytest.raises(ValueError, match="already registered"):
            await store.register_device("alice@example.com", "DUP001", "scout-v2")

    @pytest.mark.asyncio
    async def test_remove_device(self, store, mock_db):
        """remove_device deletes the OwnsDevice edge."""
        mock_db.execute_sql.side_effect = [
            [
                {
                    "vin": "N001",
                    "model": "explorer-v1",
                    "registered_at": "2026-01-01T00:00:00+00:00",
                    "role": "owner",
                }
            ],  # get_device exists
            [],  # DELETE EDGE result
        ]
        assert await store.remove_device("alice@example.com", "N001") is True
        delete_query = mock_db.execute_sql.call_args_list[1][0][0]
        assert "DELETE EDGE" in delete_query

    @pytest.mark.asyncio
    async def test_remove_device_not_found(self, store, mock_db):
        """remove_device returns False when edge doesn't exist."""
        mock_db.execute_sql.return_value = []
        assert await store.remove_device("alice@example.com", "NOEXIST") is False

    @pytest.mark.asyncio
    async def test_device_exists_true(self, store, mock_db):
        """device_exists returns True when count > 0."""
        mock_db.execute_sql.return_value = [{"count": 1}]
        assert await store.device_exists("N001") is True

    @pytest.mark.asyncio
    async def test_device_exists_false(self, store, mock_db):
        """device_exists returns False when count is 0."""
        mock_db.execute_sql.return_value = [{"count": 0}]
        assert await store.device_exists("NOEXIST") is False
