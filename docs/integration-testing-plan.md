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
| **BLE simulator / mock** | End-to-end BLE GATT pairing and command tests without hardware | For on-device integration testing (see BLE section) |
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

## BLE Pairing & Encrypted Control (Phase 13/18/2)

> **Status:** Fully implemented across nomopractic (GATT server),
> nomothetic (pairing coordination), and nomotactic (BLE client).
> See nomopractic ADR-001 (GATT server), ADR-002 (binary protocol),
> and ADR-003 (security model) for design details.

### Component Inventory

| Component | Repo | Location | Status |
|-----------|------|----------|--------|
| BLE GATT server | nomopractic | `src/ble/mod.rs` | Implemented (behind `ble` feature flag) |
| Binary protocol codec | nomopractic | `src/ble/protocol.rs` | Implemented + 20 unit tests |
| Session auth + AES-CCM | nomopractic | `src/ble/session.rs` | Implemented + 8 unit tests |
| GATT service registration | nomopractic | `src/ble/services.rs` | Implemented |
| IPC bridge | nomopractic | `src/ble/bridge.rs` | Implemented + 9 mapping tests |
| WiFi provisioning (nmcli) | nomopractic | `src/ble/wifi.rs` | Implemented + 8 unit tests |
| BLE config | nomopractic | `src/config.rs` | Implemented + 6 unit tests |
| Pairing secret file | nomothetic | `src/nomothetic/pairing.py` | Implemented + 25 unit tests |
| Device auth endpoints | nomothetic | `src/nomothetic/device_auth_routes.py` | Implemented + 17 tests |
| BLE client (`RealBleService`) | nomotactic | `lib/ble.ts` | Implemented |
| Binary protocol codec (TS) | nomotactic | `lib/ble-protocol.ts` | Implemented |
| Session encryption (TS) | nomotactic | `lib/ble-session.ts` | Implemented |
| Transport layer | nomotactic | `lib/transport.tsx` | Implemented |
| Connection indicator | nomotactic | `components/ConnectionIndicator.tsx` | Implemented |

### Unit Tests (Already Passing)

#### nomopractic — Binary Protocol (`ble/protocol.rs`)

| Test | Status | Coverage |
|------|:------:|----------|
| Round-trip encode/decode for all 10 opcodes | PASS | `Heartbeat`, `GetBattery`, `SetMotorSpeed`, `StopAllMotors`, `SetServoAngle`, `Drive`, `Steer`, `ReadUltrasonic`, `ReadGrayscale`, `GetHealth` |
| Negative speed encoding (signed i16) | PASS | `set_motor_speed_negative_full` |
| Speed clamping at ±100 | PASS | `speed_clamp_extremes` |
| Servo angle maximum (180°) | PASS | `servo_angle_max` |
| Zero TTL motor | PASS | `zero_ttl_motor` |
| Zero sequence number | PASS | `zero_seq_nr` |
| Speed ×100 roundtrip | PASS | `speed_conversion_roundtrip` |
| Voltage mV conversion | PASS | `voltage_conversion` |
| Truncated input | PASS | `decode_request_too_short`, `decode_request_bad_opcode` |

#### nomopractic — Session Auth (`ble/session.rs`)

| Test | Status | Coverage |
|------|:------:|----------|
| Pair with correct secret | PASS | `pair_succeeds_with_correct_secret` |
| Pair with wrong secret | PASS | `pair_fails_with_wrong_secret` |
| Pair with different-length secret | PASS | `pair_fails_with_different_length` |
| Unique salts per session | PASS | `pair_produces_unique_salts` |
| Encrypt/decrypt roundtrip | PASS | `encrypt_decrypt_roundtrip` |
| Replay detection (counter reuse) | PASS | `replay_detection` |
| Decrypt too-short input | PASS | `decrypt_too_short` |
| TX counter increments | PASS | `tx_counter_increments` |
| Session state lifecycle | PASS | `session_state_lifecycle` |
| Nonce direction byte | PASS | `nonce_includes_direction_byte` |

