# Phase 5 Planning: Rust HAT Drivers, Repo Strategy & AWS IoT

## Summary

This document records the architectural decisions made during planning for
Phase 5 (HAT module drivers) and the future OTA update strategy. Three
questions were evaluated:

1. Would converting existing Python modules to Rust improve performance?
2. Should the project be split into multiple repositories?
3. How does AWS IoT affect the OTA update strategy?

---

## 1 — Python-to-Rust Conversion Analysis

Each module was assessed for whether Rust would deliver meaningful performance
or binary-size benefits over the current Python implementation.

| Module | Bottleneck | Rust Benefit |
|---|---|---|
| `nomothetic.camera` | libcamera (C++) via picamera2 | **None** — would be FFI to the same native code |
| `nomothetic.streaming` | Camera sensor frame rate | **None** — Flask is not the constraint |
| `nomothetic.api` | Camera hardware + network latency | **Marginal** — smaller RSS and faster cold start on Pi |
| `nomothetic.telemetry` | Network I/O + sleep interval | **None** — pure I/O wait |

### Conclusion

A full Rust conversion is not justified. The project's performance profile is
almost entirely hardware I/O-bound or delegates to native libraries (libcamera).
Python's overhead is not the limiting factor in any existing module.

The one module with a credible (though not overwhelming) Rust case is
`nomothetic.api` — running FastAPI + uvicorn + picamera2 + paho-mqtt together
consumes ~80–150 MB RSS on a 512 MB Pi Zero, whereas a Rust binary (axum)
would use ~5–15 MB. However, the cost of losing picamera2 FFI, Pydantic
models, OpenAPI auto-generation (ADR-002), and adding ARM cross-compilation
to the Windows/macOS dev workflow far outweighs the RSS savings.

### Phase 5 Exception

HAT/sensor drivers are the one area where Rust has a **genuine case**. If
hardware requires tight-latency GPIO toggling, SPI burst transfers, or
deterministic timing (microsecond-precision protocols), Python's GIL and
interpreter overhead become real constraints. Rust with `rppal` (a pure-Rust
Raspberry Pi GPIO/SPI/I2C crate) delivers memory safety and deterministic
latency without a daemon dependency like `pigpio`.

**Decision:** Phase 5 HAT drivers will be implemented in Rust in a separate
repository (`nomopractic`). See ADR-006.

---

## 2 — Repository Strategy

### Why the Python Monorepo Stays Together

None of the Python modules have external consumers or independent release
cadences — there is no benefit to splitting them into separate repositories.

The shared `Camera` dependency (`nomothetic.camera`) is used by both
`nomothetic.streaming` and `nomothetic.api`; keeping them together ensures
consistent versions without cross-repo pinning.

Updates are applied to the whole repository atomically: a single `git pull`
on the device updates all modules simultaneously. When the fleet adopts AWS
IoT Jobs for OTA (see ADR-007), a single job document will specify the version
for this repository; splitting modules would require separate job targets.

### Why the Rust HAT Code Is a Separate Repo

1. **Different build artifact** — compiled `aarch64-unknown-linux-gnu` binary,
   not a pip-installable package
2. **Different update pipeline** — binary artifact download + SHA-256 verify +
   atomic file swap (not `git reset --hard`)
3. **Different systemd service** — runs as `nomopractic.service`, independent of
   `nomothetic.service`
4. **Different release cadence** — HAT firmware changes independently of the
   Python REST API

### Final Layout

```
nomothetic/              ← Python monorepo (this repo — keep everything here)
  nomothetic.camera
  nomothetic.streaming
  nomothetic.api
  nomothetic.telemetry
  nomothetic.hat         ← IPC client for nomopractic (Phase 5)

nomopractic/          ← Separate Rust repo (Phase 5)
  Cargo.toml
  src/main.rs       ← HAT daemon
  systemd/          ← nomopractic.service unit file
  scripts/          ← OTA binary deploy script
```

### Interface Between Rust and Python

`nomothetic.api` communicates with `nomopractic` via a **Unix domain socket** at
`/run/nomopractic/nomopractic.sock`. This is the confirmed approach (see ADR-006).

The Python client is `nomothetic.hat.HatClient`. It uses **newline-delimited JSON
(NDJSON)** framing: each request and response is a single JSON object followed
by `\n`. Full schema: [docs/hat_ipc_schema.md](hat_ipc_schema.md).

The localhost HTTP fallback option is **not implemented** — the Unix socket
approach was chosen for its lower overhead, no port allocation, and
kernel-enforced process isolation.

---

## 4 — Phase 5 Milestones

### Milestone 5.1 — IPC Schema & nomopractic Scaffold ✅

