# nomon — Development Roadmap

## Status Summary

| Phase | Name | Status |
|-------|------|--------|
| 1 | Camera Module | ✅ Complete |
| 1.5 | MJPEG Stream Server | ✅ Complete |
| 2 | HTTPS REST API | ✅ Complete |
| 2.5 | Auth & Rate Limiting | 🔲 Optional / Deferred |
| 3 | MQTT Telemetry | ✅ Complete |
| 5 | HAT Module Driver (Rust) | ✅ Complete |
| 6 | Motor API Endpoints | ✅ Complete |
| 7 | Vehicle Convenience API | ✅ Complete |
| 8 | Audio & Peripheral Expansion | ✅ Complete |

**Test totals (current): 262 passing** (23 camera + 14 streaming + 113 API + 36 telemetry + 60 HAT + 16 audio)

---

## Completed Phases

### Phase 1 — Camera Module (`nomothetic.camera`)

**Deliverables:**
- `Camera` class wrapping `picamera2` for OV5647 sensor
- Still image capture: `capture_image(filename)`
- Video recording: `start_recording(filename)` / `stop_recording()`
- JPEG frame generator: `get_jpeg_frame_generator()`
- Encoder selection: H264 (default, 5 Mbps) or MJPEG
- Filename-only security validation with path traversal protection
- 20 passing tests

**Hardware specs confirmed:**
- Default video: 1280×720 @ 30 fps
- Max still: 2592×1944 @ 15.63 fps

---

### Phase 1.5 — MJPEG Stream Server (`nomothetic.streaming`)

**Deliverables:**
- `StreamServer` class using Flask
- HTML viewer at `GET /` with dark-themed responsive layout
- MJPEG stream at `GET /stream` (multipart/x-mixed-replace)
- Blocking (`start()`) and background thread (`start_background()`) modes
- Optional dependency: Flask in `[web]` group
- 14 passing tests

---

### Phase 2 — HTTPS REST API (`nomothetic.api`)

**Deliverables:**
- `APIServer` class using FastAPI + uvicorn
- HTTPS with auto-generated self-signed certificates in `.certs/`
- 5 camera control endpoints (see architecture.md)
- Pydantic request/response models with UTC timestamps
- CORS middleware for mobile clients
- OpenAPI docs at `/docs` and `/redoc`
- Optional dependency: FastAPI, uvicorn, cryptography, python-multipart, python-dotenv in `[api]` group
- 26 passing tests

**Test totals: 63 passing (20 camera + 14 streaming + 26 API + 3 integration)**

---

### Phase 3 — MQTT Telemetry (`nomothetic.telemetry`)

**Deliverables:**
- `TelemetryPublisher` class using `paho-mqtt` 2.x
- Background daemon thread (non-blocking, REST API unaffected)
- Structured JSON telemetry payload (device ID, camera status, nomothetic version, UTC timestamp)
- Configurable broker host/port/topic/interval via `config.toml` (`[mqtt]` section)
- Device ID auto-detection: env var → `/proc/cpuinfo` Pi serial → hostname
- Reconnect/retry with exponential back-off (1 s → 60 s cap)
- Optional dependency: `paho-mqtt` in `[telemetry]` group
- 23 passing tests

---

### Phase 5 — HAT Module Driver (Rust, Separate Repo)

**Hardware confirmed:** SunFounder Robot HAT V4 on I2C bus 1, address `0x14`.
See [docs/pi_hardware.md](pi_hardware.md) for discovery details.

**Language & repo:** Rust, in the `nomopractic` repository (see ADR-006).
Rust is chosen for deterministic latency in GPIO/I2C timing-critical
operations. The Python modules remain in this repo — they are I/O-bound
and gain nothing from a Rust conversion.

**IPC:** Unix domain socket at `/run/nomopractic/nomopractic.sock` with NDJSON framing.
Full schema: [docs/hat_ipc_schema.md](hat_ipc_schema.md).
Python client: `nomothetic.hat.HatClient` — see [docs/hat_python_client.md](hat_python_client.md).