#### nomopractic — Config (`config.rs`)

| Test | Status | Coverage |
|------|:------:|----------|
| BLE defaults (enabled=false, name="nomon") | PASS | `ble_config_defaults` |
| BLE config from TOML | PASS | `ble_config_from_toml` |
| Partial TOML uses defaults | PASS | `ble_config_partial_toml_uses_defaults` |
| Device name empty rejected | PASS | `ble_device_name_empty_rejected` |
| Device name too long rejected (>29 bytes) | PASS | `ble_device_name_too_long_rejected` |
| Environment overrides | PASS | `ble_env_overrides` |

#### nomothetic — Pairing (`test_pairing.py`)

| Test | Status | Coverage |
|------|:------:|----------|
| Secret has sufficient entropy (≥128 bits) | PASS | `test_generate_secret_has_sufficient_entropy` |
| Verify and consume succeeds | PASS | `test_verify_and_consume_success` |
| Consumed secret cannot reuse | PASS | `test_consumed_secret_cannot_reuse` |
| Wrong secret rejected | PASS | `test_verify_and_consume_wrong_secret` |
| Constant-time comparison | PASS | `test_verify_uses_constant_time_compare` |
| Reset clears paired state | PASS | `test_reset_clears_paired_state` |
| Reset regenerates JWT secret | PASS | `test_reset_regenerates_jwt_secret` |
| Shared secret file written | PASS | `test_shared_secret_file_written` |
| Shared secret file permissions | PASS | `test_shared_secret_file_permissions` |
| Atomic write (no partial reads) | PASS | `test_atomic_write` |
| Cross-issuer token rejection | PASS | `test_cross_issuer_rejection` |

#### nomothetic — Device Auth Endpoints (`test_device_auth.py`)

| Test | Status | Coverage |
|------|:------:|----------|
| Status before pairing | PASS | `test_status_unpaired` |
| Status after pairing | PASS | `test_status_after_pairing` |
| Pair success → tokens returned | PASS | `test_pair_success` |
| Wrong secret → 401 | PASS | `test_pair_wrong_secret` |
| Already paired → 409 | PASS | `test_pair_already_paired` |
| Rate limited → 429 | PASS | `test_pair_rate_limited` |
| Refresh success | PASS | `test_refresh_success` |
| Invalid refresh → 401 | PASS | `test_refresh_invalid_token` |
| Profile with valid token | PASS | `test_me_returns_profile` |
| Profile without token → 401 | PASS | `test_me_requires_auth` |
| Device endpoint requires auth | PASS | `test_device_endpoint_requires_token` |

### Integration Tests — Needed

These tests validate cross-component behavior that unit tests cannot cover.
They require coordinated infrastructure (BLE hardware or simulator, running
daemons, or multi-process test harnesses).

#### E2E-1: Full BLE Pairing Flow (nomopractic + nomothetic + nomotactic)

> **Priority: P0** — This is THE critical path for device setup.

```
Preconditions:
  - nomothetic running in device mode (NOMON_DEVICE_AUTH=true)
  - nomopractic running with ble.enabled=true
  - pairing_secret_path shared between both daemons

Test Sequence:
  1. nomothetic starts → generates pairing secret → writes to shared file
  2. Verify: shared file exists at pairing_secret_path with mode 0640
  3. nomotactic scans for BLE devices → discovers nomon advertisement
  4. nomotactic connects to GATT server
  5. nomotactic writes pairing secret to Pairing Secret characteristic
  6. nomopractic reads shared file → constant-time compare → derives session key
  7. nomopractic sends auth notification: salt (16B) || JWT
  8. nomotactic receives notification → derives same session key via HKDF
  9. Verify: both sides have identical 16-byte AES session keys
  10. Verify: JWT is valid (iss=nomon-device, sub=device-owner@local)
  11. Verify: pairing secret file is deleted after successful pairing

Expected Result:
  - BLE session established (both sides have session key)
  - JWT usable for HTTPS auth (if WiFi available later)
  - Pairing secret consumed (file deleted, nomothetic state cleared)
```

