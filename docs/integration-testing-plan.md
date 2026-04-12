# nomothetic — Integration Testing Plan

## Overview

This document covers the integration testing strategy for nomothetic across
both deployment modes (central and device), cross-repo boundaries, and planned
features not yet implemented. Each test area indicates whether it can be tested
today and what infrastructure is required.

---

## Testing Infrastructure

### Current Tools

| Tool | Purpose |
|------|---------|
| **pytest** | Test runner for all Python tests |
| **FastAPI `TestClient`** (via httpx) | In-process HTTP testing — no real server needed |
| **`unittest.mock`** | Mock hardware (picamera2, pyaudio, GPIO, I2C) and IPC sockets |
| **Mock Unix socket server** | Background-thread socket server for HatClient IPC tests |
| **`patch.dict(os.environ, ...)`** | Switch between central/device mode and auth modes per test |
| **pytest fixtures** | `central_client`, `client`, `device_auth_client`, `device_no_auth_client`, `auth_service` for repeatable setup |

### Future Infrastructure Needed

| Tool | Purpose | When Needed |
|------|---------|-------------|
| **ArcadeDB test instance** (Docker) | Integration tests for `GremlinUserStore`, `GremlinFleetStore`, and `DatabaseClient` | Available now — central via `nomographic/docker-compose.yml` + `scripts/init-db.sh central`; local via `scripts/migrate-local.sh` |
| **BLE simulator / mock** | Test BLE discovery and Wi-Fi provisioning | When BLE implementation starts |
| **MQTT test broker** (Mosquitto Docker) | Telemetry publish integration tests | For end-to-end telemetry validation |
| **pytest-asyncio** | If async test patterns are introduced | If test coverage expands to async flows |

---

## Central Mode Functionality

All tests in this section use `NOMON_API_MODE=central` and a `TestClient`
fixture with `NOMON_JWT_SECRET` injected. No hardware or database required —
user and device state lives in in-memory stores.

### Auth Flows

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Registration | Successful user registration returns 201 + tokens + profile | Yes | `test_register_success` |
| Registration | Duplicate email returns 409 | Yes | `test_register_duplicate_email` |
| Registration | Invalid email format returns 422 | Yes | `test_register_invalid_email` |
| Registration | Password < 8 chars returns 422 | Yes | `test_register_short_password` |
| Registration | Email normalised to lowercase | Yes | `test_email_normalised` (unit) |
| Login | Valid credentials return tokens | Yes | `test_login_success` |
| Login | Wrong password returns 401 | Yes | `test_login_wrong_password` |
| Login | Unknown email returns 401 | Yes | `test_login_unknown_email` |
| Login | Inactive user rejected | Yes | `test_authenticate_inactive_user` (unit) |
| Token refresh | Valid refresh token returns new token pair | Yes | `test_refresh_success` |
| Token rotation | Old refresh token invalidated after use | Yes | `test_refresh_rotation_invalidates_old` |
| Token refresh | Invalid refresh token returns 401 | Yes | `test_refresh_invalid_token` |
| Expired token | Expired JWT rejected with error | Yes | `test_expired_token_rejected` (unit) |
| Invalid token | Malformed JWT rejected | Yes | `test_invalid_token_rejected` (unit) |
| Profile | Authenticated `GET /api/auth/me` returns profile | Yes | `test_me_authenticated` |
| Profile | Unauthenticated request returns 401 | Yes | `test_me_unauthenticated` |
| Profile | Invalid bearer token returns 401 | Yes | `test_me_invalid_token` |
| Logout | `POST /api/auth/logout` revokes refresh token | Yes | `test_logout_success` |
| Logout | Unauthenticated logout returns 401 | Yes | `test_logout_unauthenticated` |

### Fleet CRUD

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Register device | `POST /api/fleet/devices` with valid token returns 201 | Yes | `test_register_device` |
| Duplicate device | Same VIN returns 409 | Yes | `test_register_duplicate_device` |
| List devices | `GET /api/fleet/devices` returns user's devices | Yes | `test_list_devices` |
| Device detail | `GET /api/fleet/devices/{vin}` returns device + role | Yes | `test_get_device_detail` |
| Device not found | Unknown VIN returns 404 | Yes | `test_get_device_not_found` |
| Remove device | `DELETE /api/fleet/devices/{vin}` removes device | Yes | `test_remove_device` |
| Remove not found | Removing unknown VIN returns 404 | Yes | `test_remove_device_not_found` |
| Unauthenticated | Fleet endpoints without token return 401 | Yes | `test_fleet_unauthenticated` |

