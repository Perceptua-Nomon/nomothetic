"""Tests for the device-side autonomy event MQTT forwarder."""

from typing import Any
from unittest.mock import MagicMock

from nomothetic.autonomy_forwarder import AutonomyEventForwarder
from nomothetic.routine_log_store import RoutineEvent, RoutineLogStore


def _forwarder(**kwargs: Any) -> AutonomyEventForwarder:
    defaults: dict[str, Any] = {"broker": "broker.local", "device_id": "N001"}
    defaults.update(kwargs)
    return AutonomyEventForwarder(**defaults)


def _routine_event(**kwargs: Any) -> RoutineEvent:
    defaults: dict[str, Any] = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "type": "running",
        "data": {"routine": "explore"},
        "run_id": "run1",
        "device_id": "N001",
    }
    defaults.update(kwargs)
    return RoutineEvent(**defaults)


# ============================================================================
# Construction
# ============================================================================


class TestFromEnv:
    """Tests for environment-driven construction."""

    def test_from_env_without_broker_returns_none(self, monkeypatch):
        """No NOMON_MQTT_BROKER -> no forwarder (history stays device-local)."""
        monkeypatch.delenv("NOMON_MQTT_BROKER", raising=False)
        assert AutonomyEventForwarder.from_env() is None

    def test_from_env_reads_broker_and_topic(self, monkeypatch):
        """Broker, port, and autonomy topic come from the environment."""
        monkeypatch.setenv("NOMON_MQTT_BROKER", "broker.local")
        monkeypatch.setenv("NOMON_MQTT_PORT", "2883")
        monkeypatch.setenv("NOMON_MQTT_AUTONOMY_TOPIC", "fleet/autonomy")
        monkeypatch.setenv("NOMON_DEVICE_ID", "N009")
        forwarder = AutonomyEventForwarder.from_env()
        assert forwarder is not None
        assert forwarder.broker == "broker.local"
        assert forwarder.port == 2883
        assert forwarder.topic == "fleet/autonomy"
        assert forwarder.device_id == "N009"

    def test_default_topic(self):
        """The default autonomy topic is nomon/autonomy."""
        assert _forwarder().topic == "nomon/autonomy"


# ============================================================================
# Enqueue
# ============================================================================


class TestEnqueue:
    """Tests for the RoutineLogStore on_event-compatible enqueue callback."""

    def test_enqueue_builds_payload(self):
        """The queued payload carries device, routine, run, type, data, time."""
        forwarder = _forwarder()
        assert forwarder.enqueue_event("explore", _routine_event()) is True
        payload = forwarder._queue.get_nowait()
        assert payload == {
            "device_id": "N001",
            "routine": "explore",
            "run_id": "run1",
            "type": "running",
            "data": {"routine": "explore"},
            "timestamp": "2026-01-01T00:00:00+00:00",
        }

    def test_enqueue_falls_back_to_forwarder_device_id(self):
        """An event without a device_id is stamped with the forwarder's."""
        forwarder = _forwarder(device_id="FALLBACK")
        forwarder.enqueue_event("explore", _routine_event(device_id=None))
        payload = forwarder._queue.get_nowait()
        assert payload["device_id"] == "FALLBACK"

    def test_enqueue_drops_when_queue_full(self):
        """A full buffer drops the event and reports False (never raises)."""
        forwarder = _forwarder(max_queue=1)
        assert forwarder.enqueue_event("explore", _routine_event()) is True
        assert forwarder.enqueue_event("explore", _routine_event()) is False
        assert forwarder._queue.qsize() == 1

    def test_log_store_observer_integration(self):
        """RoutineLogStore(on_event=...) feeds recorded events into the queue."""
        forwarder = _forwarder()
        store = RoutineLogStore(on_event=forwarder.enqueue_event)
        store.record("explore", "starting", data={}, run_id="run9", device_id="N001")
        payload = forwarder._queue.get_nowait()
        assert payload["routine"] == "explore"
        assert payload["type"] == "starting"
        assert payload["run_id"] == "run9"


# ============================================================================
# Publish
# ============================================================================


class TestPublish:
    """Tests for the lazy-connect publish helper (mocked client)."""

    def test_publish_connects_lazily_and_publishes(self):
        """First publish connects the client, then publishes with QoS."""
        forwarder = _forwarder()
        client = MagicMock()
        result = MagicMock()
        result.is_published.return_value = True
        client.publish.return_value = result
        forwarder._client = client

        assert forwarder._publish({"type": "running"}) is True
        client.connect.assert_called_once_with("broker.local", 1883, keepalive=60)
        topic, payload = client.publish.call_args[0]
        assert topic == "nomon/autonomy"
        assert '"type": "running"' in payload
        assert client.publish.call_args[1]["qos"] == 1

    def test_publish_failure_marks_disconnected(self):
        """A connect/publish error returns False and resets the connection."""
        forwarder = _forwarder()
        client = MagicMock()
        client.connect.side_effect = OSError("no broker")
        forwarder._client = client

        assert forwarder._publish({"type": "running"}) is False
        assert forwarder._connected is False

    def test_publish_unacknowledged_returns_false(self):
        """A publish that never acks within the timeout is a failure."""
        forwarder = _forwarder()
        client = MagicMock()
        result = MagicMock()
        result.is_published.return_value = False
        client.publish.return_value = result
        forwarder._client = client

        assert forwarder._publish({"type": "running"}) is False
