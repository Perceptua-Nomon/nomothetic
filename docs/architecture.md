# nomon — Architecture

## System Overview

`nomon` runs on a small fleet of Raspberry Pi microcontrollers, each operating independently as a self-contained node. A mobile app and centralized management server interact with each Pi via its REST API.

```
┌─────────────────────────────────────────────────────────────────┐
│  Client Layer                                                   │
│                                                                 │
│   Mobile App          Mgmt Server             Admin (SSH)       │
│       │                   │                        │            │
│       │ HTTPS :8443        │ MQTT telemetry         │ Tailscale  │
└───────┼───────────────────┼────────────────────────┼────────────┘
        │                   │                        │
┌───────▼───────────────────▼────────────────────────▼────────────┐
│  Raspberry Pi Zero 2 W — Debian GNU/Linux 13 (trixie)           │
│                                                                 │
│   nomothetic.api (FastAPI/uvicorn)    StreamServer (Flask/MJPEG)     │
│         │                                │                      │
│   nomothetic.camera (picamera2) ──────────────┘                      │
│         │                                                       │
│   nomothetic.telemetry (paho-mqtt) ────────────► MQTT broker         │
│         │                                                       │
│   nomothetic.hat.HatClient                                           │
│         │  NDJSON over Unix socket (/run/nomopractic/nomopractic.sock)   │
│         ▼                                                       │
│   nomopractic.service (Rust daemon)                               │
│         │  rppal (pure-Rust I2C/GPIO)                           │
│         ▼                                                       │
│   SunFounder Robot HAT V4  ──  I2C bus 1, address 0x14         │
│         │                                                       │
│   OV5647 camera ── I2C bus 10/11, address 0x36 (muxed)         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Module Responsibilities

### `nomothetic.camera` — `Camera`

The lowest-level hardware abstraction. Wraps `picamera2` directly.

**Responsibilities:**
- Initialize and configure the OV5647 sensor
- Still image capture → JPEG files on disk
- Video recording → H264/MJPEG files on disk
- Provide a JPEG frame generator for streaming consumers
- Enforce filename safety (no path traversal)

**Key design decisions:**
- Conditional `picamera2` import — module is importable on non-Pi systems
- `directory` parameter controls where all files are written; never allows escape
- Single encoder instance; switching encoder requires reinitialization
- `get_jpeg_frame_generator()` yields raw JPEG bytes — both `StreamServer` and future direct callers use this

**Does NOT:**
- Serve HTTP
- Do network I/O
- Have awareness of the REST API

---

### `nomothetic.streaming` — `StreamServer`

A lightweight local LAN viewer. Not used by the mobile app.

**Responsibilities:**
- Create a `Camera` instance internally
- Serve an HTML viewer page at `/`
- Serve an MJPEG stream at `/stream` (multipart/x-mixed-replace)
- Run in foreground (`start()`) or background thread (`start_background()`)

**Key design decisions:**
- Flask chosen for minimal overhead — two endpoints only (see ADR-003)
- HTTP (not HTTPS) — LAN-only, not exposed to mobile clients
- Thread-safe frame sharing via `_frame_lock`
- Default binding: `localhost` — must be explicitly changed for LAN access

**Port:** 8000 (default, configurable)

---

### `nomothetic.api` — `APIServer` / `create_app()`

The primary remote control interface. Mobile app and management server talk to this.

**Responsibilities:**
- Expose camera operations as a JSON REST API
- Terminate HTTPS/TLS connections using self-signed certs
- Auto-generate self-signed certs on first run (stored in `.certs/`)
- Run in foreground (`run()`) or background thread (`start_background()`)
- Validate all incoming request data via Pydantic models

**Key design decisions:**
- FastAPI chosen for automatic OpenAPI docs and Pydantic integration (see ADR-002)
- Self-signed certs chosen for zero-configuration private network deployment (see ADR-001)
- CORS `allow_origins=["*"]` in development — restrict for production
- Global `_camera` instance managed by FastAPI lifespan context manager
- All responses include a UTC `timestamp` ISO 8601 field

**Port:** 8443 (default, configurable)

**Endpoints:**

| Method | Path | Tag | Description |
|--------|------|-----|-------------|
| `GET` | `/` | Health | Health check |
| `GET` | `/api/camera/status` | Camera | Camera state (resolution, fps, encoder, recording) |
| `POST` | `/api/camera/capture` | Camera | Still image capture |
| `POST` | `/api/camera/record/start` | Camera | Start video recording |
| `POST` | `/api/camera/record/stop` | Camera | Stop video recording |
| `POST` | `/api/camera/pan` | Vehicle | Set camera pan servo angle |
| `POST` | `/api/camera/tilt` | Vehicle | Set camera tilt servo angle |
| `GET` | `/api/hat/battery` | HAT | Read HAT battery voltage |
| `POST` | `/api/hat/servo` | HAT | Set servo channel angle |
| `POST` | `/api/hat/reset` | HAT | Assert MCU reset |
| `GET` | `/api/hat/servo/status` | HAT | Active servo TTL leases |
| `GET` | `/api/hat/mcu/status` | HAT | MCU reset counter |
| `POST` | `/api/hat/motor` | HAT | Set DC motor channel speed |
| `POST` | `/api/hat/motor/stop` | HAT | Stop all DC motors |
| `GET` | `/api/hat/motor/status` | HAT | Active motor TTL leases |
| `POST` | `/api/hat/speaker` | HAT | Enable/disable speaker amplifier (BCM 20) |
| `POST` | `/api/drive` | Vehicle | Drive all motors at signed speed |
| `POST` | `/api/steer` | Vehicle | Set steering servo angle |
| `GET` | `/api/sensor/grayscale` | Vehicle | Read grayscale ADC channels |
| `GET` | `/api/sensor/ultrasonic` | Sensor | Trigger ultrasonic and return distance |
| `POST` | `/api/stream/start` | Stream | Start MJPEG stream server |
| `POST` | `/api/stream/stop` | Stream | Stop MJPEG stream server |
| `GET` | `/api/stream/status` | Stream | Current stream server state |
| `POST` | `/api/audio/record/start` | Audio | Start USB mic recording |
| `POST` | `/api/audio/record/stop` | Audio | Stop USB mic recording |
| `POST` | `/api/audio/play` | Audio | Play WAV over HifiBerry DAC |
| `POST` | `/api/audio/play/stop` | Audio | Stop audio playback |
| `GET` | `/api/audio/files` | Audio | List available WAV files |
| `GET` | `/api/audio/status` | Audio | Current recorder/player state |
| `GET` | `/api/audio/volume` | Audio | Read current output volume (0–100) |
| `POST` | `/api/audio/volume` | Audio | Set output volume (HifiBerry DAC) |
| `GET` | `/api/audio/mic-gain` | Audio | Read current mic capture gain (0–100) |
| `POST` | `/api/audio/mic-gain` | Audio | Set mic capture gain (USB mic PCM2902) |
| `GET` | `/api/sensor/grayscale/normalized` | Sensor | Normalised grayscale sensor readings (0.0–1.0) |
| `GET` | `/api/calibration` | Calibration | Full calibration snapshot |
| `PUT` | `/api/calibration/motor/{channel}` | Calibration | Set motor calibration (speed_scale, deadband, reversed) |
| `PUT` | `/api/calibration/servo/{servo_name}` | Calibration | Set servo trim offset (µs) |
| `POST` | `/api/calibration/grayscale/{channel}/capture` | Calibration | Capture live ADC reading as white/black reference |
| `POST` | `/api/calibration/save` | Calibration | Persist calibration to disk |
| `POST` | `/api/calibration/reset` | Calibration | Revert in-memory calibration to defaults |
| `POST` | `/api/routine/start` | Routine | Start a named autonomous routine (currently: `explore`) |
| `POST` | `/api/routine/stop` | Routine | Stop the active routine; returns run statistics |
| `GET` | `/api/routine/status` | Routine | Query active routine state: running, elapsed time, avoidance counts |
| `GET` | `/docs` | — | Interactive Swagger UI |
| `GET` | `/redoc` | — | ReDoc API docs |

**HTTP Status Codes:**

| Code | Meaning |
|------|---------|
| `200` | Success |
| `400` | Bad request (invalid filename, bad parameters) |
| `409` | Conflict (recording already in/not in progress; routine ALREADY_RUNNING or not running) |
| `422` | Unprocessable Entity (Pydantic validation failure — invalid param type or range) |
| `500` | Server/hardware error |
| `503` | HAT daemon unavailable (nomopractic not running) |

---

### `nomothetic.telemetry` — `TelemetryPublisher`
A background telemetry publisher. Sends structured JSON to an MQTT broker.

**Responsibilities:**
- Discover device identity (env var → Pi serial → hostname)
- Build a JSON telemetry payload (device ID, timestamp, nomothetic version, camera status)
- Publish periodically over MQTT in a daemon background thread
- Handle broker unavailability with exponential back-off reconnect
- Expose a one-shot `publish_now()` for scripted or ad-hoc use

**Key design decisions:**
- Conditional `paho-mqtt` import — module is importable without paho-mqtt installed
- Fully standalone — no coupling to `APIServer` or `StreamServer` lifecycle
- `threading.Event` shutdown signal for clean daemon thread exit
- Back-off: 1 s → 2 s → 4 s → … capped at 60 s; resets on successful connect
- Camera is optional — payload `"camera"` field is `null` if no `Camera` provided
- All config via env vars (`NOMON_MQTT_*`) or constructor arguments

**Does NOT:**
- Receive MQTT messages (subscribe)
- Expose HTTP endpoints
- Block the REST API

**Port:** N/A — uses MQTT (default TCP 1883)

---

### `nomothetic.hat` — `HatClient`

The IPC client for the `nomopractic` Rust daemon. See
[docs/hat_python_client.md](hat_python_client.md) for the full module design.

**Responsibilities:**
- Open and maintain a connection to `/run/nomopractic/nomopractic.sock`
- Serialise requests and deserialise responses (NDJSON)
- Expose typed Python methods (`get_battery_voltage`, `set_servo_angle`, etc.)
- Raise `HatConnectionError` if the daemon is not running
- Apply per-request timeout

**Key design decisions:**
- Contains *no hardware register logic* — all hardware knowledge is in the Rust daemon
- `asyncio.to_thread` wraps blocking socket calls for FastAPI route handlers
- Persistent connection with automatic reconnect on broken pipe
- Follows the same conditional-import pattern as other `nomothetic` modules

**Does NOT:**
- Know about I2C addresses, PWM registers, ADC scaling
- Run its own thread — called synchronously from route handlers (wrapped in `to_thread`)

---

### `nomothetic.audio` — `AudioRecorder` / `AudioPlayer`

Handles USB microphone recording and HifiBerry DAC playback. Speaker amplifier
enable/disable is delegated to `HatClient` (nomopractic BCM 20 GPIO).

**Hardware:**
- **Microphone**: USB PnP Sound Device (Texas Instruments PCM2902), ALSA card 2
- **Speaker output**: HifiBerry DAC (ALSA card 1, `sndrpihifiberry`)
- **Speaker amplifier enable**: BCM 20 (`spk_en` on Robot HAT V4), controlled
  via `nomopractic` IPC (`enable_speaker` / `disable_speaker`)

**Responsibilities:**
- `AudioRecorder.start()`: opens PyAudio input stream on ALSA card 2, records in
  background thread, closes and writes WAV on `stop()`
- `AudioPlayer.play()`: opens PyAudio output stream on default device (HifiBerry
  DAC), plays WAV chunks in background thread
- `list_audio_files()`: lists `*.wav` files in the configured audio directory
- Graceful degradation: `RuntimeError` raised when `pyaudio` is not installed

**Key design decisions:**
- Camera stays in `nomothetic` (complex libcamera Python interface, no HAT GPIO)
- USB microphone stays in `nomothetic` (USB audio, no HAT GPIO interface needed)
- Speaker GPIO enable stays in `nomopractic` (HAT pin, same as all other GPIO)
- Audio playback stays in `nomothetic` (PyAudio/ALSA, Python-native)
- Background threading (not asyncio) for pyaudio compatibility
- `threading.Event` stop signal; thread joins with 3 s timeout

**Optional dependency:** `pyaudio>=0.2.14` in `[audio]` extra group

---

## Data Flow — Still Capture

```
Mobile App
  POST /api/camera/capture {"filename": "photo.jpg"}
        │
  APIServer (FastAPI route)
        │ validates filename
        │ calls Camera.capture_image("photo.jpg")
        │
  Camera
        │ starts picamera2 still config
        │ captures frame to disk at <directory>/photo.jpg
        │ returns
        │
  APIServer
        └─► 200 {"success": true, "filename": "photo.jpg", "timestamp": "..."}