### Rate Limiting

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Registration limit | 11th registration in window returns 429 | Yes | `test_register_rate_limit_429` |
| Under limit | Requests under limit pass | Yes | `test_allows_requests_under_limit` |
| Over limit | Requests over limit return 429 | Yes | `test_blocks_requests_over_limit` |
| Per-IP isolation | Different IPs have independent counters | Yes | `test_different_keys_independent` |
| Window expiry | Old requests expire after sliding window | Yes | `test_requests_expire_after_window` |
| Reset | `reset()` clears all state | Yes | `test_reset_clears_all_state` |
| Client IP extraction | Correct IP from `X-Forwarded-For` / direct | Yes | `TestClientIp` class |

### JWT Dependency Injection

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| `jwt_required` | Protected routes reject missing/invalid tokens | Yes | Tested via fleet + `/api/auth/me` endpoints |
| Token payload | Valid token carries correct `sub` and `iss` claims | Yes | `test_create_and_verify_access_token` |
| Secret validation | Secret < 32 chars rejected at init | Yes | `test_secret_too_short` |
| Env var secret | `NOMON_JWT_SECRET` read from environment | Yes | `test_secret_from_env` |

### CORS (Central Mode)

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Explicit origins | Central mode sets explicit `allow_origins` with `allow_credentials=True` | Yes | Verify via `OPTIONS` preflight in TestClient |
| Credentials | `Access-Control-Allow-Credentials: true` header present | Partially | Test CORS middleware config; full browser test needs manual verification |

### Error Responses

| Code | Meaning | Tested Via |
|------|---------|-----------|
| 400 | Bad request (malformed body) | Camera/HAT endpoint tests |
| 401 | Unauthorized (missing/invalid/expired token) | Auth flow tests |
| 404 | Not found (unknown device, unknown route) | Fleet and mode tests |
| 409 | Conflict (duplicate email, duplicate VIN) | Registration + fleet tests |
| 422 | Validation failure (invalid email format, short password) | Registration tests |
| 429 | Rate limited | Rate limit tests |
| 503 | HAT daemon unavailable | HAT endpoint tests (mocked socket refusal) |

---

## Device Mode Functionality

All tests in this section use the default `NOMON_API_MODE=device` (or unset).
Hardware is mocked — no Raspberry Pi required.

### Health

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Health endpoint | `GET /` returns status, version, mode | Yes | `test_health_check` |
| Mode indicator | Health response shows `"mode": "device"` | Yes | Implied by default mode |

### Camera Endpoints

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Camera status | `GET /api/camera/status` returns resolution, fps, recording state | Yes | `test_camera_status_with_camera` |
| No camera | Status without camera returns 500 | Yes | `test_camera_status_without_camera` |
| Still capture | `POST /api/camera/capture` captures JPEG | Yes | `test_capture_image_success` (mock camera) |
| Recording state | Status reflects `_is_recording` | Yes | `test_camera_status_recording` |
| Filename safety | Path traversal attempts rejected | Yes | Camera module tests |

### HAT Endpoints (via IPC)

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Battery voltage | `GET /api/hat/battery` | Yes | Mock socket server returns canned response |
| Servo control | `POST /api/hat/servo` | Yes | `test_set_servo_pulse_us`, `test_set_servo_angle` |
| Servo status | `GET /api/hat/servo/status` active leases | Yes | `test_get_servo_status` |
| Motor control | `POST /api/hat/motor` | Yes | `test_set_motor_speed` |
| Motor stop | `POST /api/hat/motor/stop` | Yes | `test_stop_all_motors` |
| Motor status | `GET /api/hat/motor/status` | Yes | `test_get_motor_status` |
| MCU reset | `POST /api/hat/reset` | Yes | `test_reset_mcu` |
| MCU status | `GET /api/hat/mcu/status` | Yes | `test_get_mcu_status` |
| Speaker enable | `POST /api/hat/speaker` | Yes | `test_enable_speaker`, `test_disable_speaker` |
| Drive | `POST /api/drive` | Yes | `test_drive` |
| Steer | `POST /api/steer` | Yes | `test_steer` |
| Camera pan/tilt | `POST /api/camera/pan`, `/tilt` | Yes | `test_pan_camera`, `test_tilt_camera` |
| Connection error | Daemon not running → `HatConnectionError` → 503 | Yes | `test_connection_refused` |

### Sensor Endpoints

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Grayscale | `GET /api/sensor/grayscale` | Yes | Mock socket response |
| Grayscale normalized | `GET /api/sensor/grayscale/normalized` | Yes | Mock socket response |
| Ultrasonic | `GET /api/sensor/ultrasonic` | Yes | `test_read_ultrasonic` |
| I2C scan | Combined with HAT health | Partially | Health method checks I2C status |

