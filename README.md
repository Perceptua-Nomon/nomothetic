# nomothetic

Comms for the `nomon` fleet.

This Python package provides peripheral control, HTTPS REST API, MQTT telemetry, and fleet management for a fleet of Raspberry Pi devices. Supports two deployment modes:

- **Device mode** (default): hardware control endpoints running on each Pi
- **Central mode**: authentication and fleet management endpoints for a centrally-hosted server

---

## Modules

| Module | Class | Description |
|---|---|---|
| `nomothetic.camera` | `Camera` | picamera2 wrapper — still capture, video recording, MJPEG frames |
| `nomothetic.streaming` | `StreamServer` | Flask MJPEG stream server for local LAN viewing |
| `nomothetic.api` | `APIServer` | FastAPI HTTPS REST server — mode-aware route registration |
| `nomothetic.telemetry` | `TelemetryPublisher` | paho-mqtt background telemetry publisher |
| `nomothetic.hat` | `HatClient` | IPC client for the nomopractic Rust daemon |
| `nomothetic.audio` | `AudioPlayer`, `AudioRecorder` | ALSA audio recording and playback |
| `nomothetic.mode` | `Mode` | Device/central mode selection from `NOMON_API_MODE` env var |
| `nomothetic.auth` | `AuthService` | JWT authentication (central and device modes) |
| `nomothetic.auth_routes` | — | `/api/auth/*` endpoints (register, login, refresh, logout, profile) |
| `nomothetic.device_auth_routes` | — | `/api/device/auth/*` endpoints (pairing, refresh, profile) |
| `nomothetic.pairing` | `PairingState` | Device pairing secret lifecycle (generate, verify, consume) |
| `nomothetic.fleet_routes` | — | `/api/fleet/*` endpoints (device CRUD) |
| `nomothetic.rate_limit` | `RateLimiter` | Sliding-window rate limiting for auth and pairing endpoints |
| `nomothetic.db` | `DatabaseClient` | ArcadeDB HTTP API client with Gremlin query support |
| `nomothetic.user_store` | `UserStore` | User persistence (in-memory + Gremlin backends) |
| `nomothetic.fleet_store` | `FleetStore` | Fleet device persistence (in-memory + Gremlin backends) |
| `nomothetic.token_store` | `TokenStore` | Refresh token persistence (in-memory + Gremlin backends) |
| `nomothetic.gremlin_utils` | — | Shared Gremlin value sanitiser |
| `nomothetic.db_utils` | — | Shared database query utilities |

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

# JWT authentication (central and device modes)
pip install "nomothetic[auth]"

# Central-mode fleet server (httpx for device health checks)
pip install "nomothetic[central]"

# All runtime extras
pip install "nomothetic[pi,api,web,telemetry,auth,central]"
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
