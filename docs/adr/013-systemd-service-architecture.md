# ADR-013: Systemd Service Architecture

**Status:** Accepted  
**Date:** 2026-04-11  
**Deciders:** Perceptua  

---

## Context

nomothetic runs two servers on each Raspberry Pi (API and MJPEG stream) and
can also run as a central fleet management server. Until now, servers were
managed via `scripts/start.sh` and `scripts/stop.sh` with PID file tracking —
adequate for development but insufficient for production:

- No automatic restart on crash or reboot.
- PID files can become stale if the process is killed externally.
- No standardised log aggregation (logs written to flat files).
- No dependency ordering between nomothetic and nomopractic.

The nomopractic daemon already ships a systemd service file
(`nomopractic.service`) that runs as `User=root` (required for I2C/GPIO)
under `Group=nomon`.

## Decision

Ship **three systemd service files** for nomothetic, covering device-mode
and central-mode deployments:

### 1. `nomothetic-api.service` (device mode)

- Runs the FastAPI/uvicorn API server on port 8443.
- `After=network.target nomopractic.service` — starts after the HAT daemon.
- `Wants=nomopractic.service` — soft dependency; API starts even if the
  daemon is unavailable (HAT endpoints return 503).
- `EnvironmentFile=-/etc/nomothetic/nomothetic.env` — dash prefix makes the
  file optional; defaults in `config.toml` suffice for basic operation.
- `Environment=NOMON_API_MODE=device` — explicit mode selection.

### 2. `nomothetic-stream.service` (device mode)

- Runs the Flask MJPEG stream server on port 8000.
- No dependency on nomopractic — the stream uses the camera directly.
- `EnvironmentFile=-/etc/nomothetic/nomothetic.env` — optional.

### 3. `nomothetic-central.service` (central mode)

- Runs uvicorn with TLS on port 443.
- `EnvironmentFile=/etc/nomothetic/nomothetic.env` — **required** (no dash
  prefix); central mode needs `NOMON_JWT_SECRET` and `ARCADEDB_*` variables.
- `Environment=NOMON_API_MODE=central` — explicit mode selection.
- TLS certificates at `/etc/nomothetic/tls/{cert,key}.pem`.
- No dependency on nomopractic — central mode has no hardware endpoints.

### Common conventions

All three services share:
- `User=nomon`, `Group=nomon` — matching `nomopractic.service`.
- `Type=simple`, `Restart=on-failure`, `RestartSec=5s`.
- `StandardOutput=journal`, `StandardError=journal` — logs go to journald.
- `WorkingDirectory=/opt/nomothetic` — default install path (adjustable).
- `WantedBy=multi-user.target`.

### Deploy integration

`scripts/deploy.sh` detects systemd availability and:
1. Copies service files to `/etc/systemd/system/` if they differ.
2. Runs `systemctl daemon-reload`.
3. Enables and restarts the device-mode services.

The start/stop scripts remain available for development and environments
without systemd.

## Consequences

**Positive:**
- Automatic restart on crash (`Restart=on-failure`).
- Automatic start on boot (`WantedBy=multi-user.target`).
- Correct startup ordering (API waits for network and HAT daemon).
- Centralised log management via journald (`journalctl -u nomothetic-api`).
- Separation of concerns: device-mode and central-mode services are
  independent units that can be enabled/disabled individually.

**Negative:**
- Three service files to maintain (mitigated by shared conventions).
- `scripts/start.sh` / `scripts/stop.sh` are redundant on systemd hosts
  but retained for development and non-systemd environments.
- Central service requires manual TLS certificate provisioning.

**Risks:**
- Service file paths (`/opt/nomothetic`, venv path) must be adjusted per
  deployment. Template comments in the files guide operators.