### Audio Endpoints

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| TTS / speak | `POST /api/audio/play` | Yes | Mocked PyAudio |
| List voices | `GET /api/audio/files` | Yes | Lists WAV files in directory |
| Volume control | `GET/POST /api/audio/volume` | Yes | Mock IPC |
| Mic gain | `GET/POST /api/audio/mic-gain` | Yes | Mock IPC |
| Record start/stop | `POST /api/audio/record/start`, `/stop` | Yes | Mocked PyAudio stream |

### Calibration Endpoints

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Get calibration | `GET /api/calibration` | Yes | Mock IPC returns snapshot |
| Set motor cal | `PUT /api/calibration/motor/{channel}` | Yes | Mock IPC |
| Set servo trim | `PUT /api/calibration/servo/{servo_name}` | Yes | Mock IPC |
| Grayscale capture | `POST /api/calibration/grayscale/{channel}/capture` | Yes | Mock IPC |
| Save calibration | `POST /api/calibration/save` | Yes | Mock IPC |
| Reset calibration | `POST /api/calibration/reset` | Yes | Mock IPC |

### Routine Endpoints

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Start routine | `POST /api/routine/start` | Yes | Mock IPC |
| Stop routine | `POST /api/routine/stop` | Yes | Mock IPC |
| Routine status | `GET /api/routine/status` | Yes | Mock IPC |
| Already running | Starting while active returns 409 | Yes | `ALREADY_RUNNING` error code |

### Streaming Endpoints

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Start stream | `POST /api/stream/start` | Yes | Mock camera, `test_streaming.py` |
| Stop stream | `POST /api/stream/stop` | Yes | `test_stop_stream` |
| Stream status | `GET /api/stream/status` | Yes | `test_stream_status` |
| MJPEG feed | `GET /api/stream/feed` | Yes | SSE/MJPEG response verified |
| Double start | Starting while active returns 409 | Yes | `test_start_already_streaming` |

### Telemetry Endpoints

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Publish telemetry | `POST /api/telemetry` | Yes | `test_telemetry.py` |
| Get latest | `GET /api/telemetry/latest` | Yes | In-memory telemetry store |
| Get history | `GET /api/telemetry/history` | Yes | Filtered by time range |
| MQTT bridge | Telemetry forwarded via MQTT | Yes | Mock MQTT client |

### IPC Layer (nomothetic ↔ nomopractic)

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| NDJSON framing | Request/response roundtrip over mock socket | Yes | `test_hat.py` uses real socket I/O in-process |
| Request ID echo | Response `id` matches request `id` | Yes | Mock server echoes ID |
| Error propagation | IPC error codes map to `HatError` | Yes | `test_unknown_method`, hardware error tests |
| Connection refused | Missing daemon → `HatConnectionError` | Yes | `test_connection_refused` |
| Timeout handling | Socket timeout raises expected error | Yes | Configurable timeout in HatClient |
| Multiple methods | All IPC methods have dedicated tests | Yes | ~30 methods covered in `test_hat.py` |

### Device-Mode Authentication (Phase 17)

All tests in this section use the default `NOMON_API_MODE=device` with
`NOMON_DEVICE_AUTH=true` (default). Tests use the `device_auth_client` fixture
from `test_device_auth.py`. No hardware, database, or network required —
pairing state, users, and tokens live in in-memory stores.

#### Pairing Flow

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Pairing status | `GET /api/device/auth/status` returns `{ paired: false, pairing_available: true }` before pairing | Yes | `test_status_before_pairing` |
| Pair success | `POST /api/device/auth/pair` with correct secret → tokens + `paired: true` | Yes | `test_pair_success` |
| Wrong secret | Incorrect secret → 401 | Yes | `test_pair_wrong_secret` |
| Already paired | Second pair attempt → 409 | Yes | `test_pair_already_paired` |
| Rate limit | >3 pairing attempts in 60s → 429 | Yes | `test_pair_rate_limit` |
| Status after pair | `GET /api/device/auth/status` returns `{ paired: true, pairing_available: false }` | Yes | `test_status_after_pairing` |

#### Token Management

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Token refresh | `POST /api/device/auth/refresh` with valid refresh token → new token pair | Yes | `test_refresh_success` |
| Invalid refresh | Invalid token → 401 | Yes | `test_refresh_invalid` |
| Profile | `GET /api/device/auth/me` with valid access token → user profile | Yes | `test_me_authenticated` |
| No token | `GET /api/device/auth/me` without token → 401 | Yes | `test_me_unauthenticated` |

