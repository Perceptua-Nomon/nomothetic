"""Tests for telemetry store implementations."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from nomothetic.telemetry_store import (
    InMemoryTelemetryStore,
    SqlTelemetryStore,
    TelemetryReadingItem,
)


def _item(recorded_at: str, battery: float = 8.0) -> TelemetryReadingItem:
    return TelemetryReadingItem(
        battery_voltage=battery,
        cpu_temp_c=45.0,
        uptime_seconds=100,
        recorded_at=recorded_at,
    )


# ============================================================================
# InMemoryTelemetryStore
# ============================================================================


class TestInMemoryTelemetryStore:
    """Tests for the in-memory telemetry store."""

    @pytest.fixture
    def store(self):
        return InMemoryTelemetryStore()

    @pytest.mark.asyncio
    async def test_record_and_get_latest(self, store):
        """get_latest returns the most recently recorded reading."""
        await store.record_reading("N001", _item("2026-01-01T00:00:00+00:00", battery=8.1))
        await store.record_reading("N001", _item("2026-01-01T00:01:00+00:00", battery=8.0))
        latest = await store.get_latest("N001")
        assert latest is not None
        assert latest.recorded_at == "2026-01-01T00:01:00+00:00"
        assert latest.battery_voltage == 8.0

    @pytest.mark.asyncio
    async def test_get_latest_empty(self, store):
        """get_latest returns None for an unknown device."""
        assert await store.get_latest("NOEXIST") is None

    @pytest.mark.asyncio
    async def test_get_history_newest_first(self, store):
        """History is ordered newest-first regardless of insertion order."""
        await store.record_reading("N001", _item("2026-01-01T00:02:00+00:00"))
        await store.record_reading("N001", _item("2026-01-01T00:00:00+00:00"))
        await store.record_reading("N001", _item("2026-01-01T00:01:00+00:00"))
        history = await store.get_history("N001")
        recorded = [r.recorded_at for r in history]
        assert recorded == [
            "2026-01-01T00:02:00+00:00",
            "2026-01-01T00:01:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ]

    @pytest.mark.asyncio
    async def test_get_history_limit(self, store):
        """limit caps the number of readings returned."""
        for minute in range(5):
            await store.record_reading("N001", _item(f"2026-01-01T00:0{minute}:00+00:00"))
        history = await store.get_history("N001", limit=2)
        assert len(history) == 2
        assert history[0].recorded_at == "2026-01-01T00:04:00+00:00"

    @pytest.mark.asyncio
    async def test_get_history_since(self, store):
        """since filters out readings before the bound (inclusive)."""
        await store.record_reading("N001", _item("2026-01-01T00:00:00+00:00"))
        await store.record_reading("N001", _item("2026-01-01T00:05:00+00:00"))
        history = await store.get_history("N001", since="2026-01-01T00:03:00+00:00")
        assert len(history) == 1
        assert history[0].recorded_at == "2026-01-01T00:05:00+00:00"

    @pytest.mark.asyncio
    async def test_history_scoped_by_vin(self, store):
        """Readings are isolated per device."""
        await store.record_reading("N001", _item("2026-01-01T00:00:00+00:00"))
        await store.record_reading("N002", _item("2026-01-01T00:00:00+00:00"))
        assert len(await store.get_history("N001")) == 1
        assert len(await store.get_history("N002")) == 1

    @pytest.mark.asyncio
    async def test_bounded_retention(self):
        """Oldest readings are dropped past the per-VIN cap."""
        store = InMemoryTelemetryStore(max_per_vin=3)
        for minute in range(5):
            await store.record_reading("N001", _item(f"2026-01-01T00:0{minute}:00+00:00"))
        history = await store.get_history("N001", limit=100)
        assert len(history) == 3
        # Newest three retained.
        assert history[0].recorded_at == "2026-01-01T00:04:00+00:00"
        assert history[-1].recorded_at == "2026-01-01T00:02:00+00:00"


# ============================================================================
# SqlTelemetryStore
# ============================================================================


class TestSqlTelemetryStore:
    """Tests for the ArcadeDB-backed telemetry store."""

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        db.execute_sql = AsyncMock()
        return db

    @pytest.fixture
    def store(self, mock_db):
        return SqlTelemetryStore(mock_db)

    @pytest.mark.asyncio
    async def test_record_reading_creates_edge(self, store, mock_db):
        """record_reading inserts a TelemetryReading and a ReadFrom edge."""
        mock_db.execute_sql.return_value = []
        await store.record_reading("N001", _item("2026-01-01T00:00:00+00:00"))
        query, params = mock_db.execute_sql.call_args[0]
        assert "CREATE EDGE ReadFrom" in query
        assert "INSERT INTO TelemetryReading" in query
        assert params["vin"] == "N001"
        assert params["battery_voltage"] == 8.0
        # recorded_at is formatted for the DATETIME column (second precision).
        assert params["recorded_at"] == "2026-01-01 00:00:00"

    @pytest.mark.asyncio
    async def test_get_history_traverses_readfrom(self, store, mock_db):
        """get_history traverses Vehicle.in('ReadFrom') ordered newest-first."""
        mock_db.execute_sql.return_value = [
            {
                "battery_voltage": 8.0,
                "cpu_temp_c": 45.0,
                "uptime_seconds": 100,
                "recorded_at": "2026-01-01T00:00:00+00:00",
            }
        ]
        history = await store.get_history("N001", limit=10)
        assert len(history) == 1
        assert history[0].battery_voltage == 8.0
        query, params = mock_db.execute_sql.call_args[0]
        assert "in('ReadFrom')" in query
        assert "ORDER BY recorded_at DESC" in query
        assert params["limit"] == 10

    @pytest.mark.asyncio
    async def test_get_history_converts_db_datetime_to_iso(self, store, mock_db):
        """An ArcadeDB-format recorded_at is returned to callers as ISO-8601."""
        mock_db.execute_sql.return_value = [
            {
                "battery_voltage": 8.0,
                "cpu_temp_c": 45.0,
                "uptime_seconds": 100,
                "recorded_at": "2026-01-01 00:00:00.789",  # ArcadeDB DATETIME format (millisecond)
            }
        ]
        history = await store.get_history("N001", limit=10)
        assert history[0].recorded_at == "2026-01-01T00:00:00.789000+00:00"

    @pytest.mark.asyncio
    async def test_get_history_since_adds_clause(self, store, mock_db):
        """A since bound adds a recorded_at filter and binds the param."""
        mock_db.execute_sql.return_value = []
        await store.get_history("N001", since="2026-01-01T00:00:00+00:00")
        query, params = mock_db.execute_sql.call_args[0]
        assert "recorded_at >= :since" in query
        # The ISO bound is formatted to the DATETIME column's format.
        assert params["since"] == "2026-01-01 00:00:00"

    @pytest.mark.asyncio
    async def test_get_latest_returns_first(self, store, mock_db):
        """get_latest returns the single newest reading."""
        mock_db.execute_sql.return_value = [
            {
                "battery_voltage": 7.9,
                "cpu_temp_c": 44.0,
                "uptime_seconds": 200,
                "recorded_at": "2026-01-01T00:01:00+00:00",
            }
        ]
        latest = await store.get_latest("N001")
        assert latest is not None
        assert latest.uptime_seconds == 200
        assert mock_db.execute_sql.call_args[0][1]["limit"] == 1
