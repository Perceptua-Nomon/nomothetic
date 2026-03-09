# ADR-006: Rust for HAT/Sensor Drivers in a Separate Repository

**Status:** Accepted  
**Date:** 2026-03-05  
**Deciders:** Perceptua  

---

## Context

Phase 5 introduces HAT module and sensor drivers. These drivers may require
tight-latency GPIO toggling, I2C burst transfers, or protocols with
microsecond-precision timing. The project currently uses Python for all
modules, and a Python-to-Rust conversion was evaluated for every module.

**Hardware identified:** SunFounder Robot HAT V4 on I2C bus 1, address `0x14`
(Raspberry Pi Zero 2 W, Debian GNU/Linux 13 / trixie). SPI nodes exist but
the HAT is primarily I2C. The OV5647 camera sensor occupies muxed I2C buses
10/11 at address `0x36` and must not be disturbed by HAT drivers.

Options evaluated for Phase 5 driver implementation:

1. **Python in this repo** — follow the `Camera` pattern with conditional
   imports for `spidev`, `gpiozero`, `pigpio`
2. **Rust in this repo** — mixed-language monorepo with Cargo + setuptools
3. **Rust in a separate repo** — standalone Rust daemon, communicates with
   `nomothetic.api` via local IPC

## Decision

Implement HAT/sensor drivers in **Rust in a separate repository** (`nomopractic`),
running as its own systemd service (`nomopractic.service`). Communication with
`nomothetic.api` occurs over a **Unix domain socket at `/run/nomopractic/nomopractic.sock`** using
**newline-delimited JSON (NDJSON)** framing. The localhost HTTP fallback option
is explicitly **not implemented** — the Unix socket approach is simpler, lower
overhead, and OS-enforced process isolation is sufficient.

The full IPC schema is specified in [docs/hat_ipc_schema.md](../hat_ipc_schema.md).
The Python client module design is in [docs/hat_python_client.md](../hat_python_client.md).
The Rust crate layout is documented in the nomopractic repository.

## Rationale

### Why Rust (not Python) for HAT drivers

- Python's GIL and interpreter overhead create non-deterministic latency in
  GPIO and I2C timing-critical operations — even with `pigpio` as a C backend
- Rust with `rppal` provides pure-Rust GPIO/I2C access with deterministic
  latency and memory safety, without requiring a daemon like `pigpio`
- Compiled binary footprint (~5–15 MB) is far smaller than the Python
  interpreter + dependencies required for equivalent hardware access
- No performance benefit was found for any of the existing Python modules
  (`camera`, `streaming`, `api`, `telemetry`, `updater`) — their bottlenecks
  are hardware I/O, native libraries, or network latency, not Python overhead.
  Rust conversion is only justified for the HAT driver layer

### Why a separate repo (not a mixed-language monorepo)

- **Different build artifact**: a compiled `aarch64-unknown-linux-gnu` binary
  has nothing in common with `pip install -e .`
- **Different update pipeline**: the binary is deployed via artifact download +
  SHA-256 verification + atomic file swap, not `git fetch + reset --hard`
  (the current `UpdateManager` strategy)
- **Different systemd service**: `nomopractic.service` has an independent
  lifecycle from `nomothetic.service`
- **Different release cadence**: HAT firmware may change independently of the
  Python REST API; coupling them in one repo would force unnecessary
  co-releases
- **Existing Python modules must stay together**: the `UpdateManager` OTA
  strategy (Phase 4) relies on a single-repo atomic update via
  `git reset --hard`. Splitting any Python module out would break the atomicity
  guarantee and require a dual-manifest update mechanism

### Why the Python modules are NOT split

Every Python module was evaluated for independent-repo viability:

| Module | Coupling | Split benefit |
|---|---|---|
| `nomothetic.camera` | Used by `api` + `streaming` | None — breaks dep graph |
| `nomothetic.streaming` | Depends on `nomothetic.camera` | None |
| `nomothetic.api` | Central hub; depends on `camera` | None |
| `nomothetic.telemetry` | Depends on `nomothetic.__version__` | None — too lightweight |

No Python module has external consumers or an independent release cadence.

## Interface Contract

`nomothetic.api` communicates with the `nomopractic` daemon via a **Unix domain socket
at `/run/nomopractic/nomopractic.sock`** using **newline-delimited JSON (NDJSON)** framing.

NDJSON was chosen over length-prefixed framing because:
- Text-based — debuggable with `socat` or `nc`
- No 4-byte length-field parsing required
- Messages are short (< 1 kB); savings from length-prefix are negligible

`nomothetic.api` exposes HAT operations under `/api/hat/...` endpoints that call
methods on `nomothetic.hat.HatClient`. The full schema and all methods are
specified in [docs/hat_ipc_schema.md](../hat_ipc_schema.md).

## Consequences

- A new `nomopractic` repository will be created when Phase 5 begins
- The `nomopractic` build produces a cross-compiled ARM binary (CI must include
  `cross` or equivalent ARM cross-compilation tooling)
- OTA updates for `nomopractic` use artifact-based deployment (not git-based),
  coordinated via a job document if AWS IoT Jobs is adopted (see ADR-007)
- `nomothetic.api` gains a dependency on the local IPC socket — HAT endpoints
  return `503 Service Unavailable` if `nomopractic` is not running
- Phase 5 in the roadmap is updated to reflect Rust + separate repo