#### Endpoint Protection

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Protected endpoints | Device endpoints (e.g. `GET /api/sensor/grayscale`) return 401 without token | Yes | `test_device_endpoint_requires_token` |
| Auth bypass | Same endpoint succeeds with valid bearer token from pairing | Yes | `test_device_endpoint_with_token` |
| Health unprotected | `GET /` returns 200 without auth | Yes | `test_health_no_auth_required` |
| Auth opt-out | `NOMON_DEVICE_AUTH=false` → endpoints unauthenticated | Yes | `test_device_auth_disabled` |

#### Pairing Module (Unit)

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Secret entropy | `generate_secret()` returns ≥22 chars (128 bits) | Yes | `test_generate_secret_has_sufficient_entropy` |
| Consume-once | `verify_and_consume()` succeeds once, then fails | Yes | `test_verify_and_consume_success`, `test_consumed_secret_cannot_reuse` |
| Wrong secret | `verify_and_consume()` returns `False` for wrong input | Yes | `test_verify_and_consume_wrong_secret` |
| Constant-time | Uses `hmac.compare_digest` (not `==`) | Yes | Code inspection + `test_verify_uses_constant_time_compare` |
| Reset | `reset()` clears state, regenerates JWT secret | Yes | `test_reset_clears_paired_state`, `test_reset_regenerates_jwt_secret` |
| JWT secret | Auto-generated ≥32 chars on construction | Yes | `test_jwt_secret_auto_generated` |
| Cross-issuer | Device tokens rejected by central issuer and vice versa | Yes | `test_cross_issuer_rejection` |

### CORS (Device Mode)

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Restricted origin | Device mode uses `NOMON_CORS_ORIGINS` (defaults to `https://10.0.0.1:8443`) | Yes | Verify middleware config |
| No credentials | `allow_credentials=False` in device mode | Yes | Implicit |

---

## Mode Isolation

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Default is device | Unset `NOMON_API_MODE` → device mode | Yes | `test_default_mode_is_device` |
| Central mode | `NOMON_API_MODE=central` recognised | Yes | `test_central_mode` |
| Case insensitive | `CENTRAL`, `Central` all work | Yes | `test_case_insensitive` |
| Whitespace stripped | `"  central  "` → central | Yes | `test_whitespace_stripped` |
| Invalid → device | Unknown values fall back to device | Yes | `test_invalid_mode_falls_back_to_device` |
| Route isolation | Device endpoints 404 in central mode | Yes | `test_device_endpoints_not_registered` |

---

## Bluetooth & Device Registration (Stubs Only)

> **Status:** Interface stubs exist in nomotactic (`lib/ble.ts`). No real BLE
> implementation exists in any repo. The types and mock service below define
> the planned contract.

### nomotactic BLE Stubs

| Component | Status | Location |
|-----------|--------|----------|
| `BleService` interface | Defined | `nomotactic/lib/ble.ts` |
| `BleDevice` type | Defined | `nomotactic/lib/ble.ts` |
| `ConnectionStatus` type | Defined | `nomotactic/lib/ble.ts` |
| `MockBleService` | Implemented | `nomotactic/lib/ble.ts` — returns hardcoded devices |

### Planned BLE Flow

```
nomotactic (app)                nomopractic (Pi)
     │                                │
     │  1. BLE scan for nomon ads     │ ◄── BLE advertisement (not implemented)
     │────────────────────────────────►│
     │  2. Connect via BLE            │
     │────────────────────────────────►│
     │  3. Send Wi-Fi credentials     │ ◄── BLE characteristic write (not implemented)
     │────────────────────────────────►│
     │  4. Pi joins Wi-Fi             │
     │                                │
     │  5. Register device with       │
     │     central API (HTTP)         │
     │────────► nomothetic central    │
```

### Planned Tests

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| BLE scan | Discover nearby nomon devices | No | `MockBleService.scan()` returns hardcoded list; real BLE not implemented |
| BLE connect | Connect to a specific device | No | Mock connect simulates delay; no real BLE stack |
| BLE send command | Send UTF-8 command to device | No | Mock echoes command; no real BLE characteristic |
| Wi-Fi provisioning | Send Wi-Fi SSID/password over BLE | No | Protocol not defined yet |
| Device registration | BLE discovery → HTTP registration with central | No | Central API exists; BLE→HTTP bridge not built |
| BLE advertisement | nomopractic advertises as BLE peripheral | No | Not implemented in nomopractic |

---

## Cross-Repo Integration

### nomotactic ↔ nomothetic (central)

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Auth via HTTP | Login / register flows from app to central API | Partially | nomotactic `lib/api.ts` has service layer; testable with a mock central server or running TestClient |
| Token storage | nomotactic stores and refreshes tokens | Partially | Auth context exists in `lib/auth.tsx`; unit testable |
| Fleet management | App lists / registers devices via central API | Partially | API layer defined; needs integration test harness |

