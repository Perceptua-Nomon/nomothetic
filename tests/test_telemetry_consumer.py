"""Tests for the central telemetry MQTT consumer and payload parsing."""

import pytest

from nomothetic.autonomy_store import InMemoryAutonomyStore
from nomothetic.telemetry_consumer import (
    TelemetryConsumer,
    autonomy_event_from_payload,
    reading_from_payload,
)
from nomothetic.telemetry_store import InMemoryTelemetryStore

# ============================================================================
# reading_from_payload (pure parse)
# ============================================================================


class TestReadingFromPayload:
    """Tests for the pure payload->reading mapping."""

    def test_maps_full_payload(self):
        """A full enriched payload maps device_id->vin and all metrics."""
        parsed = reading_from_payload(
            {
                "device_id": "N001",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "battery_voltage": 8.0,
                "cpu_temp_c": 45.5,
                "uptime_seconds": 1234,
            }
        )
        assert parsed is not None
        vin, item = parsed
        assert vin == "N001"
        assert item.battery_voltage == 8.0
        assert item.cpu_temp_c == 45.5
        assert item.uptime_seconds == 1234
        assert item.recorded_at == "2026-01-01T00:00:00+00:00"

    def test_missing_device_id_returns_none(self):
        """A payload without a usable device_id is skipped."""
        assert reading_from_payload({"battery_voltage": 8.0}) is None
        assert reading_from_payload({"device_id": "   "}) is None

    def test_missing_metrics_default_to_zero(self):
        """Absent numeric fields default to zero (schema NOTNULL)."""
        parsed = reading_from_payload({"device_id": "N001"})
        assert parsed is not None
        _, item = parsed
        assert item.battery_voltage == 0.0
        assert item.cpu_temp_c == 0.0
        assert item.uptime_seconds == 0

    def test_missing_timestamp_is_generated(self):
        """A missing timestamp is replaced with a generated ISO string."""
        parsed = reading_from_payload({"device_id": "N001"})
        assert parsed is not None
        _, item = parsed
        assert item.recorded_at  # non-empty ISO timestamp

    def test_non_numeric_metric_is_ignored(self):
        """A non-numeric metric falls back to the default rather than raising."""
        parsed = reading_from_payload({"device_id": "N001", "battery_voltage": "oops"})
        assert parsed is not None
        _, item = parsed
        assert item.battery_voltage == 0.0


# ============================================================================
# TelemetryConsumer.ingest (parse + persist)
# ============================================================================


class TestConsumerIngest:
    """Tests for the consumer's awaitable ingest path (no broker)."""

    def _consumer(self, store):
        # Construct without going through __init__'s paho requirement.
        consumer = TelemetryConsumer.__new__(TelemetryConsumer)
        consumer._store = store
        return consumer

    @pytest.mark.asyncio
    async def test_ingest_persists_reading(self):
        """A valid payload is parsed and stored under its VIN."""
        store = InMemoryTelemetryStore()
        consumer = self._consumer(store)
        stored = await consumer.ingest(
            {
                "device_id": "N001",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "battery_voltage": 8.0,
            }
        )
        assert stored is True
        latest = await store.get_latest("N001")
        assert latest is not None
        assert latest.battery_voltage == 8.0

    @pytest.mark.asyncio
    async def test_ingest_skips_unusable_payload(self):
        """An unusable payload is skipped and nothing is stored."""
        store = InMemoryTelemetryStore()
        consumer = self._consumer(store)
        stored = await consumer.ingest({"no_device": True})
        assert stored is False
        assert await store.get_latest("N001") is None


class TestConsumerFromEnv:
    """Tests for environment-driven construction."""

    def test_from_env_without_broker_returns_none(self, monkeypatch):
        """No NOMON_MQTT_BROKER -> no consumer (telemetry ingestion disabled)."""
        monkeypatch.delenv("NOMON_MQTT_BROKER", raising=False)
        assert TelemetryConsumer.from_env(InMemoryTelemetryStore()) is None


# ============================================================================
# autonomy_event_from_payload (pure parse)
# ============================================================================


