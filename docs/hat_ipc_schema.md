# nomopractic IPC Schema

## Overview

`nomothetic.api` (Python) communicates with the `nomopractic` daemon (Rust) through a
**Unix domain socket** using **newline-delimited JSON (NDJSON)** framing.

This document is the interface contract between the two processes. Both sides
must implement this schema exactly; changes require coordinated releases.

---

## Transport

| Property | Value |
|----------|-------|
| Mechanism | Unix domain socket (SOCK_STREAM) |
| Default path | `/run/nomopractic/nomopractic.sock` |
| Config override | `NOMON_HAT_SOCKET_PATH` env var |
| Direction | Client-initiated (nomothetic.api connects; nomopractic listens) |
| Connections | Short-lived per-request or persistent; daemon accepts multiple |

The Unix domain socket was chosen over localhost HTTP because:
- No port allocation or conflicts
- Kernel-enforced process isolation (file permissions on socket path)
- Lower overhead than TCP loopback for frequent servo/ADC calls
- Simpler to secure with filesystem ACLs (`chmod 660`, `chown root:nomon`)

---

## Framing

Each message (request or response) is a single JSON object terminated by a
`\n` (newline, U+000A). Receivers buffer bytes until `\n`, then parse the
complete JSON object.

NDJSON was chosen over length-prefixed framing because:
- Text-based — can be debugged interactively with `socat` or `nc`
- No 4-byte length-field parsing required; standard JSON libraries handle it
- Messages are short (< 1 kB); length-prefix overhead savings are negligible
- Familiar convention for streaming logs and inter-process JSON protocols

### Rules

- Each message MUST end with exactly one `\n`
- A message MUST NOT contain an embedded `\n` inside JSON string values (use `\n` JSON escape)
- Maximum message length: 4096 bytes (daemon enforces; client should not exceed)
- Encoding: UTF-8

---

## Request Envelope

