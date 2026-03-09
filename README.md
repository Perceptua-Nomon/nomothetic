# nomothetic

Comms for the `nomon` fleet.

This Python package provides peripheral control, HTTPS REST API, and MQTT telemetry for a fleet of Raspberry Pi devices.

---

## Modules

| Module | Class | Description |
|---|---|---|
| `nomothetic.camera` | `Camera` | picamera2 wrapper — still capture, video recording, MJPEG frames |
| `nomothetic.streaming` | `StreamServer` | Flask MJPEG stream server for local LAN viewing |
| `nomothetic.api` | `APIServer` | FastAPI HTTPS REST server — primary remote control interface |
| `nomothetic.telemetry` | `TelemetryPublisher` | paho-mqtt background telemetry publisher |
| *(planned)* | *(planned)* | HAT IPC client for the future `nomopractic` Rust daemon *(Phase 5; Python client not yet implemented, module not available in current release)* |

See [docs/architecture.md](docs/architecture.md) for a full system diagram and module responsibilities.

---

## Installation

`nomothetic` uses optional dependency groups — install only what you need:

```bash
# Camera & SPI hardware (Raspberry Pi OS only)
pip install "nomothetic[pi]"

# HTTPS REST API (most common)
pip install "nomothetic[api]"

# MJPEG stream server (local LAN)
pip install "nomothetic[web]"

# MQTT telemetry
pip install "nomothetic[telemetry]"

# All runtime extras
pip install "nomothetic[pi,api,web,telemetry]"
```

> **Note:** `picamera2` and `spidev` are only installable on Raspberry Pi OS. Install the `[pi]` extra (`pip install "nomothetic[pi]"`) on the Pi. The package remains importable without them on other platforms for development and testing.

---

## Quick Start

### REST API

```python
from nomothetic.api import APIServer

server = APIServer(host="0.0.0.0", port=8443, use_ssl=True)
server.run()  # HTTPS on :8443; self-signed cert auto-generated in .certs/
```

See [examples/api_server.py](examples/api_server.py) for a fuller example and [docs/architecture.md](docs/architecture.md) for the full endpoint reference.

### MJPEG Stream (local LAN)

```python
from nomothetic.streaming import StreamServer

stream = StreamServer(host="0.0.0.0", port=8000)
stream.start()  # http://<pi-ip>:8000/stream
```

### MQTT Telemetry

```python
from nomothetic.telemetry import TelemetryPublisher

pub = TelemetryPublisher(broker="mqtt.example.com", topic="nomon/telemetry")
pub.start_background()  # daemon thread; publishes a JSON payload every 30 s by default
```

Configured via `NOMON_MQTT_*` environment variables. See [docs/phase3_completion.md](docs/phase3_completion.md) for the full variable reference.

---

## Development

```bash
make install-dev   # pip install -e ".[dev,web,api]"
make test          # pytest with coverage
make lint          # ruff check
make format        # black .
make type-check    # mypy src/
```

Tests pass on Windows/macOS — hardware is fully mocked. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## Roadmap

See [docs/roadmap.md](docs/roadmap.md) for phase status and planned work.