**Milestone 5.1 — IPC Schema & Scaffold:**
- [x] `docs/hat_ipc_schema.md` — full IPC protocol spec
- [x] `docs/hat_python_client.md` — Python client design
- [x] `nomopractic` repository scaffolded; health IPC working on Pi

**Milestone 5.2 — Battery + Servo (P0 deliverables):**
- [x] `nomopractic`: I2C, ADC, battery voltage (`get_battery_voltage` IPC method)
- [x] `nomopractic`: PWM, servo angle + TTL watchdog
- [x] `nomothetic.hat.HatClient` with `get_battery_voltage`, `set_servo_angle`, `reset_mcu`, `health` (20 tests)
- [x] `nomothetic.api` endpoints: `GET /api/hat/battery`, `POST /api/hat/servo`, `POST /api/hat/reset`
- [x] Mock-socket tests in `tests/test_hat.py`

**Milestone 5.3 — MCU Reset + GPIO (P1):**
- [x] GPIO named pins (D4/D5/MCURST/SW/LED), `reset_mcu` IPC method
- [x] `POST /api/hat/reset` endpoint (Python + Rust both complete)
- [x] OTA binary deploy script (`nomopractic/scripts/deploy.sh`)

**Milestone 5.4 — CI & Release pipeline:**
- [x] GitHub Actions CI for `nomopractic`: fmt + clippy + tests + cross-compile aarch64
- [x] GitHub Releases on `v*` tags with SHA-256 artifact manifest
- [x] GitHub Actions CI for `nomothetic`: lint + type-check + tests

**Milestone 5.5 — Daemon State Endpoints:**
- [x] `nomopractic`: `get_servo_status` (active leases) and `get_mcu_status` (reset counter) IPC methods
- [x] `nomothetic.hat.HatClient.get_servo_status()` / `get_mcu_status()` with typed dataclasses
- [x] `GET /api/hat/servo/status` and `GET /api/hat/mcu/status` REST endpoints
- [x] Mock-socket tests in `tests/test_hat.py`; API tests in `tests/test_api.py`

**Milestone 5.6 — Launch scripts:**
- [x] `config.toml` — unified configuration template (`[stream]`, `[api]`, `[hat]`, `[audio]`, `[mqtt]`, `[telemetry]`, `[logging]`)
- [x] `scripts/start.sh stream|api|all` — background launch with PID tracking and log file
- [x] `scripts/stop.sh stream|api|all` — graceful shutdown via PID file
- [x] `scripts/deploy.sh` — SSH deploy with rollback support
- [x] `Makefile` targets: `start-stream`, `start-api`, `stop-stream`, `stop-api`, `stop`, `deploy`

**Design constraints:**
- Cross-compiled for `aarch64-unknown-linux-gnu` (CI uses `cross`)
- `nomothetic.api` HAT endpoints return `503 Service Unavailable` if daemon not running
- Python tests mock the IPC socket — testable on any developer machine without Pi hardware

---

### Phase 6 — Motor API Endpoints

**Goal**: Expose the DC motor control IPC methods (`set_motor_speed`,
`stop_all_motors`, `get_motor_status`) implemented in `nomopractic` as REST
API endpoints in `nomothetic.api`, with a matching `HatClient` façade and
full mock-socket/unit test coverage.

#### 6.1 — HatClient Motor Methods (`nomothetic.hat`)
- [x] `MotorLeaseEntry` dataclass: `channel`, `ttl_remaining_ms`, `conn_id`
- [x] `MotorStatusResult` dataclass: `active_leases: list[MotorLeaseEntry]`
- [x] `set_motor_speed(channel, speed_pct, ttl_ms)` — validates channel 0–3,
      speed_pct −100.0–100.0; sends `set_motor_speed` IPC call
- [x] `stop_all_motors()` — sends `stop_all_motors` IPC call; returns `stopped` count
- [x] `get_motor_status()` — sends `get_motor_status`; returns `MotorStatusResult`