**Test Infrastructure Required:**
- Raspberry Pi with BlueZ OR `bluer` mock adapter (D-Bus mock)
- Both daemons running (can share a tmpdir for pairing_secret_path)
- Mobile device or BLE test client (e.g., `bluez` `gatttool` or Python `bleak`)

**Interim Testability:**
- Steps 1-2 testable now (nomothetic unit tests)
- Steps 6-7 testable now (nomopractic session unit tests)
- Steps 8-9 testable now by verifying HKDF parameters match across repos
- Full flow requires BLE hardware or D-Bus adapter mock

#### E2E-2: Encrypted BLE Command Roundtrip

> **Priority: P0** — Validates that encrypted commands reach hardware.

```
Preconditions:
  - BLE session established (from E2E-1)
  - nomopractic IPC handler running (HAT accessible or mocked)

Test Sequence:
  1. nomotactic builds GetBattery request frame (opcode=0x02, seq=1)
  2. nomotactic encrypts frame payload with session key (AES-128-CCM)
  3. nomotactic writes encrypted frame to Command Write characteristic
  4. nomopractic decrypts frame → verifies counter > last seen
  5. nomopractic bridges to IPC handler → handler.dispatch("get_battery")
  6. nomopractic encodes response (BatteryResult, opcode=0x82)
  7. nomopractic encrypts response with session key
  8. nomopractic sends notification on Command Response characteristic
  9. nomotactic decrypts response → verifies counter > last seen
  10. nomotactic decodes BatteryResult → displays voltage

Expected Result:
  - Correct battery voltage returned
  - Both TX counters incremented by 1
  - RX counters updated to match received counter values
```

**Additional Command Tests:**
| Command | Key Verification |
|---------|-----------------|
| SetMotorSpeed(ch=0, speed=50, ttl=500) | Speed encoded as i16 ×100 = 5000 |
| Drive(speed=75, ttl=1000) | Fixed-point and TTL correct |
| Steer(angle=45, ttl=500) | Angle encoded as u16 ×10 = 450 |
| StopAllMotors | Empty payload, all motors stop |
| SetServoAngle(ch=0, angle=90, ttl=500) | Servo responds correctly |
| ReadUltrasonic | Distance returned as u16 ×10 |
| ReadGrayscale | Three u16 values returned |
| GetHealth | Status byte + uptime u32 |
| Heartbeat | Echo roundtrip |

#### E2E-3: BLE Replay Attack Rejection

> **Priority: P0** — Security-critical.

```
Test Sequence:
  1. Establish BLE session
  2. Send valid encrypted command (counter=0) → succeeds
  3. Replay exact same encrypted frame (counter=0) → REJECTED
  4. Send valid command with counter=1 → succeeds
  5. Send command with counter=1 (replay) → REJECTED
  6. Send command with counter=0 (out-of-order) → REJECTED
  7. Send counter=2 → succeeds (gap of 1 is allowed)

Expected Result:
  - Steps 3, 5, 6 return CryptoError::ReplayDetected
  - Both sides maintain correct counter state
  - Security checklist B5 validated
```

#### E2E-4: WiFi Provisioning over BLE

> **Priority: P1** — Required for BLE → HTTPS upgrade path.

