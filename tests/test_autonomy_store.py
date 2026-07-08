"""Tests for the autonomy run/event stores (in-memory and ArcadeDB-backed)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from nomothetic.autonomy_store import (
    AutonomyEventItem,
    InMemoryAutonomyStore,
    SqlAutonomyStore,
)


def _event(event_type: str, recorded_at: str, data: dict | None = None) -> AutonomyEventItem:
    return AutonomyEventItem(event_type=event_type, data=data or {}, recorded_at=recorded_at)


# ============================================================================
# InMemoryAutonomyStore
# ============================================================================


class TestInMemoryAutonomyStore:
    """Tests for the dict-backed autonomy store."""

    @pytest.mark.asyncio
    async def test_first_event_creates_run(self):
        """The first event for a run_id creates the run record."""
        store = InMemoryAutonomyStore()
        await store.record_event(
            "N001", "explore", "run1", _event("running", "2026-01-01T00:00:00+00:00")
        )
        runs = await store.get_runs("N001")
        assert len(runs) == 1
        run = runs[0]
        assert run.run_id == "run1"
        assert run.routine == "explore"
        assert run.status == "running"
        assert run.started_at == "2026-01-01T00:00:00+00:00"
        assert run.ended_at is None
        assert run.event_count == 1

    @pytest.mark.asyncio
    async def test_log_event_does_not_change_status(self):
        """A free-form log event records without touching the run status."""
        store = InMemoryAutonomyStore()
        await store.record_event(
            "N001", "explore", "run1", _event("running", "2026-01-01T00:00:00+00:00")
        )
        await store.record_event(
            "N001", "explore", "run1", _event("log", "2026-01-01T00:00:01+00:00")
        )
        runs = await store.get_runs("N001")
        assert runs[0].status == "running"
        assert runs[0].event_count == 2
        assert runs[0].updated_at == "2026-01-01T00:00:01+00:00"

    @pytest.mark.asyncio
    async def test_stopping_sets_ended_at_and_status(self):
        """A stopping event marks the run stopped and stamps ended_at."""
        store = InMemoryAutonomyStore()
        await store.record_event(
            "N001", "explore", "run1", _event("running", "2026-01-01T00:00:00+00:00")
        )
        await store.record_event(
            "N001", "explore", "run1", _event("stopping", "2026-01-01T00:05:00+00:00")
        )
        run = (await store.get_runs("N001"))[0]
        assert run.status == "stopped"
        assert run.ended_at == "2026-01-01T00:05:00+00:00"

    @pytest.mark.asyncio
    async def test_error_sets_ended_at_and_status(self):
        """An error event marks the run errored and stamps ended_at."""
        store = InMemoryAutonomyStore()
        await store.record_event(
            "N001", "explore", "run1", _event("error", "2026-01-01T00:00:00+00:00")
        )
        run = (await store.get_runs("N001"))[0]
        assert run.status == "error"
        assert run.ended_at == "2026-01-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_get_runs_newest_started_first_with_limit(self):
        """Runs are ordered newest-started first and bounded by limit."""
        store = InMemoryAutonomyStore()
        await store.record_event(
            "N001", "explore", "run1", _event("running", "2026-01-01T00:00:00+00:00")
        )
        await store.record_event(
            "N001", "follow-user", "run2", _event("running", "2026-01-02T00:00:00+00:00")
        )
        await store.record_event(
            "N001", "explore", "run3", _event("running", "2026-01-03T00:00:00+00:00")
        )
        runs = await store.get_runs("N001", limit=2)
        assert [r.run_id for r in runs] == ["run3", "run2"]

    @pytest.mark.asyncio
    async def test_get_runs_since_filters(self):
        """A since bound excludes runs started before it."""
        store = InMemoryAutonomyStore()
        await store.record_event(
            "N001", "explore", "run1", _event("running", "2026-01-01T00:00:00+00:00")
        )
        await store.record_event(
            "N001", "explore", "run2", _event("running", "2026-01-03T00:00:00+00:00")
        )
        runs = await store.get_runs("N001", since="2026-01-02T00:00:00+00:00")
        assert [r.run_id for r in runs] == ["run2"]

    @pytest.mark.asyncio
    async def test_get_events_chronological(self):
        """Events come back oldest-first (log order)."""
        store = InMemoryAutonomyStore()
        await store.record_event(
            "N001", "explore", "run1", _event("running", "2026-01-01T00:00:00+00:00")
        )
        await store.record_event(
            "N001", "explore", "run1", _event("log", "2026-01-01T00:00:01+00:00", {"msg": "x"})
        )
        events = await store.get_events("N001", "run1")
        assert [e.event_type for e in events] == ["running", "log"]
        assert events[1].data == {"msg": "x"}

    @pytest.mark.asyncio
    async def test_unknown_vin_or_run_returns_empty(self):
        """Unknown devices and runs yield empty lists, not errors."""
        store = InMemoryAutonomyStore()
        assert await store.get_runs("NOPE") == []
        assert await store.get_events("NOPE", "run1") == []
        await store.record_event(
            "N001", "explore", "run1", _event("running", "2026-01-01T00:00:00+00:00")
        )
        assert await store.get_events("N001", "other-run") == []

    @pytest.mark.asyncio
    async def test_run_cap_drops_oldest_started(self):
        """Past the per-VIN cap, the oldest-started run is evicted."""
        store = InMemoryAutonomyStore(max_runs_per_vin=2)
        await store.record_event(
            "N001", "explore", "run1", _event("running", "2026-01-01T00:00:00+00:00")
        )
        await store.record_event(
            "N001", "explore", "run2", _event("running", "2026-01-02T00:00:00+00:00")
        )
        await store.record_event(
            "N001", "explore", "run3", _event("running", "2026-01-03T00:00:00+00:00")
        )
        runs = await store.get_runs("N001")
        assert {r.run_id for r in runs} == {"run2", "run3"}

    @pytest.mark.asyncio
    async def test_event_cap_drops_oldest_events(self):
        """Past the per-run cap, the oldest events are dropped."""
        store = InMemoryAutonomyStore(max_events_per_run=2)
        await store.record_event(
            "N001", "explore", "run1", _event("running", "2026-01-01T00:00:00+00:00")
        )
        await store.record_event(
            "N001", "explore", "run1", _event("log", "2026-01-01T00:00:01+00:00")
        )
        await store.record_event(
            "N001", "explore", "run1", _event("log", "2026-01-01T00:00:02+00:00")
        )
        events = await store.get_events("N001", "run1")
        assert [e.recorded_at for e in events] == [
            "2026-01-01T00:00:01+00:00",
            "2026-01-01T00:00:02+00:00",
        ]
        assert (await store.get_runs("N001"))[0].event_count == 2

    @pytest.mark.asyncio
    async def test_vins_are_isolated(self):
        """Runs are scoped per VIN."""
        store = InMemoryAutonomyStore()
        await store.record_event(
            "N001", "explore", "run1", _event("running", "2026-01-01T00:00:00+00:00")
        )
        await store.record_event(
            "N002", "explore", "run2", _event("running", "2026-01-01T00:00:00+00:00")
        )
        assert [r.run_id for r in await store.get_runs("N001")] == ["run1"]
        assert [r.run_id for r in await store.get_runs("N002")] == ["run2"]


# ============================================================================
# SqlAutonomyStore
# ============================================================================


class TestSqlAutonomyStore:
    """Tests for the ArcadeDB-backed autonomy store."""

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        db.execute_sql = AsyncMock()
        return db

    @pytest.fixture
    def store(self, mock_db):
        return SqlAutonomyStore(mock_db)

    @pytest.mark.asyncio
    async def test_record_event_new_run_creates_run_and_event(self, store, mock_db):
        """An unknown run_id inserts the run (PerformedBy) then the event (PartOf)."""
        mock_db.execute_sql.return_value = []
        await store.record_event(
            "N001", "explore", "run1", _event("running", "2026-01-01T00:00:00+00:00")
        )
        calls = [c[0] for c in mock_db.execute_sql.call_args_list]
        assert "SELECT run_id FROM AutonomyRun" in calls[0][0]
        assert "CREATE EDGE PerformedBy" in calls[1][0]
        assert "INSERT INTO AutonomyRun" in calls[1][0]
        assert calls[1][1]["status"] == "running"
        assert calls[1][1]["vin"] == "N001"
        assert calls[1][1]["routine"] == "explore"
        assert "CREATE EDGE PartOf" in calls[2][0]
        assert "INSERT INTO AutonomyEvent" in calls[2][0]
        assert calls[2][1]["event_type"] == "running"
        # Timestamps are formatted for the DATETIME columns (second precision).
        assert calls[1][1]["ts"] == "2026-01-01 00:00:00"
        assert calls[2][1]["recorded_at"] == "2026-01-01 00:00:00"

    @pytest.mark.asyncio
    async def test_record_event_existing_run_updates(self, store, mock_db):
        """A known run_id updates the run instead of inserting it."""
        mock_db.execute_sql.side_effect = [[{"run_id": "run1"}], [], []]
        await store.record_event(
            "N001", "explore", "run1", _event("stopping", "2026-01-01T00:05:00+00:00")
        )
        calls = [c[0] for c in mock_db.execute_sql.call_args_list]
        assert "UPDATE AutonomyRun SET" in calls[1][0]
        assert "status = :status" in calls[1][0]
        assert "ended_at = :ts" in calls[1][0]
        assert calls[1][1]["status"] == "stopped"
        assert "CREATE EDGE PartOf" in calls[2][0]

    @pytest.mark.asyncio
    async def test_record_log_event_existing_run_keeps_status(self, store, mock_db):
        """A log event updates only updated_at on an existing run."""
        mock_db.execute_sql.side_effect = [[{"run_id": "run1"}], [], []]
        await store.record_event(
            "N001", "explore", "run1", _event("log", "2026-01-01T00:00:01+00:00")
        )
        update_query, update_params = mock_db.execute_sql.call_args_list[1][0]
        assert "status" not in update_query
        assert "ended_at" not in update_query
        assert "updated_at = :ts" in update_query
        assert "status" not in update_params

    @pytest.mark.asyncio
    async def test_event_data_serialised_as_json(self, store, mock_db):
        """The event payload is stored verbatim as a JSON string."""
        mock_db.execute_sql.side_effect = [[{"run_id": "run1"}], [], []]
        await store.record_event(
            "N001",
            "explore",
            "run1",
            _event("log", "2026-01-01T00:00:01+00:00", {"message": "hello"}),
        )
        _, event_params = mock_db.execute_sql.call_args_list[2][0]
        assert event_params["data_json"] == '{"message": "hello"}'

    @pytest.mark.asyncio
    async def test_get_runs_scoped_and_ordered(self, store, mock_db):
        """get_runs filters by vin and orders newest-started first."""
        # DB returns timestamps in ArcadeDB DATETIME format (millisecond); the store
        # converts them back to ISO-8601 for callers.
        mock_db.execute_sql.return_value = [
            {
                "run_id": "run1",
                "routine": "explore",
                "status": "stopped",
                "started_at": "2026-01-01 00:00:00.000",
                "updated_at": "2026-01-01 00:05:00.000",
                "ended_at": "2026-01-01 00:05:00.000",
                "event_count": 3,
            }
        ]
        runs = await store.get_runs("N001", limit=10)
        assert len(runs) == 1
        assert runs[0].event_count == 3
        assert runs[0].started_at == "2026-01-01T00:00:00+00:00"
        assert runs[0].ended_at == "2026-01-01T00:05:00+00:00"
        query, params = mock_db.execute_sql.call_args[0]
        assert "WHERE vin = :vin" in query
        assert "ORDER BY started_at DESC" in query
        assert params == {"vin": "N001", "limit": 10}

    @pytest.mark.asyncio
    async def test_get_runs_since_adds_clause(self, store, mock_db):
        """A since bound adds a started_at filter and binds the param."""
        mock_db.execute_sql.return_value = []
        await store.get_runs("N001", since="2026-01-01T00:00:00+00:00")
        query, params = mock_db.execute_sql.call_args[0]
        assert "started_at >= :since" in query
        # The ISO bound is formatted to the DATETIME column's format.
        assert params["since"] == "2026-01-01 00:00:00"

    @pytest.mark.asyncio
    async def test_get_events_traverses_partof_chronologically(self, store, mock_db):
        """get_events traverses in('PartOf') scoped by run and vin, oldest first."""
        mock_db.execute_sql.return_value = [
            {
                "event_type": "log",
                "data_json": '{"message": "x"}',
                "recorded_at": "2026-01-01 00:00:01.234",  # ArcadeDB DATETIME format (millisecond)
            }
        ]
        events = await store.get_events("N001", "run1", limit=5)
        assert len(events) == 1
        assert events[0].data == {"message": "x"}
        assert events[0].recorded_at == "2026-01-01T00:00:01.234000+00:00"
        query, params = mock_db.execute_sql.call_args[0]
        assert "in('PartOf')" in query
        assert "run_id = :run_id AND vin = :vin" in query
        assert "ORDER BY recorded_at ASC" in query
        assert params["limit"] == 5

    @pytest.mark.asyncio
    async def test_get_events_bad_json_yields_empty_data(self, store, mock_db):
        """Undecodable data_json degrades to an empty dict, not an error."""
        mock_db.execute_sql.return_value = [
            {
                "event_type": "log",
                "data_json": "{not json",
                "recorded_at": "2026-01-01T00:00:01+00:00",
            }
        ]
        events = await store.get_events("N001", "run1")
        assert events[0].data == {}
