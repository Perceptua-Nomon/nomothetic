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
- Configurable broker host/port/topic/interval via `.env` (`NOMON_MQTT_*`)
- Device ID auto-detection: env var → `/proc/cpuinfo` Pi serial → hostname
- Reconnect/retry with exponential back-off (1 s → 60 s cap)
- Optional dependency: `paho-mqtt` in `[telemetry]` group
- 23 passing tests

**Test totals: 86 passing (20 camera + 14 streaming + 26 API + 3 integration + 23 telemetry)**

> Updated total including Phase 5: **130 passing** (23 camera + 14 streaming + 37 API + 36 telemetry + 20 HAT)

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

**Design constraints:**
- Cross-compiled for `aarch64-unknown-linux-gnu` (CI uses `cross`)
- `nomothetic.api` HAT endpoints return `503 Service Unavailable` if daemon not running
- Python tests mock the IPC socket — testable on any developer machine without Pi hardware

**nomopractic test totals:** 82 tests (9 config + 5 handler + 5 integration + 3 i2c + 4 adc + 3 battery + 14 servo + 5 pwm + 6 gpio + 1 reset + 11 handler/integration additions)

---

## Upcoming Phases

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

## Mobile App

Developed in a separate repository. Consumes the `nomothetic` REST API.

**Expected interface:**
- HTTPS requests to `https://<pi-tailscale-ip>:8443`
- Self-signed cert acceptance (trust on first use or pinned cert)
- Endpoints: status, capture, record start/stop
- Future: stream preview, telemetry dashboard, HAT control

---

## Management Server

Developed in a separate repository.

**Expected interface:**
- MQTT broker (receives telemetry from fleet)
- Version manifest endpoint (serves release metadata for OTA)
- Object storage (S3-compatible) for release artifacts
- Admin dashboard for fleet monitoring

**AWS IoT path:** If AWS IoT is adopted, the management server uses
AWS IoT Core as the MQTT broker and AWS IoT Jobs for fleet update dispatch.
See ADR-007 and [docs/phase5_planning.md](phase5_planning.md).