```
Preconditions:
  - BLE session established
  - Pi has WiFi hardware (BCM43436s)

Test Sequence:
  1. nomotactic sends WiFi Scan command (0x01) to WiFi Command char
  2. nomopractic executes `nmcli dev wifi list` → parses output
  3. nomopractic encodes scan results → writes to WiFi Result char
  4. nomotactic receives and parses scan results
  5. nomotactic sends WiFi Connect (0x02 || ssid_len || ssid || pwd_len || pwd)
  6. nomopractic executes `nmcli dev wifi connect ...`
  7. nomopractic sends connect result (0x02 || success_byte)
  8. nomotactic sends WiFi Status query (0x03)
  9. nomopractic returns current WiFi state (connected/disconnected + SSID + RSSI)

Expected Result:
  - Pi connects to specified WiFi network
  - nomotactic can then switch transport to HTTPS
  - JWT from BLE pairing is reusable for HTTPS auth
```

**Interim Testability:**
- WiFi command encoding/decoding testable now (unit tests)
- `nmcli` interaction testable on any Linux with NetworkManager
- Full flow requires WiFi AP + BLE hardware

#### E2E-5: Transport Fallback (BLE → HTTPS)

> **Priority: P1** — Validates the hybrid transport model.

```
Preconditions:
  - BLE paired, WiFi provisioned

Test Sequence:
  1. nomotactic detects WiFi connectivity → switches to HTTPS transport
  2. Commands routed via HTTPS to nomothetic → IPC → nomopractic
  3. WiFi signal lost → nomotactic detects disconnect
  4. nomotactic falls back to BLE transport automatically
  5. Commands resume via BLE binary protocol
  6. WiFi reconnects → nomotactic switches back to HTTPS

Expected Result:
  - Seamless transport switching without user intervention
  - No command loss during transitions
  - HTTPS JWT matches BLE-issued JWT
```

#### E2E-6: Concurrent BLE + HTTPS Auth Consistency

> **Priority: P2** — Ensures token interoperability.

```
Test Sequence:
  1. Pair via BLE → receive JWT (iss=nomon-device, sub=device-owner@local)
  2. Use same JWT for HTTPS request to nomothetic device API
  3. Verify: JWT accepted by nomothetic device auth middleware
  4. Refresh token via HTTPS → verify new token works on both transports

Expected Result:
  - JWT issued by nomopractic is valid for nomothetic HTTPS auth
  - Token refresh doesn't break BLE session
```

**Interim Testability:**
- Testable now by generating a JWT with the same algorithm + claims as
  nomopractic (HS256, iss=nomon-device, sub=device-owner@local) and
  verifying nomothetic accepts it. Requires shared JWT secret.

#### E2E-7: BLE Session Termination

> **Priority: P2** — Resource cleanup validation.

```
Test Sequence:
  1. Establish BLE session
  2. Client disconnects (BLE link loss)
  3. nomopractic detects disconnect → clears session state
  4. nomopractic calls on_client_disconnect(BLE_CONN_ID) on handler
  5. Verify: all motor/servo leases held by BLE connection are released
  6. Client reconnects → must re-pair (session is gone)

Expected Result:
  - No orphaned hardware leases after BLE disconnect
  - Session key zeroed from memory
  - Reconnection requires full pairing flow
```

#### E2E-8: BLE Counter Overflow

> **Priority: P3** — Edge case at session lifetime boundary.

```
Test Sequence:
  1. Establish BLE session
  2. Send 65534 commands (counter reaches 0xFFFE)
  3. Send command 65535 (counter = 0xFFFF) → succeeds
  4. Attempt command 65536 → EncryptionFailed error
  5. Verify: session must be re-established

Expected Result:
  - Counter overflow returns error (not wrap to 0)
  - No nonce reuse under any circumstances
```

**Interim Testability:** Fully testable via unit tests (set counter to
0xFFFE, attempt encrypt). Already covered in session.rs and ble-session.ts.

### Cross-Repo Consistency Checks

These are static verification tests that can run without hardware:

