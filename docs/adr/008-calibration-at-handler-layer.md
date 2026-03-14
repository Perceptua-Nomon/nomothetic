# ADR-008: Calibration Applied at IPC Handler Layer, Not HAT Driver Layer

**Status:** Accepted  
**Date:** 2026-03-13  
**Deciders:** Perceptua  

---

## Context

Phase 10 introduces a `CalibrationStore` holding runtime-adjustable values for
every hardware path: motor speed scaling, deadband, and runtime direction flip;
servo trim offsets; and grayscale sensor surface references. These values must
be applied on every command that reaches the HAT hardware.

Two architectural layers were candidates for applying calibration:

1. **Driver-layer calibration** — pass calibration values into `hat/motor.rs`,
   `hat/servo.rs`, and `hat/adc.rs`; each driver applies corrections internally
   before writing registers.

2. **Handler-layer calibration** — apply calibration in `ipc/handler.rs` before
   calling the driver functions, keeping driver interfaces unchanged.

The HAT drivers (`hat/motor.rs`, `hat/servo.rs`, `hat/adc.rs`) are
general-purpose, hardware-facing primitives: they accept concrete physical
parameters (duty cycle percentage, pulse width, ADC channel) and write the
appropriate registers. The `CalibrationStore` is a daemon-level concern held in
`Handler` behind `Arc<tokio::sync::Mutex<CalibrationStore>>`.

## Decision

Apply calibration at the **IPC handler layer** (`ipc/handler.rs`). Each
calibration-aware handler method:

1. Acquires the `CalibrationStore` mutex.
2. Copies the relevant calibration values for the operation.
3. **Drops the lock immediately** — before any hardware `.await`.
4. Calls the HAT driver with pre-adjusted parameters.

**Motor calibration** (applied in `handle_set_motor_speed` and `handle_drive`):
```
effective_speed_pct = clamp(speed_pct × speed_scale, −100.0, 100.0)
if |effective_speed_pct| < deadband_pct: effective_speed_pct = 0.0
final_reversed = calibration.reversed XOR config.motors[ch].reversed
```

**Servo calibration** (applied in `set_named_servo`, shared by `handle_steer`,
`handle_pan_camera`, `handle_tilt_camera`):
```
raw_pulse_us = angle_to_pulse_us(angle_deg)
effective_pulse_us = clamp(raw_pulse_us + trim_us, 500, 2500)
```

**Grayscale normalisation** (applied in `handle_read_grayscale_normalized`):
```
normalized_i = clamp((raw_i − white_raw_i) / (black_raw_i − white_raw_i), 0.0, 1.0)
```

The HAT drivers themselves (`set_motor_speed`, `set_servo_pulse_us`,
`read_adc`) remain completely unaware of calibration.

## Rationale

**Driver purity.** HAT drivers are hardware primitives. Embedding
`CalibrationStore` references in their signatures would couple them to the
daemon's mutable state model, break their single-responsibility contract, and
require every driver test to construct or mock a store.

**Lock safety across async boundaries.** The `CalibrationStore` mutex guard
must be dropped before any `async` hardware `.await` to avoid holding a
`!Send` guard across an await point. Handler-layer application makes this
invariant explicit and local: copy values, drop guard, await hardware. Pushing
this responsibility into driver functions would require passing the guard across
async call boundaries or using separate synchronisation — both more complex.

**Client transparency.** Clients continue sending raw semantic values
(`speed_pct`, `angle_deg`). Calibration is a daemon-internal policy. Making it
invisible at the IPC boundary means the schema never needs calibration-aware
variants, and existing clients require no changes.

**Testability.** Handler-layer calibration is fully exercised through
`Handler::dispatch` in unit tests using `MockI2c` and `MockGpio`. No driver
changes were required, so all pre-Phase 10 driver tests pass unchanged.

## Consequences

**Positive:**
- HAT driver interfaces are identical to their Phase 6–9 state.
- Lock is always dropped before any `.await` — no deadlock risk at the driver boundary.
- All calibration logic is in one layer (`handler.rs`), simple to audit or modify.
- The Phase 11 Routine Engine drives hardware through the same handler methods
  and therefore inherits calibration corrections without any engine-level changes.

**Negative:**
- Each calibration-aware handler method must explicitly snapshot the store; this
  is a manual discipline, not a type-system guarantee.
- A future contributor adding a new hardware command must remember to apply
  calibration in the handler — the compiler will not warn if this is omitted.

**Mitigations:**
- Existing handler doc comments (`///`) document the calibration application for
  each affected method, serving as a reminder and specification.
- `CalibrationStore` validation helpers (`valid_speed_scale`, `valid_deadband_pct`,
  `valid_trim_us`, `valid_grayscale`) are `pub` so the handler can validate
  inputs before locking, keeping validation logic co-located with the types.
- `save_calibration` uses `tokio::task::spawn_blocking` to avoid blocking the
  async runtime during disk write, consistent with the `set_volume` pattern
  established in Phase 9.
