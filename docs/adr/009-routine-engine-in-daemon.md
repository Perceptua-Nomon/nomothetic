# ADR-009: Routine Engine Lives in the HAT Daemon, Not the Fleet API

**Status:** Accepted  
**Date:** 2026-03-13  
**Deciders:** Perceptua  

---

## Context

Phase 11 introduces autonomous on-robot routines — self-contained sensor/actuator
loops that run without continuous network connectivity (e.g. the `explore`
routine: drive forward, avoid obstacles via ultrasonic, avoid cliffs via
grayscale sensors).

Two architectural placements were candidates:

1. **nomothetic (Python)** — the fleet API already orchestrates all hardware
   via IPC calls. Implement routines as async background tasks inside the
   FastAPI process that call `HatClient` methods in a loop.

2. **nomopractic (Rust)** — the HAT daemon already owns the hardware resources
   (motor lease managers, `CalibrationStore`, GPIO, PWM) and runs inside the
   same process as the drivers. Implement routines as Tokio tasks inside the
   daemon, exposed via three new IPC methods: `start_routine`, `stop_routine`,
   `get_routine_status`.

The key constraint is timing: the sensor-actuator loop must poll the ultrasonic
sensor and grayscale ADC, make avoidance decisions, and command motors
repeatedly at ~100 ms intervals. At that cadence, each iteration that goes
through the network stack (unix socket + Python event loop + IPC serialisation)
adds non-deterministic latency and a failure point.

## Decision

Implement `RoutineEngine` inside **nomopractic** (`src/routine/`). The engine
spawns a Tokio background task on `start_routine` and tracks its lifecycle.
Routines interact with hardware directly through the same driver functions used
by the IPC handler, with calibration applied from the live `CalibrationStore`.

nomothetic acts as a thin façade: it calls `start_routine`, `stop_routine`, and
`get_routine_status` over IPC, maps the results onto Pydantic models, and
returns HTTP responses — exactly the same pattern used for the motor and vehicle
APIs.

The `RoutineEngine` task is **independent of IPC client connections**: it
continues running after the REST client that started it disconnects, and can
only be halted by an explicit `stop_routine` call or by reaching `max_duration_s`.

## Rationale

**Zero network round-trips per loop iteration.** Driving motors and reading
sensors inside the daemon eliminates the unix-socket serialisation overhead on
every iteration. At 100 ms loop intervals this is not a hard real-time
requirement, but keeping the control loop synchronous within a single process
eliminates an entire class of race conditions (e.g. a motor command arriving
after the sensor read that prompted it has been superseded by the next read).

**Shared daemon resources without cross-process access.** Motor lease managers,
`CalibrationStore`, and GPIO handles are `Arc`-shared within nomopractic. Adding
a routine task that borrows these same `Arc`s is architecturally consistent and
requires no new IPC contracts for calibration data or lease ownership.

**Safety model continuity.** The TTL watchdog already idles motors when a lease
expires. Routine tasks continuously refresh their leases; if the task panics or
is aborted the watchdog idles all motors within `ttl_ms` milliseconds — the same
guarantee the IPC motor commands carry. No additional safety scaffolding is
needed.

**Survivor of network partitions.** A nomothetic-side loop would halt immediately
if the IPC socket became unavailable or the network client disconnected. A
daemon-side task is entirely decoupled from network state; the robot continues
its mission regardless.

## Consequences

**Positive:**
- No cross-process hardware access; calibrated thresholds are available to the
  routine without an IPC round-trip.
- Routine lifecycle is decoupled from network connectivity — the robot continues
  operating through REST client disconnects or network interruptions.
- Motor safety (TTL watchdog, lease cleanup) is inherited automatically by the
  routine task with no additional mechanism.
- The IPC boundary for routines is narrow and stable: three methods with simple
  request/response shapes, matching the existing IPC contract style.

**Negative:**
- Routine logic (`src/routine/`) is not directly callable from Python; end-to-end
  validation requires IPC integration tests rather than unit tests alone.
- A contributor adding a new routine must work in Rust, even if the high-level
  behaviour is straightforward.

**Future:**
- Live stat streaming (obstacles/cliffs counter increments mid-run) is not
  possible with the current request/response IPC model. This will require a new
  IPC push mechanism (e.g. a server-sent-events style subscription channel),
  tracked as Phase 12 scope.