| Check | Can Test Now? | How |
|-------|:------------:|-----|
| Opcode values match (Rust ↔ TypeScript) | Yes | Compare `ble/protocol.rs` opcodes with `ble-protocol.ts` opcodes |
| GATT UUIDs match (Rust ↔ TypeScript ↔ docs) | Yes | Compare `services.rs`, `ble.ts`, `project-context.md` |
| HKDF parameters match (info string, key length) | Yes | Compare `session.rs` and `ble-session.ts` constants |
| AES-CCM parameters match (tag length, nonce format) | Yes | Compare both session modules |
| WiFi binary format match (command/result encoding) | Yes | Compare `wifi.rs` and `ble.ts` encode/decode |
| AAD computation match | Yes | Compare `services.rs` and `ble.ts` frame slicing |
| JWT claims match (iss, sub) | Yes | Compare `session.rs` and `device_auth_routes.py` |
| Counter replay logic match | Yes | Compare both session modules |
| Fixed-point encoding match (speed×100, angle×10, etc.) | Yes | Compare both protocol codecs |

> **Recommendation:** Create a dedicated cross-repo consistency test script
> that parses constants from both codebases and asserts equality. This
> prevents drift as either side evolves independently.

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
| Device pairing (HTTPS) | App pairs with device via `POST /api/device/auth/pair` | Yes | `lib/auth.tsx` has `pairWithDevice()`; tested in `test_device_auth.py` |
| Device token storage | Device tokens stored separately from central tokens | Partially | `lib/auth.tsx` uses `nomon_device_access_token` / `nomon_device_refresh_token` keys |
| Device token injection | `deviceApi()` sends device bearer token (not central token) | Partially | `lib/api.ts` routes tokens by base URL |
| Device token refresh | 401 on device API triggers device-specific refresh | Partially | Refresh handler in `lib/api.ts` |
| Status polling | App polls device health endpoint | Partially | `lib/api.ts` calls device endpoints; testable with mock server |
| Command dispatch | App sends motor/servo/camera commands with auth | Partially | API functions defined; needs mock device server |
| Camera streaming | App displays MJPEG stream from device | No | Requires running StreamServer or mock stream source |

### nomotactic ↔ nomopractic (BLE)

| Area | Test | Can Test Now? | Notes |
|------|------|:-------------:|-------|
| BLE scan + connect | Discover nomon by GATT service UUID, connect | No | Requires BLE hardware or D-Bus mock |
| BLE pairing | Write secret → receive auth notification → derive key | No | Individual steps unit-testable; full flow needs BLE stack |
| Encrypted commands | Send/receive AES-CCM-encrypted binary frames | No | Protocol codec and crypto unit-testable; GATT transport needs hardware |
| WiFi provisioning | Scan/Connect/Status over BLE GATT characteristics | No | Binary format unit-testable; nmcli + BLE needs hardware |
| Transport switching | BLE → HTTPS fallback and recovery | No | TransportProvider logic testable; actual switch needs both transports |
| JWT interop | BLE-issued JWT accepted by nomothetic HTTPS auth | Partially | Testable by minting JWT with matching claims/secret |

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
| Telemetry history | TelemetryReading vertices via ReadFrom edges | No | Schema exists in nomographic; no query layer yet |

---

## End-to-End Hardware Walkthrough

Step-by-step instructions for running the full BLE pairing → command → WiFi
provisioning workflow on real hardware. This covers every service across all
repos and validates the complete data path from phone to motors.

### Prerequisites

| Item | Requirement |
|------|-------------|
| **Raspberry Pi** | Pi Zero 2W (or any Pi with Bluetooth) running Raspberry Pi OS (64-bit) |
| **Robot HAT** | SunFounder Robot HAT V4 connected via I2C (address `0x14`) |
| **Dev machine** | macOS or Linux with Rust cross-compile toolchain, Python 3.11+, Node.js 18+ |
| **Phone** | Android or iOS device with Expo Go installed (same WiFi as dev machine) |
| **Network** | Pi and dev machine on the same local network (Pi via Ethernet or pre-configured WiFi) |

### Step 1 — Prepare the Pi

Ensure BlueZ, I2C, and the `nomon` system user/group are configured:

```bash
# SSH into the Pi
ssh pi@<PI_IP>

# Enable I2C (if not already)
sudo raspi-config nonint do_i2c 0

# Install BlueZ
sudo apt update && sudo apt install -y bluez

# Start and enable Bluetooth
sudo systemctl enable --now bluetooth

# Verify the adapter is up
bluetoothctl show   # should show "Powered: yes"

# Create the nomon group and state directory
sudo groupadd -f nomon
sudo useradd -r -s /usr/sbin/nologin -g nomon nomon 2>/dev/null || true
sudo mkdir -p /var/lib/nomon
sudo chown nomon:nomon /var/lib/nomon
sudo chmod 750 /var/lib/nomon
```

### Step 2 — Build and Deploy nomopractic

On the **dev machine**, cross-compile with the `ble` feature enabled:

```bash
cd nomopractic

# Install cross if needed
cargo install cross

# Build for Pi (aarch64) with BLE support
cross build --target aarch64-unknown-linux-gnu --release --features ble
```

Deploy to the Pi:

```bash
# Copy binary
scp target/aarch64-unknown-linux-gnu/release/nomopractic pi@<PI_IP>:/tmp/

# SSH in and install
ssh pi@<PI_IP> << 'EOF'
  sudo systemctl stop nomopractic 2>/dev/null || true
  sudo mv /tmp/nomopractic /usr/local/bin/nomopractic
  sudo chmod 755 /usr/local/bin/nomopractic
EOF

# Copy config (first time only)
scp config.toml pi@<PI_IP>:/tmp/config.toml
ssh pi@<PI_IP> << 'EOF'
  sudo mkdir -p /etc/nomopractic
  sudo cp /tmp/config.toml /etc/nomopractic/config.toml
EOF
```

**Enable BLE in the config** — add a `[ble]` section to
`/etc/nomopractic/config.toml` on the Pi:

```toml
[ble]
enabled = true
device_name = "nomon"
pairing_secret_path = "/var/lib/nomon/pairing_secret"
jwt_secret_env = "NOMON_JWT_SECRET"
```

Install and start the systemd service:

```bash
scp systemd/nomopractic.service pi@<PI_IP>:/tmp/
ssh pi@<PI_IP> << 'EOF'
  sudo cp /tmp/nomopractic.service /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now nomopractic
  sudo systemctl status nomopractic   # should show "active (running)"
EOF
```

### Step 3 — Install and Start nomothetic

On the **Pi**, install nomothetic in device mode:

```bash
ssh pi@<PI_IP>

cd /opt/nomothetic   # or wherever you cloned the repo

# Create venv and install with Pi extras
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[pi]"

# Generate a self-signed TLS cert (first time only)
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -keyout certs/key.pem \
  -out certs/cert.pem -days 365 -nodes \
  -subj "/CN=nomon-device"
```

Set environment variables and start:

```bash
export NOMON_API_MODE=device
export NOMON_DEVICE_AUTH=true
export NOMON_JWT_SECRET="<matching-secret>"  # must match nomopractic's JWT secret
export NOMON_TLS_CERT=certs/cert.pem
export NOMON_TLS_KEY=certs/key.pem

# Start the API server
uvicorn nomothetic.api:app --host 0.0.0.0 --port 8443 \
  --ssl-keyfile "$NOMON_TLS_KEY" --ssl-certfile "$NOMON_TLS_CERT"
```

Or install and use the systemd service for a persistent setup:

```bash
sudo cp systemd/nomothetic-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nomothetic-api
```

### Step 4 — Verify Both Daemons on the Pi

Run these checks from the Pi shell:

```bash
# 1. nomopractic IPC socket is alive
echo '{"method":"get_battery"}' | socat - UNIX-CONNECT:/run/nomopractic/nomopractic.sock
# Expected: JSON response with battery voltage

# 2. BLE adapter is advertising
bluetoothctl show | grep -A2 "Powered"
# Expected: Powered: yes

# Look for the GATT service registration in the journal
sudo journalctl -u nomopractic --no-pager -n 20 | grep -i ble
# Expected: lines showing BLE GATT server started

# 3. nomothetic HTTPS is responding
curl -sk https://localhost:8443/
# Expected: {"status":"ok"} or similar health response

# 4. Pairing secret file exists
ls -la /var/lib/nomon/pairing_secret
# Expected: -rw-r----- 1 root nomon ... pairing_secret
# Read the secret — you'll need this for the phone
sudo cat /var/lib/nomon/pairing_secret
```

**Record the pairing secret** — you will enter this in the app during Step 6.

### Step 5 — Start the Mobile App

On the **dev machine**:

```bash
cd nomotactic

# Install dependencies
npm install

# Set the device API URL to point at the Pi
export EXPO_PUBLIC_DEVICE_API_URL="https://<PI_IP>:8443"

# Start the Expo dev server
npx expo start
```

On the **phone**, open Expo Go and scan the QR code from the terminal. The app
should load and show the login/pairing screen.

> **Note:** For BLE testing, you must use a development build or Expo Go on a
> physical device — BLE does not work in simulators/emulators.

### Step 6 — BLE Pairing

1. In the app, navigate to the device connection screen.
2. Tap **Scan for devices** — the app scans for BLE peripherals advertising the
   nomon GATT service UUID (`e3a10001-1000-2000-3000-e3a1e3a1e3a1`).
3. Select the device named `nomon` from the scan results.
4. The app connects and discovers GATT services/characteristics.
5. Enter the **pairing secret** from Step 4 when prompted.
6. The app writes the secret to the Pairing characteristic — the Pi verifies it,
   mints a JWT, and sends it back via a BLE notification.
7. Both sides derive the AES-128-CCM encryption key from the JWT using
   HKDF-SHA256.

**Verify pairing succeeded:**
- The app should show a "Connected" or "Paired" status.
- On the Pi: `sudo journalctl -u nomopractic --no-pager -n 10` should show a
  successful pairing log entry.

### Step 7 — Send Commands over BLE

With BLE paired and encrypted, test the command path:

| Action in App | BLE Opcode | Expected Result |
|---------------|-----------|-----------------|
| Read battery | `0x07` GetBattery | App displays battery voltage |
| Drive forward | `0x01` SetMotorSpeed | Motors spin (verify physically) |
| Stop motors | `0x01` SetMotorSpeed (speed=0) | Motors stop |
| Read ultrasonic | `0x09` GetUltrasonic | App displays distance in cm |
| Steer servo | `0x03` SetServoAngle | Servo moves to target angle |

Each command is encrypted with AES-128-CCM. Verify on the Pi that the
counter increments by checking daemon logs:

```bash
sudo journalctl -u nomopractic --no-pager -n 30 | grep -i "counter\|decrypt\|command"
```

### Step 8 — WiFi Provisioning over BLE

1. In the app, navigate to the WiFi settings screen.
2. Tap **Scan WiFi** — the app writes a scan request to the WiFi GATT
   characteristic. The Pi runs `nmcli dev wifi list` and returns available
   networks as binary-encoded results.
3. Select a network from the scan results.
4. Enter the WiFi password when prompted.
5. The app sends a WiFi Connect command with SSID + password over BLE.
6. The Pi runs `nmcli dev wifi connect ...` and returns the connection status.

**Verify WiFi connected:**

```bash
# On the Pi
nmcli connection show --active
# Expected: the WiFi network appears with an IP address

ip addr show wlan0
# Expected: an IP on the target WiFi network
```

### Step 9 — Send Commands over HTTPS

Once the Pi is on WiFi, the app's `TransportProvider` should automatically
switch from BLE to HTTPS (or you can manually test HTTPS):