#### 6.2 — REST Endpoints (`nomothetic.api`)
- [x] `POST /api/hat/motor` — set a motor channel's speed
      Request: `{channel: 0–3, speed_pct: −100.0–100.0, ttl_ms: 100–5000}`
      Response: `{channel, speed_pct, timestamp}`
- [x] `POST /api/hat/motor/stop` — immediately stop all motors
      Response: `{stopped: N, timestamp}`
- [x] `GET /api/hat/motor/status` — return active motor TTL lease table
      Response: `{active_leases: [...], timestamp}`
- [x] `503` on `HatConnectionError`; `500` on `HatError`; `422` on invalid params

#### 6.3 — Tests
- [x] `tests/test_hat.py`: `set_motor_speed`, `stop_all_motors`, `get_motor_status`
      (success, validation errors, hardware error)
- [x] `tests/test_api.py`: all three motor endpoints (success, 503 no client,
      503 connection error, 500 hardware error, 422 invalid params)

---

### Phase 7 — Vehicle Convenience API

**Goal**: High-level REST endpoints and matching `HatClient` methods that
replace raw channel-index calls with named, coordinated vehicle commands.
Channel-to-peripheral mappings are owned by the `nomopractic` daemon config;
nomothetic simply calls the named IPC methods.

#### 7.1 — HatClient Vehicle Methods
- [x] `GrayscaleResult` dataclass: `channels: list[int]`, `values: list[int]`
- [x] `drive(speed_pct, ttl_ms)` → IPC `drive` (all motors in sync)
- [x] `steer(angle_deg, ttl_ms)` → IPC `steer`
- [x] `pan_camera(angle_deg, ttl_ms)` → IPC `pan_camera`
- [x] `tilt_camera(angle_deg, ttl_ms)` → IPC `tilt_camera`
- [x] `read_grayscale()` → IPC `read_grayscale`, returns `GrayscaleResult`
- [x] `ValueError` raised on out-of-range inputs before IPC call

#### 7.2 — REST Vehicle Endpoints
- [x] `POST /api/drive` — `{ speed_pct, ttl_ms? }` → `{ speed_pct, motors }`
- [x] `POST /api/steer` — `{ angle_deg, ttl_ms? }` → `{ angle_deg }`
- [x] `POST /api/camera/pan` — `{ angle_deg, ttl_ms? }` → `{ angle_deg }`
- [x] `POST /api/camera/tilt` — `{ angle_deg, ttl_ms? }` → `{ angle_deg }`
- [x] `GET /api/sensor/grayscale` → `{ channels, values }`
- [x] All endpoints tagged `"Vehicle"` in OpenAPI docs
- [x] 503 on daemon unavailable, 500 on hardware error, 422 on invalid params

#### 7.3 — Tests
- [x] `tests/test_hat.py`: 23 new tests (drive, steer, pan_camera, tilt_camera,
      read_grayscale — success, validation, error cases)
- [x] `tests/test_api.py`: 20 new tests for all 5 vehicle endpoints
- [x] `uv run pytest tests/` — 208 passing
- [x] `uv run ruff check src/ tests/` — 0 errors
- [x] `uv run black --check src/ tests/` — clean
- [x] `uv run mypy src/ tests/` — 0 errors

---

### Phase 8 — Audio & Peripheral Expansion

**Goal**: Expose the ultrasonic distance sensor and speaker amplifier enable
(both new in nomopractic Phase 8) as REST API endpoints, add USB microphone
recording and HifiBerry DAC playback via a new `nomothetic.audio` module, and
wire stream start/stop into the REST API.

#### 8.1 — HatClient Peripheral Methods (`nomothetic.hat`)
- [x] `UltrasonicResult` dataclass: `distance_cm: float`
- [x] `read_ultrasonic()` → `UltrasonicResult` (IPC `read_ultrasonic`)
- [x] `enable_speaker()` → `None` (IPC `enable_speaker`, asserts BCM 20 high)
- [x] `disable_speaker()` → `None` (IPC `disable_speaker`, asserts BCM 20 low)