class TestAutonomyEventFromPayload:
    """Tests for the pure autonomy payload->event mapping."""

    def test_maps_full_payload(self):
        """A full forwarded payload maps device_id->vin plus routine/run/event."""
        parsed = autonomy_event_from_payload(
            {
                "device_id": "N001",
                "routine": "explore",
                "run_id": "run1",
                "type": "running",
                "data": {"speed": 40},
                "timestamp": "2026-01-01T00:00:00+00:00",
            }
        )
        assert parsed is not None
        vin, routine, run_id, item = parsed
        assert vin == "N001"
        assert routine == "explore"
        assert run_id == "run1"
        assert item.event_type == "running"
        assert item.data == {"speed": 40}
        assert item.recorded_at == "2026-01-01T00:00:00+00:00"

    @pytest.mark.parametrize("missing", ["device_id", "routine", "run_id", "type"])
    def test_missing_required_field_returns_none(self, missing):
        """A payload missing any required attribution field is skipped."""
        payload = {
            "device_id": "N001",
            "routine": "explore",
            "run_id": "run1",
            "type": "running",
        }
        del payload[missing]
        assert autonomy_event_from_payload(payload) is None

    def test_non_dict_data_defaults_to_empty(self):
        """A non-dict data field falls back to an empty dict."""
        parsed = autonomy_event_from_payload(
            {"device_id": "N001", "routine": "explore", "run_id": "r", "type": "log", "data": "x"}
        )
        assert parsed is not None
        assert parsed[3].data == {}

    def test_missing_timestamp_is_generated(self):
        """A missing timestamp is replaced with a generated ISO string."""
        parsed = autonomy_event_from_payload(
            {"device_id": "N001", "routine": "explore", "run_id": "r", "type": "log"}
        )
        assert parsed is not None
        assert parsed[3].recorded_at


class TestConsumerIngestAutonomy:
    """Tests for the consumer's awaitable autonomy ingest path (no broker)."""

    def _consumer(self, autonomy_store):
        consumer = TelemetryConsumer.__new__(TelemetryConsumer)
        consumer._store = InMemoryTelemetryStore()
        consumer._autonomy_store = autonomy_store
        return consumer

    @pytest.mark.asyncio
    async def test_ingest_autonomy_persists_event(self):
        """A valid autonomy payload is parsed and persisted under its VIN."""
        store = InMemoryAutonomyStore()
        consumer = self._consumer(store)
        stored = await consumer.ingest_autonomy(
            {
                "device_id": "N001",
                "routine": "explore",
                "run_id": "run1",
                "type": "running",
                "timestamp": "2026-01-01T00:00:00+00:00",
            }
        )
        assert stored is True
        runs = await store.get_runs("N001")
        assert len(runs) == 1
        assert runs[0].run_id == "run1"

    @pytest.mark.asyncio
    async def test_ingest_autonomy_without_store_is_noop(self):
        """No autonomy store configured -> ingest is a skip, not an error."""
        consumer = self._consumer(None)
        stored = await consumer.ingest_autonomy(
            {"device_id": "N001", "routine": "explore", "run_id": "r", "type": "running"}
        )
        assert stored is False

    @pytest.mark.asyncio
    async def test_ingest_autonomy_skips_unusable(self):
        """An unattributable payload is skipped and nothing is stored."""
        store = InMemoryAutonomyStore()
        consumer = self._consumer(store)
        stored = await consumer.ingest_autonomy({"device_id": "N001", "type": "running"})
        assert stored is False
        assert await store.get_runs("N001") == []

    def test_ingest_routing_selects_autonomy_topic(self):
        """The topic router picks the autonomy coroutine for the autonomy topic."""
        store = InMemoryAutonomyStore()
        consumer = self._consumer(store)
        consumer.topic = "nomon/telemetry"
        consumer.autonomy_topic = "nomon/autonomy"
        payload = {"device_id": "N001", "routine": "explore", "run_id": "r", "type": "running"}
        coro = consumer._ingest_for_topic("nomon/autonomy", payload)
        try:
            assert coro.__name__ == "ingest_autonomy"
        finally:
            coro.close()
        telem_coro = consumer._ingest_for_topic("nomon/telemetry", {"device_id": "N001"})
        try:
            assert telem_coro.__name__ == "ingest"
        finally:
            telem_coro.close()