```

---

## Data Flow — MJPEG Stream

```
Browser / LAN Client
  GET /stream (HTTP)
        │
  StreamServer (Flask)
        │ opens multipart/x-mixed-replace response
        │
  Camera.get_jpeg_frame_generator()
        │ yields JPEG bytes from picamera2
        │
  StreamServer
        └─► streams boundary-wrapped JPEG frames continuously
```

---

## Data Flow — HAT Battery Voltage

```
Mobile App
  GET /api/hat/battery
        │
  APIServer (FastAPI route)
        │ asyncio.to_thread(hat_client.get_battery_voltage)
        │
  HatClient (nomothetic.hat)
        │ {"id":"1","method":"get_battery_voltage","params":{}}\n
        │  →  Unix socket  →  nomopractic.service (Rust)
        │       I2C read: bus 1, addr 0x14, ADC channel A4
        │  ←  {"id":"1","ok":true,"result":{"voltage_v":7.42}}\n
        │
  APIServer
        └─► 200 {"voltage_v": 7.42, "timestamp": "..."}
```

---

## Data Flow — HAT Servo Angle

```
Mobile App
  POST /api/hat/servo {"channel": 0, "angle_deg": 90.0}
        │
  APIServer (FastAPI route)
        │ asyncio.to_thread(hat_client.set_servo_angle, 0, 90.0)
        │
  HatClient (nomothetic.hat)
        │ {"id":"2","method":"set_servo_angle","params":{"channel":0,"angle_deg":90.0,"ttl_ms":500}}\n
        │  →  Unix socket  →  nomopractic.service (Rust)
        │       I2C PWM write: pulse_us=1611 on channel 0
        │  ←  {"id":"2","ok":true,"result":{"channel":0,"angle_deg":90.0,"pulse_us":1611}}\n
        │
  APIServer
        └─► 200 {"channel": 0, "angle_deg": 90.0, "pulse_us": 1611, "timestamp": "..."}