#### 8.2 — Audio Module (`nomothetic.audio`)
- [x] New module `src/nomothetic/audio.py`
- [x] `AudioRecorder`: records USB mic (PCM2902, ALSA card 2) to WAV
  - `start(filename=None) -> str`: starts background recording thread; returns output path
  - `stop() -> str | None`: signals thread, finalises WAV; returns path or None
  - Auto-generated timestamped filename when `filename` is absent
- [x] `AudioPlayer`: plays WAV via HifiBerry DAC (default output device)
  - `play(filename)`: resolves bare names against `audio_dir`; starts background thread
  - `stop()`: signals thread early
- [x] `AudioStatus` dataclass: `recording`, `recording_file`, `playing`, `playback_file`
- [x] `list_audio_files(audio_dir=None) -> list[str]`: sorted WAV basenames
- [x] Optional pyaudio dependency: `RuntimeError` raised when not installed
- [x] Constants: `DEFAULT_AUDIO_DIR` (`$NOMON_MEDIA_DIR/audio`, derived from `NOMON_MEDIA_DIR` env),
      `DEFAULT_INPUT_DEVICE_INDEX` (`NOMON_AUDIO_INPUT_INDEX` env, default 2)
- [x] Optional dependency group `[audio]` — `pyaudio>=0.2.14`

#### 8.3 — REST Endpoints (`nomothetic.api`)
- [x] `GET /api/sensor/ultrasonic` → `{ distance_cm, timestamp }` (tag: Sensor)
- [x] `POST /api/hat/speaker` → `{ enabled, timestamp }` (tag: HAT)
- [x] `POST /api/stream/start` → `{ url, host, port, timestamp }` (tag: Stream)
  - Starts `StreamServer` in background; returns existing URL if already running
- [x] `POST /api/stream/stop` → `{ success, timestamp }` (tag: Stream)
- [x] `GET /api/stream/status` → `{ running, url, timestamp }` (tag: Stream)
- [x] `POST /api/audio/record/start` → `{ recording, filename, timestamp }` (tag: Audio)
- [x] `POST /api/audio/record/stop` → `{ recording, filename, timestamp }` (tag: Audio)
- [x] `POST /api/audio/play` → enables speaker, starts playback → `{ playing, filename, timestamp }`
- [x] `POST /api/audio/play/stop` → stops playback, disables speaker → `{ success, timestamp }`
- [x] `GET /api/audio/files` → `{ files, timestamp }` (tag: Audio)
- [x] `GET /api/audio/status` → `{ recording, recording_file, playing, playback_file, timestamp }`
- [x] `lifespan` initialises `AudioRecorder` and `AudioPlayer` on startup; tears them down on shutdown

#### 8.4 — Tests
- [x] `tests/test_hat.py`: 8 new tests (ultrasonic success/error; speaker enable/disable success/error)
- [x] `tests/test_audio.py`: 16 new tests (list_audio_files, AudioRecorder, AudioPlayer)
- [x] `tests/test_api.py`: 36 new tests covering all 11 new endpoints
- [x] `uv run pytest tests/` — 262 passing
- [x] `uv run ruff check src/ tests/` — 0 errors
- [x] `uv run black --check src/ tests/` — clean
- [x] `uv run mypy src/ tests/` — 0 errors

**Architecture notes:**
- Camera stays in `nomothetic` (Python libcamera; no HAT GPIO needed)
- USB microphone recording stays in `nomothetic` (USB audio, not HAT GPIO)
- Speaker GPIO enable (BCM 20) is controlled by `nomopractic`; audio output
  (PyAudio → HifiBerry DAC) is handled directly by `nomothetic`
- `POST /api/audio/play` automatically enables the speaker via HAT client
  before starting playback; `POST /api/audio/play/stop` disables it after

---

## Upcoming

### Phase 2.5 — Authentication & Rate Limiting (Optional)

Adds security layers on top of the existing API. Can be deferred since Tailscale VPN currently provides network-layer access control.