```bash
# From the dev machine — verify HTTPS is reachable over WiFi
curl -sk https://<PI_WIFI_IP>:8443/
# Expected: health response

# Send an authenticated command (use the JWT from BLE pairing)
curl -sk -H "Authorization: Bearer <JWT>" \
  -X POST https://<PI_WIFI_IP>:8443/api/hat/motor \
  -H "Content-Type: application/json" \
  -d '{"motor": 1, "speed": 50}'
# Expected: 200 OK — motor spins

# Stop the motor
curl -sk -H "Authorization: Bearer <JWT>" \
  -X POST https://<PI_WIFI_IP>:8443/api/hat/motor \
  -d '{"motor": 1, "speed": 0}'
```

In the app, send the same commands through the UI — verify the transport
indicator shows HTTPS instead of BLE.

### Step 10 — Transport Fallback

Test that the app falls back to BLE when WiFi drops:

1. **Disconnect the Pi from WiFi:**
   ```bash
   ssh pi@<PI_IP_ETHERNET> "sudo nmcli connection down <WIFI_SSID>"
   ```
2. In the app, send a command — the `TransportProvider` should detect the HTTPS
   failure and fall back to BLE.
3. Verify the command still executes (motors respond).
4. **Reconnect WiFi:**
   ```bash
   ssh pi@<PI_IP_ETHERNET> "sudo nmcli connection up <WIFI_SSID>"
   ```
5. After a short delay, the next command should route over HTTPS again.

### Step 11 — Cleanup

```bash
# On the Pi — stop services
sudo systemctl stop nomopractic nomothetic-api

# Verify no orphaned BLE advertisements
bluetoothctl show | grep "Discovering"
# Expected: no — advertising should have stopped with the daemon

# Verify IPC socket is cleaned up
ls /run/nomopractic/nomopractic.sock 2>/dev/null && echo "WARN: socket still exists"
```

### Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| BLE scan finds nothing | BlueZ not running or adapter powered off | `sudo systemctl restart bluetooth && bluetoothctl power on` |
| Pairing fails with "invalid secret" | Secret mismatch or file not readable | `sudo cat /var/lib/nomon/pairing_secret` and re-enter exactly |
| `socat` to IPC socket hangs | nomopractic not running or socket path wrong | `sudo systemctl status nomopractic` — check for crash in journal |
| HTTPS connection refused | nomothetic not running or wrong port | `sudo systemctl status nomothetic-api` — verify port 8443 |
| Motor commands return OK but nothing moves | Robot HAT not connected or I2C disabled | `sudo i2cdetect -y 1` — should show device at `0x14` |
| WiFi scan returns empty | Pi has no WiFi hardware or antenna contention | Run `nmcli dev wifi list` manually — BCM43436s shares antenna with BLE |
| Transport does not fall back to BLE | TransportProvider not detecting failure | Check app logs for timeout errors; verify BLE is still connected |
| `cross build` fails for `ble` feature | Missing `dbus` cross-compile deps | Ensure the Cross.toml has `pkg-config` and `libdbus-1-dev` for aarch64 |

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
| **P0** | BLE pairing flow (E2E-1) | Security-critical — broken pairing = no device access or unauthorized access |
| **P0** | BLE replay protection (E2E-3) | Security-critical — replay attack = unauthorized command execution |
| **P0** | Encrypted command roundtrip (E2E-2) | Core device control path over BLE |
| **P1** | WiFi provisioning (E2E-4) | Required for BLE → HTTPS upgrade; blocked without WiFi hardware |
| **P1** | Transport fallback (E2E-5) | User experience — seamless connectivity transitions |
| **P2** | BLE/HTTPS auth consistency (E2E-6) | Token interoperability across transports |
| **P2** | BLE session termination (E2E-7) | Resource cleanup — orphaned leases |
| **P2** | ArcadeDB integration | Gremlin stores exist (`db.py`, `user_store.py`, `fleet_store.py`); integration tests against Docker ArcadeDB recommended |
| **P3** | BLE counter overflow (E2E-8) | Edge case — covered by unit tests already |
