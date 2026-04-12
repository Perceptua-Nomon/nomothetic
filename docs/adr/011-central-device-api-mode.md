# ADR-011: Central vs Device API Mode

**Status:** Accepted  
**Date:** 2026-04-10  
**Deciders:** Perceptua  

---

## Context

nomothetic currently runs exclusively on Raspberry Pi devices, exposing
hardware control, camera, audio, and streaming endpoints over HTTPS. Phase 13
introduces a **central API** that serves fleet management, user authentication,
and device registration to internet-facing clients.

Two deployment strategies were evaluated:

1. **Separate codebases** — a new `nomothetic-central` repository with its own
   FastAPI app, models, and test suite. Shares nothing with the device-mode API.
2. **Single codebase, config-driven mode** — the same `nomothetic` package runs
   in either `device` or `central` mode based on a configuration flag. Each mode
   registers a different set of route groups. Shared utilities (Pydantic models,
   error handling, middleware) are reused.
3. **Single codebase, all routes always loaded** — every endpoint is always
   registered; hardware routes return 503 on the central server; auth routes
   return 404 on devices.

## Decision

Use a **single codebase with config-driven mode selection** (option 2).

- `NOMON_API_MODE` environment variable: `device` (default) or `central`
- `src/nomothetic/mode.py` exports a `Mode` enum and a `get_mode()` function
- `create_app()` conditionally registers route groups based on the active mode
- Shared code (Pydantic base models, error handlers, CORS config, health
  endpoint) is loaded in both modes

### Route Registration by Mode

| Route group | Device mode | Central mode |
|-------------|-------------|--------------|
| Health (`/`) | ✅ | ✅ |
| Camera (`/api/camera/*`) | ✅ | ❌ |
| HAT (`/api/hat/*`) | ✅ | ❌ |
| Vehicle (`/api/drive`, `/api/steer`, etc.) | ✅ | ❌ |
| Sensor (`/api/sensor/*`) | ✅ | ❌ |
| Stream (`/api/stream/*`) | ✅ | ❌ |
| Audio (`/api/audio/*`) | ✅ | ❌ |
| Calibration (`/api/calibration/*`) | ✅ | ❌ |
| Routine (`/api/routine/*`) | ✅ | ❌ |
| Device Auth (`/api/device/auth/*`) | ✅ | ❌ |
| Auth (`/api/auth/*`) | ❌ | ✅ |
| Fleet (`/api/fleet/*`) | ❌ | ✅ |

## Rationale

- **Code reuse:** Pydantic models, error handling patterns, CORS config, and
  test utilities are shared. No duplication across repos.
- **Single CI pipeline:** One test suite covers both modes via test fixtures
  that set `NOMON_API_MODE` before creating the app.
- **Deployment flexibility:** The same Docker image or pip install can run as
  either a device API or a central API — only the env var changes.
- **Conditional imports preserved:** Hardware-only libraries (picamera2,
  pyaudio, rppal socket) are only imported when device-mode routes are
  loaded. Central mode never attempts to import Pi-specific code.
- **Clean separation:** No "dead" endpoints returning 503 on the wrong
  server. Each mode only exposes the routes it supports.

## Trade-offs

- **Mode-specific test fixtures.** Tests must create the app in the correct
  mode. Addressed with a `@pytest.fixture(params=["device", "central"])`
  pattern for shared tests, plus mode-specific test modules.
- **Risk of accidental cross-mode imports.** A central-mode module
  accidentally importing `nomothetic.camera` would fail on x86. Mitigated by
  keeping mode-specific route registration in clearly separated functions.
- **OpenAPI docs differ per mode.** The `/docs` page shows different
  endpoints depending on which mode is running. This is intentional — each
  deployment documents exactly what it exposes.

## Device-Mode Security Boundary

**Updated (Phase 17 — ADR-014):** Device-mode endpoints now have opt-in
JWT authentication via a pairing-secret flow.

When `NOMON_DEVICE_AUTH=true` (the default), all `/api/*` device endpoints
require a valid JWT bearer token.  The device owner pairs once using a
128-bit secret displayed at startup, which issues device-scoped JWTs
(issuer `nomon-device`).  See ADR-014 for the full pairing lifecycle.

Key properties:

- **Single-owner model** — exactly one user pairs with the device.
- **Issuer isolation** — device tokens (`nomon-device`) are rejected by
  central mode (`nomon-central`) and vice versa.
- **Rate-limited pairing** — 3 attempts/minute per IP.
- **Opt-out** — setting `NOMON_DEVICE_AUTH=false` disables JWT requirements
  on device endpoints and suppresses pairing route registration, restoring
  the pre-Phase-17 behaviour.  A warning is logged.

Network-layer controls (Tailscale VPN, firewall rules) remain recommended
as defence in depth, but are no longer the sole access control mechanism.

## Consequences

- New module `src/nomothetic/mode.py`
- `NOMON_API_MODE` env var added to config template
- `create_app()` gains mode-aware route registration
- Central mode requires `[auth]` optional dependency; fleet routes use
  in-memory storage (ArcadeDB integration deferred to a future phase)
- Device mode behaviour is unchanged — all existing endpoints work as before

## Future

- Both modes could run simultaneously on different ports if needed (unlikely)
- Central mode may gain admin endpoints (user management, fleet analytics)
- ~~Device mode may gain local auth if Tailscale is removed (see ADR-010)~~
  — Implemented in Phase 17 (ADR-014): pairing-secret JWT auth with
  `NOMON_DEVICE_AUTH=false` opt-out