### nomotactic ↔ nomothetic (device)

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| Device pairing | App pairs with device via `POST /api/device/auth/pair` | Partially | `lib/auth.tsx` has `pairWithDevice()`; testable with mock server |
| Device token storage | Device tokens stored separately from central tokens | Partially | `lib/auth.tsx` uses `nomon_device_access_token` / `nomon_device_refresh_token` keys |
| Device token injection | `deviceApi()` sends device bearer token (not central token) | Partially | `lib/api.ts` routes tokens by base URL |
| Device token refresh | 401 on device API triggers device-specific refresh | Partially | Refresh handler in `lib/api.ts` |
| Status polling | App polls device health endpoint | Partially | `lib/api.ts` calls device endpoints; testable with mock server |
| Command dispatch | App sends motor/servo/camera commands with auth | Partially | API functions defined; needs mock device server |
| Camera streaming | App displays MJPEG stream from device | No | Requires running StreamServer or mock stream source |

### nomothetic ↔ nomopractic (IPC)

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| HatClient → daemon | Full NDJSON roundtrip over Unix socket | Yes | `test_hat.py` — mock socket server in background thread |
| All IPC methods | Every method in `hat_ipc_schema.md` tested | Yes | Comprehensive mock responses for all ~30 methods |
| Error handling | IPC error codes propagate to HTTP responses | Yes | `HatConnectionError` → 503, `HatError` → 500 |
| Connection lifecycle | Connect, disconnect, reconnect | Yes | Tested via mock socket server lifecycle |

### nomothetic ↔ nomographic (ArcadeDB)

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| User storage | Users stored in ArcadeDB `User` vertex | Yes | `GremlinUserStore` in `user_store.py` — requires running ArcadeDB (Docker Compose) |
| Device storage | Devices stored in ArcadeDB `Vehicle` vertex | Yes | `GremlinFleetStore` in `fleet_store.py` — requires running ArcadeDB (Docker Compose) |
| Token storage | Refresh tokens stored in ArcadeDB `RefreshToken` vertex | Yes | `GremlinTokenStore` in `token_store.py` — requires running ArcadeDB (Docker Compose) |
| Graph queries | Gremlin traversals for user→device relationships | Yes | `GremlinUserStore` and `GremlinFleetStore` implement Gremlin traversals via `DatabaseClient` (`db.py`) |
| Telemetry history | TelemetryReading vertices via HasTelemetry edges | No | Schema exists in nomographic; no query layer yet |

---

## Test Execution

### Running All Tests

```bash
cd nomothetic
source .venv/bin/activate
pytest
```

### Running Specific Test Groups

```bash
# Central mode tests only
pytest tests/test_central.py tests/test_auth.py tests/test_rate_limit.py

# Device mode tests only
pytest tests/test_api.py tests/test_hat.py tests/test_camera.py tests/test_audio.py

# Device auth only
pytest tests/test_pairing.py tests/test_device_auth.py

# IPC layer only
pytest tests/test_hat.py

# Mode isolation
pytest tests/test_mode.py
```

### Linting

```bash
black --check .
ruff check .
```

### Coverage

```bash
pytest --cov=nomothetic --cov-report=html
```

---

## Priority Matrix

Tests are prioritised by risk and user impact:

| Priority | Area | Rationale |
|----------|------|-----------|
| **P0** | Auth flows (register, login, refresh, token validation) | Security-critical — broken auth = full system compromise |
| **P0** | Device pairing (pair, rate limit, endpoint protection) | Security-critical — unauthenticated device access |
| **P0** | Rate limiting | Prevents brute-force attacks on auth and pairing endpoints |
| **P0** | IPC error handling (503 on daemon down) | User-facing error when hardware unavailable |
| **P1** | Fleet CRUD | Core fleet management functionality |
| **P1** | HAT/motor/servo control via IPC | Primary device control path |
| **P1** | Mode isolation | Wrong endpoints on wrong mode = confusing errors |
| **P2** | Camera/audio/calibration/streaming endpoints | Important but lower blast radius |
| **P2** | Telemetry publish and retrieval | Data integrity for fleet monitoring |
| **P2** | CORS configuration | Security — but mitigated by network isolation on device |
| **P3** | BLE integration | Not implemented yet — stub tests only |
| **P2** | ArcadeDB integration | Gremlin stores exist (`db.py`, `user_store.py`, `fleet_store.py`); integration tests against Docker ArcadeDB recommended |