```

---



| Concern | Approach |
|---------|----------|
| Transport encryption | TLS 1.2+ via uvicorn; self-signed cert auto-generated |
| Authentication (current) | None — relies on Tailscale VPN for network-layer access control |
| Authentication (planned) | JWT tokens or API keys (Phase 2.5) |
| Path traversal | Filename-only validation in `Camera`; rejects `/`, `\`, `..`, `.` prefix, absolute paths |
| CORS | `allow_origins=["*"]` in dev; tighten for production |
| Secrets | `python-dotenv` for envvars; `.env` and `.certs/` are gitignored |

---

## Dependency Map

```
nomothetic.hat
  ├── socket (stdlib)
  ├── json (stdlib)
  └── (no hardware deps — all hardware is in the Rust daemon)

nomothetic.api
  ├── nomothetic.camera
  ├── nomothetic.hat           (HatClient — IPC to nomopractic daemon)
  ├── fastapi
  ├── uvicorn
  ├── pydantic
  ├── cryptography
  └── python-dotenv

nomothetic.streaming
  ├── nomothetic.camera
  └── flask

nomothetic.camera
  ├── picamera2  (Pi only — install [pi] extra; conditional import)
  └── (no other runtime deps)

nomothetic.telemetry
  ├── nomothetic (for __version__)
  ├── paho-mqtt  (optional — conditional import)
  └── (standard library: threading, json, socket, os)