**Deliverables:**
- [x] `docs/hat_ipc_schema.md` — full IPC protocol spec
- [x] `docs/hat_python_client.md` — Python `HatClient` module design
- [x] `nomopractic` repository created with `Cargo.toml`, `src/main.rs`, systemd unit
- [x] `config.rs` + `ipc/` modules scaffolded (accepts connections, echoes health response)
- [ ] CI workflow: cross-compile `aarch64-unknown-linux-gnu` binary

**Exit criteria:** `socat` health check returns `{"ok":true}` on real Pi hardware. ✅ Verified.

### Milestone 5.2 — Battery + Servo Control (P0)

**Deliverables (nomopractic Rust):**
- [x] `hat/i2c.rs` — low-level I2C read/write helpers (I2cBus trait, RppalI2c, Hat)
- [x] `hat/adc.rs` — ADC channel read command scheme (A0–A7, 10 ms async delay)
- [x] `hat/battery.rs` — `get_battery_voltage` using ADC A4, scaling × 3
- [ ] `hat/pwm.rs` — PWM register writes (REG_CHN, REG_PSC, REG_ARR) — stub only
- [ ] `hat/servo.rs` — `set_servo_pulse_us` + `set_servo_angle` with TTL lease watchdog — stub only
- [x] IPC method: `get_battery_voltage` (31 tests passing in nomopractic)
- [ ] IPC methods: `set_servo_pulse_us`, `set_servo_angle` — blocked on `hat/pwm.rs` / `hat/servo.rs`

**Deliverables (nomothetic Python):**
- [x] `src/nomothetic/hat.py` — `HatClient` with all IPC methods incl. servo & reset
- [x] `tests/test_hat.py` — mock socket tests (no hardware required)
- [x] `nomothetic.api` endpoints: `GET /api/hat/battery`, `POST /api/hat/servo`

**Exit criteria:** Mobile app can read battery voltage and command servo angle
on real Pi hardware. ⏳ Battery blocked on Pi deploy; servo blocked on nomopractic pwm/servo implementation.

### Milestone 5.3 — MCU Reset + GPIO (P1)

**Deliverables:**
- [ ] `hat/gpio.rs` — named GPIO pin map (D4, D5, MCURST, SW, LED) — stub only
- [ ] `reset.rs` — MCU reset procedure (BCM5 assert/deassert)
- [ ] IPC method: `reset_mcu`
- [x] `nomothetic.api` endpoint: `POST /api/hat/reset` — Python side done
- [ ] OTA binary deploy script (`scripts/deploy.sh`)

### Milestone 5.4 — Fleet OTA for nomopractic

**Deliverables:**
- [ ] GitHub Releases CI for `aarch64-unknown-linux-gnu` binary
- [ ] SHA-256 artifact manifest endpoint on management server
- [ ] `scripts/deploy.sh` for atomic binary swap

---

## 3 — AWS IoT for OTA Updates

### AWS IoT Jobs (Recommended Tier)

IoT Jobs is a task-dispatch service built on IoT Core (MQTT broker). The
management server publishes a job document to a device's reserved MQTT topic;
the device subscribes, executes the job, and reports status back.

**Multi-repo coordination:** A single job document can specify versions for
both the Python package and the Rust binary simultaneously:

```json
{
  "nomon_version": "0.5.0",
  "nomon_git_sha": "abc123",
  "nomopractic_version": "1.2.0",
  "nomopractic_artifact_url": "s3://...",
  "nomopractic_sha256": "def456"
}
```

This solves the mixed-version transient state problem — one job = one atomic
fleet intent.

**What changes on-device:**

| Concern | Manual (SSH + git pull) | With AWS IoT Jobs |
|---|---|---|
| Update trigger | Manual SSH | IoT Core MQTT push |
| Update mechanism | `git pull` | S3 artifact download |
| Verification | - | SHA-256 + health check |
| Multi-repo coord | Sequential SSH per repo | Job document JSON |
| Rollback | `git reset --hard` | Job failure report + script |
| Telemetry broker | Any MQTT broker | AWS IoT Core (same paho-mqtt) |
| On-device extra | Nothing | AWS IoT Device SDK (~5 MB) |

**Migration path:** `nomothetic.telemetry` already has MQTT connection logic via
`paho-mqtt`; the IoT Jobs subscription adds a second topic on the same broker
connection, using X.509 certificate auth instead of username/password.

### AWS IoT Greengrass v2 (Not Recommended)

Greengrass provides the strongest multi-repo coordination (full component
lifecycle management with deployment-level rollback), but requires a JVM
runtime that consumes ~150–250 MB RAM — a deal-breaker on Pi Zero 2W (512 MB).

**Decision:** If AWS IoT is adopted, use IoT Jobs (not Greengrass). See ADR-007.