```json
{"id": "req-001", "method": "get_battery_voltage", "params": {}}\n
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Caller-assigned request identifier; echoed in response |
| `method` | string | yes | Method name (see [Methods](#methods) below) |
| `params` | object | yes | Method parameters (empty object `{}` if none) |

The `id` field is opaque to the daemon. The Python client should use a
short unique identifier per request (e.g., sequential integer as string, or
short UUID prefix).

---

## Response Envelope

**Success:**
```json
{"id": "req-001", "ok": true, "result": {"voltage_v": 7.42}}\n
```

**Error:**
```json
{"id": "req-001", "ok": false, "error": {"code": "HARDWARE_ERROR", "message": "I2C read failed: EREMOTEIO"}}\n
```

| Field | Type | Always present | Description |
|-------|------|----------------|-------------|
| `id` | string | yes | Echoed from request |
| `ok` | bool | yes | `true` on success, `false` on error |
| `result` | object | when `ok=true` | Method-specific result payload |
| `error` | object | when `ok=false` | Error details |
| `error.code` | string | when `ok=false` | Machine-readable error code (see below) |
| `error.message` | string | when `ok=false` | Human-readable description |

### Error Codes

| Code | Meaning |
|------|---------|
| `UNKNOWN_METHOD` | The requested method name is not recognised |
| `INVALID_PARAMS` | One or more required params are missing or out of range |
| `HARDWARE_ERROR` | I2C/SPI/GPIO operation failed at the OS level |
| `NOT_READY` | Daemon is initialising; retry after a short delay |
| `SERVO_LEASE_EXPIRED` | Servo lease TTL elapsed — servo channel idled (pulse_us=0) until a new command is issued |
| `INTERNAL_ERROR` | Unexpected daemon error (bug) |

---

## Methods

### `health`

Returns daemon liveness and hardware connection status.

**Request:**
```json
{"id": "1", "method": "health", "params": {}}\n
```

**Response (`result`):**
```json
{
  "schema_version": "1.0.0",
  "status": "ok",
  "version": "0.1.0",
  "hat_address": "0x14",
  "i2c_bus": 1,
  "uptime_s": 3600
}
```

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | string | IPC schema semver (see [Versioning](#versioning)); client should verify on connect |
| `status` | `"ok"` \| `"degraded"` | `"ok"` if I2C link is up; `"degraded"` otherwise |
| `version` | string | nomopractic semver |
| `hat_address` | string | I2C address in use (hex string, e.g. `"0x14"`) |
| `i2c_bus` | integer | Linux I2C bus number (default `1`) |
| `uptime_s` | integer | Seconds since daemon start |

---

### `get_battery_voltage`

Reads the battery voltage via ADC channel A4 on the Robot HAT V4.

**Hardware detail:** ADC command scheme sends `(7 - channel) | 0x10` as one
byte, then reads back 2 bytes. Raw ADC value is scaled: `battery_v = raw_v × 3`.

**Request:**
```json
{"id": "2", "method": "get_battery_voltage", "params": {}}\n
```

**Response (`result`):**
```json
{
  "voltage_v": 7.42,
  "raw_adc": 24700
}
```

| Field | Type | Description |
|-------|------|-------------|
| `voltage_v` | float | Battery voltage in volts (scaled) |
| `raw_adc` | integer | Raw 16-bit ADC reading before scaling |

---

### `set_servo_pulse_us`

Sets a PWM channel to a specific pulse width in microseconds.

**Hardware detail:** Robot HAT V4 PWM controller (I2C 0x14):
- `REG_CHN=0x20`, `REG_PSC=0x40`, `REG_ARR=0x44`
- Clock: 72 MHz; servo period: PERIOD=4095; pulse width range: 500–2500 µs

**Request:**
```json
{"id": "3", "method": "set_servo_pulse_us", "params": {"channel": 0, "pulse_us": 1500, "ttl_ms": 500}}\n
```

| Param | Type | Required | Range | Description |
|-------|------|----------|-------|-------------|
| `channel` | integer | yes | 0–11 | PWM channel number |
| `pulse_us` | integer | yes | 500–2500 | Pulse width in microseconds |
| `ttl_ms` | integer | no | 100–5000 | Lease TTL (ms); servo idles if not refreshed. Default: 500 |

**Response (`result`):**
```json
{"channel": 0, "pulse_us": 1500}
```

---

### `set_servo_angle`

Convenience wrapper: converts an angle in degrees to a pulse width and calls
the PWM controller.

Mapping: `pulse_us = 500 + (angle / 180.0) × 2000`
(i.e., 0° → 500 µs, 90° → 1611 µs, 180° → 2500 µs)

**Request:**
```json
{"id": "4", "method": "set_servo_angle", "params": {"channel": 0, "angle_deg": 90.0, "ttl_ms": 500}}\n
```

| Param | Type | Required | Range | Description |
|-------|------|----------|-------|-------------|
| `channel` | integer | yes | 0–11 | PWM channel number |
| `angle_deg` | float | yes | 0.0–180.0 | Target angle in degrees |
| `ttl_ms` | integer | no | 100–5000 | Lease TTL (ms). Default: 500 |

**Response (`result`):**
```json
{"channel": 0, "angle_deg": 90.0, "pulse_us": 1611}
```

---

### `set_motor_speed`

Set a DC motor's speed as a signed percentage. IPC motor channel indices
map to the configured `[[motors]]` entries (0-based order in `config.toml`).

**Hardware detail:** TC1508S H-bridge — direction pin HIGH = forward,
LOW = reverse; PWM duty sets speed. Channels 12–15, timer group 3 (100 Hz).

**Request:**
```json
{"id": "m1", "method": "set_motor_speed", "params": {"channel": 0, "speed_pct": 50.0, "ttl_ms": 500}}\n
```

| Param | Type | Required | Range | Description |
|-------|------|----------|-------|-------------|
| `channel` | integer | yes | 0–3 | IPC motor index (position in `config.motors`) |
| `speed_pct` | float | yes | −100.0–100.0 | Signed speed: negative = reverse, 0 = stop |
| `ttl_ms` | integer | no | 100–5000 | Lease TTL (ms); motor stopped if not refreshed. Default: 500 |

**Response (`result`):**
```json
{"channel": 0, "speed_pct": 50.0}
```

**Error codes:** `INVALID_PARAMS` if channel is not configured or `speed_pct`
is out of range; `HARDWARE_ERROR` on I2C or GPIO failure.

---

### `stop_all_motors`

Immediately set all configured motor channels to zero duty (stop). Clears all
motor leases.

**Request:**
```json
{"id": "m2", "method": "stop_all_motors", "params": {}}\n
```

**Response (`result`):**
```json
{"stopped": 2}
```

| Field | Type | Description |
|-------|------|-------------|
| `stopped` | integer | Number of motors commanded to stop |

---

### `get_motor_status`

Return the currently active motor TTL leases.

**Request:**
```json
{"id": "m3", "method": "get_motor_status", "params": {}}\n
```

**Response (`result`):**
```json
{
  "active_leases": [
    {"channel": 0, "ttl_remaining_ms": 312, "conn_id": 4},
    {"channel": 1, "ttl_remaining_ms": 198, "conn_id": 4}
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `active_leases` | array | Per-motor lease entries (empty if no active leases) |
| `active_leases[].channel` | integer | IPC motor channel index |
| `active_leases[].ttl_remaining_ms` | integer | Milliseconds until auto-stop |
| `active_leases[].conn_id` | integer | Connection that holds the lease |

---

### `reset_mcu`

Asserts and de-asserts the MCU reset line to restart the Robot HAT V4
microcontroller.

**Hardware detail:** `MCURST` → BCM5 (GPIO output). The procedure is:
1. Set BCM5 low (assert reset)
2. Hold for ≥ 10 ms
3. Set BCM5 high (de-assert)

**Request:**
```json
{"id": "5", "method": "reset_mcu", "params": {}}\n
```

**Response (`result`):**
```json
{"reset_ms": 10}
```

| Field | Type | Description |
|-------|------|-------------|
| `reset_ms` | integer | Duration the reset line was held low (milliseconds) |

---

### `drive`

Set all configured DC motors to the same speed simultaneously. This is the
preferred way to drive the robot — it is atomic (no inter-motor delay) and
returns the number of motors commanded.

**Request:**
```json
{"id": "d1", "method": "drive", "params": {"speed_pct": 50.0, "ttl_ms": 500}}\n
```

| Param | Type | Required | Range | Description |
|-------|------|----------|-------|-------------|
| `speed_pct` | float | yes | −100.0–100.0 | Signed speed: negative = reverse, 0 = coast |
| `ttl_ms` | integer | no | 100–5000 | Lease TTL (ms); motors stop if not refreshed. Default: 500 |

**Response (`result`):**
```json
{"speed_pct": 50.0, "motors": 2}
```

| Field | Type | Description |
|-------|------|-------------|
| `speed_pct` | float | Commanded speed (echoed) |
| `motors` | integer | Number of motors set |

---

### `steer`

Set the steering servo to a target angle using the channel configured as
`config.servos.steering` (PicarX default: P2).

**Request:**
```json
{"id": "d2", "method": "steer", "params": {"angle_deg": 90.0, "ttl_ms": 500}}\n
```

| Param | Type | Required | Range | Description |
|-------|------|----------|-------|-------------|
| `angle_deg` | float | yes | 0.0–180.0 | Target angle (90° = straight ahead) |
| `ttl_ms` | integer | no | 100–5000 | Lease TTL (ms). Default: 500 |

**Response (`result`):**
```json
{"servo": "steering", "channel": 2, "angle_deg": 90.0, "pulse_us": 1611}
```

| Field | Type | Description |
|-------|------|-------------|
| `servo` | string | Logical servo name (`"steering"`) |
| `channel` | integer | Physical PWM channel used |
| `angle_deg` | float | Commanded angle (echoed) |
| `pulse_us` | integer | Resulting pulse width in µs |

**Error:** Returns `INVALID_PARAMS` if steering servo is not configured
(`config.servos.steering = null`).

---

### `pan_camera`

Set the camera pan (horizontal) servo to a target angle using the channel
configured as `config.servos.camera_pan` (PicarX default: P0).

**Request:**
```json
{"id": "d3", "method": "pan_camera", "params": {"angle_deg": 90.0, "ttl_ms": 500}}\n
```

| Param | Type | Required | Range | Description |
|-------|------|----------|-------|-------------|
| `angle_deg` | float | yes | 0.0–180.0 | Target angle (90° = centre) |
| `ttl_ms` | integer | no | 100–5000 | Lease TTL (ms). Default: 500 |

**Response (`result`):**
```json
{"servo": "camera_pan", "channel": 0, "angle_deg": 90.0, "pulse_us": 1611}
```

**Error:** Returns `INVALID_PARAMS` if camera_pan servo is not configured.

---

### `tilt_camera`

Set the camera tilt (vertical) servo using the channel configured as
`config.servos.camera_tilt` (PicarX default: P1).

**Request:**
```json
{"id": "d4", "method": "tilt_camera", "params": {"angle_deg": 90.0, "ttl_ms": 500}}\n
```

| Param | Type | Required | Range | Description |
|-------|------|----------|-------|-------------|
| `angle_deg` | float | yes | 0.0–180.0 | Target angle (90° = horizontal) |
| `ttl_ms` | integer | no | 100–5000 | Lease TTL (ms). Default: 500 |

**Response (`result`):**
```json
{"servo": "camera_tilt", "channel": 1, "angle_deg": 90.0, "pulse_us": 1611}
```

**Error:** Returns `INVALID_PARAMS` if camera_tilt servo is not configured.

---

### `read_grayscale`

Read all three grayscale sensor ADC channels in a single IPC round-trip.
Channel indices come from `config.sensors.grayscale` (PicarX default: A0, A1, A2).

**Request:**
```json
{"id": "d5", "method": "read_grayscale", "params": {}}\n
```

**Response (`result`):**
```json
{
  "channels": [0, 1, 2],
  "values": [12345, 8900, 14200]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `channels` | array[integer] | ADC channel numbers read (from config) |
| `values` | array[integer] | Raw 16-bit ADC readings, one per channel |

---

## Safety: Servo & Motor TTL Lease

Servos hold their last commanded position and draw stall current indefinitely
if the controller disappears. Motors would continue spinning uncontrolled.
To prevent this, every `set_servo_*` and `set_motor_speed` command carries a
**TTL (time-to-live)** parameter.

### Daemon Behaviour

1. On receiving a servo command, the daemon sets a per-channel watchdog timer
   to `ttl_ms` milliseconds.
2. If the client refreshes the command before the timer expires, the timer
   resets.
3. If the timer expires (no refresh), the daemon sends a **neutral/idle**
   command to that channel (pulse_us=0, disabling the PWM output).
4. If the **client disconnects** while a servo lease is active, the daemon
   immediately idles all leased channels on that connection.

### Recommended Client Pattern

```python
# Refresh the servo every 200 ms with a 500 ms TTL
while holding_position:
    client.set_servo_angle(channel=0, angle_deg=90.0, ttl_ms=500)
    await asyncio.sleep(0.2)
```

### Rationale

- Prevents runaway servo stall on Python crash or network disconnect
- TTL is short enough (< 1 s) to feel instantaneous on disconnect
- Daemon does not need to know application semantics — TTL is mechanical

---

## Example Session (socat debug)

```bash
# Connect to daemon socket interactively
socat - UNIX-CONNECT:/run/nomopractic/nomopractic.sock

# Type each line and press Enter:
{"id":"1","method":"health","params":{}}\n
# → {"id":"1","ok":true,"result":{"status":"ok","version":"0.1.0",...}}

{"id":"2","method":"get_battery_voltage","params":{}}\n
# → {"id":"2","ok":true,"result":{"voltage_v":7.42,"raw_adc":24700}}

{"id":"3","method":"set_servo_angle","params":{"channel":0,"angle_deg":90.0}}\n
# → {"id":"3","ok":true,"result":{"channel":0,"angle_deg":90.0,"pulse_us":1611}}

{"id":"4","method":"unknown_method","params":{}}\n
# → {"id":"4","ok":false,"error":{"code":"UNKNOWN_METHOD","message":"No method 'unknown_method'"}}
```

---

## Versioning

The IPC schema follows **semantic versioning** independent of nomon and
nomopractic application versions:

| Change | Version bump |
|--------|-------------|
| Add optional field to existing result | Patch |
| Add new method | Minor |
| Remove method, rename field, change type | Major |

The `health` response includes a `schema_version` field that the Python client
checks on connect. The client should reject connections where the major version
of `schema_version` does not match the version it was built against.

---

## Socket Permissions

The daemon creates the socket with mode `0660` and group `nomon`. The `nomon`
Linux user running the Python API must be a member of the `nomon` group:

```bash
# One-time device setup
sudo groupadd -r nomon
sudo usermod -aG nomon pi   # or whatever user runs nomothetic.api
sudo systemctl restart nomopractic
```