```

---

## Phase 5 — HAT Module Driver (Rust, Separate Repo)

The `nomopractic` Rust daemon (see ADR-006) runs as `nomopractic.service` and
communicates with `nomothetic.api` via a Unix domain socket at
`/run/nomopractic/nomopractic.sock`. Python was evaluated and rejected for HAT
drivers due to GIL-induced latency in timing-critical GPIO/I2C operations.

**Hardware confirmed:** SunFounder Robot HAT V4 on I2C bus 1 at address `0x14`.
See [docs/pi_hardware.md](pi_hardware.md) for discovery details.

**IPC:** `nomothetic.hat.HatClient` (Python) connects to the socket and exchanges
NDJSON messages with the Rust daemon. The full schema is defined in
[docs/hat_ipc_schema.md](hat_ipc_schema.md).

`nomothetic.api` HAT endpoints (`/api/hat/...`) proxy requests via `HatClient`.
If the daemon is not running, HAT endpoints return `503 Service Unavailable`.

**First milestone deliverables:** battery voltage reading + servo angle control.
See [docs/hat_python_client.md](hat_python_client.md) for the Python client design.

---

## Repository Strategy

All Python modules remain in this single repository. None of them have external
consumers or independent release cadences, so there is no benefit to splitting
them. Updates are applied atomically: a single `git pull` moves all modules to
the same commit simultaneously.

The Rust HAT daemon (`nomopractic`) lives in a separate repository because it
produces a different build artifact (compiled binary), uses a different update
mechanism (artifact download, not git), runs as a separate systemd service,
and has an independent release cadence. See ADR-006 for the full rationale.

```
nomothetic/              ← Python monorepo (this repo)
  nomothetic.camera
  nomothetic.streaming
  nomothetic.api
  nomothetic.telemetry
  nomothetic.hat     ← IPC client for nomopractic (Phase 5)

nomopractic/          ← Rust repo (Phase 5, separate)
  Cargo.toml
  src/main.rs
  systemd/nomopractic.service
```