**Candidate deliverables:**
- [ ] JWT token issuance and validation middleware
- [ ] API key management (create/revoke/list via admin endpoint)
- [ ] Per-client rate limiting
- [ ] Request audit logging (structured JSON log file)
- [ ] `GET /api/admin/keys` endpoint (protected)

**Implementation approach:**
- Middleware-first: avoid coupling auth logic into route handlers
- Consider `fastapi-users` or hand-rolled JWT with `python-jose`/`authlib`
- Rate limiting via `slowapi` (wraps `limits`)
- Log to file; Phase 3 MQTT can forward logs to management server

---

### v0.3.0 Release Prep

**Goal**: Consolidate configuration strategy, remove legacy setup files, and
confirm all new work passes checks before tagging.

- [x] Config strategy formalised: `.env` = secrets only; `config.toml` = safe
      defaults (committed to repo; no copy step required)
- [x] `config.toml` ships with new `[audio]`, `[mqtt]`, `[telemetry]` sections
- [x] `scripts/start.sh` extended to parse and export audio/MQTT/telemetry config
- [x] `requirements.txt` and `requirements-dev.txt` removed (superseded by
      `pyproject.toml` + `uv.lock`)
- [x] `docs/releases/` removed (GitHub auto-generates release notes from tags)
- [x] Version bumped to `0.3.0` in `pyproject.toml`
- [x] `uv run pytest tests/` — 262 passing
- [x] `uv run ruff check`, `black --check`, `mypy` — clean

---

### Phase 9 — Audio Levels Control (P1)

**Goal**: Add REST API endpoints (and matching `HatClient` methods) to control
output volume (HifiBerry DAC) and input gain (USB microphone PCM2902). Requires
corresponding IPC methods in `nomopractic` (see nomopractic Phase 9).

**Candidate deliverables:**

**Output Volume:**
- [ ] `HatClient.set_volume(volume_pct: int)` — sends `set_volume` IPC call (0–100)
- [ ] `HatClient.get_volume() -> int` — sends `get_volume` IPC call
- [ ] `POST /api/audio/volume` request `{ volume_pct: 0–100 }` → response `{ volume_pct, timestamp }`
- [ ] `GET /api/audio/volume` → response `{ volume_pct, timestamp }`
- [ ] Pydantic models: `VolumeRequest`, `VolumeResponse`
- [ ] Auto-apply configured default volume on `POST /api/audio/play` startup

**Input Gain:**
- [ ] `HatClient.set_mic_gain(gain_pct: int)` — sends `set_mic_gain` IPC call (0–100)
- [ ] `HatClient.get_mic_gain() -> int` — sends `get_mic_gain` IPC call
- [ ] `POST /api/audio/mic-gain` request `{ gain_pct: 0–100 }` → response `{ gain_pct, timestamp }`
- [ ] `GET /api/audio/mic-gain` → response `{ gain_pct, timestamp }`
- [ ] Pydantic models: `MicGainRequest`, `MicGainResponse`
- [ ] Auto-apply configured default input gain on `POST /api/audio/record` startup

**Testing & Integration:**
- [ ] New tests in `test_hat.py` and `test_api.py` for volume and mic gain endpoints

---

## Adjacent Systems

### Mobile App

Developed in a separate repository. Consumes the `nomothetic` REST API.

**Expected interface:**
- HTTPS requests to `https://<pi-tailscale-ip>:8443`
- Self-signed cert acceptance (trust on first use or pinned cert)
- Endpoints: status, capture, record start/stop
- Future: stream preview, telemetry dashboard, HAT control

### Management Server

Developed in a separate repository.

**Expected interface:**
- MQTT broker (receives telemetry from fleet)
- Version manifest endpoint (serves release metadata for OTA)
- Object storage (S3-compatible) for release artifacts
- Admin dashboard for fleet monitoring

**AWS IoT path:** If AWS IoT is adopted, the management server uses
AWS IoT Core as the MQTT broker and AWS IoT Jobs for fleet update dispatch.
See ADR-007 and [docs/phase5_planning.md](phase5_planning.md).
