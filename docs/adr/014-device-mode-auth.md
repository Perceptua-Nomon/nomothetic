# ADR-014: Device-Mode Authentication

## Status

Accepted

## Context

Device-mode nomon endpoints (camera, motor, sensor, audio, calibration,
routine) are served over HTTPS on the local network.  Prior to this
change they relied solely on network-level access control (Tailscale VPN
or private LAN).  A user connecting from the nomotactic mobile app had
no application-layer identity — any client on the network could drive
the robot.

We need a lightweight, zero-infrastructure auth mechanism that:

1. Works offline (no internet or central server required).
2. Does not require the user to create an account before first use.
3. Supports exactly one owner per device (single-user model).
4. Issues short-lived JWT access tokens for API calls.
5. Can be disabled for development or trusted-network deployments.

## Decision

### Pairing-Secret Flow

On first boot (or after a factory reset), the nomothetic device service
generates a random 128-bit pairing secret and logs it to the systemd
journal / console:

```
INFO  DEVICE PAIRING SECRET: <22-char-url-safe-string>
```

**Persistence:** the secret is written to `/var/lib/nomon/pairing_secret`
(`0640 nomon:nomon`) so that it survives service restarts and mode
switches.  The directory `/var/lib/nomon` must be owned by `nomon:nomon`
with mode `0750` for the write to succeed; if the write fails, a `WARNING`
is logged and the in-memory secret is used for the current session
(meaning the secret changes on next restart).  On subsequent boots the
same secret is reused — the device keeps the same pairing code until an
explicit factory reset.

The device owner reads this secret (physically or via SSH) and enters it
in the nomotactic app's pairing prompt.  The app calls
`POST /api/device/auth/pair` with the secret and a display name.

On success:

- A local user (`device-owner@local`) is created in an in-memory store
  with a random password hash (never used directly).
- JWT access and refresh tokens are issued with issuer `nomon-device`.
- The pairing secret is consumed (single-use) — subsequent attempts
  return 409.
- The pairing endpoint is rate-limited to 3 requests/minute per IP.

### Auto-Generated JWT Secret

Each device manages its JWT signing secret via `DeviceJwtSecretStore`
(`nomothetic.device_jwt`), which persists the secret on disk so that it
survives service restarts and AP → Wi-Fi mode switches.

**File:** `/var/lib/nomon/device_jwt_secret`  
**Permissions:** `0600 nomon:nomon`  
**Write method:** atomic — `tempfile.mkstemp` + `os.rename`  
**Fallback:** if `/var/lib/nomon/` is absent at write time, a `WARNING` is
logged and an in-memory secret is used; the service starts normally.  
**Rotation:** `DeviceJwtSecretStore().rotate()` generates and persists a new
secret, invalidating all existing JWTs.  `PairingState.reset()` (factory
reset) calls `rotate()` automatically.  
**Cross-mode persistence:** both `nomothetic-ap.service` (AP mode) and
`nomothetic-api.service` (Wi-Fi mode) load from the same on-disk secret.
JWTs issued during AP pairing remain valid after the device transitions to
Wi-Fi mode — no re-pairing required.

### Issuer Separation

Device-mode tokens use issuer `nomon-device`; central-mode tokens use
`nomon-central`.  The AuthService `verify_token` method validates the
issuer claim, preventing a token obtained from one mode being accepted
by the other.

### Opt-Out

Setting `NOMON_DEVICE_AUTH=false` disables all device auth:

- No pairing endpoints are registered.
- No JWT dependency is added to device endpoints.
- A warning is logged.

This preserves backward compatibility and supports trusted-network
deployments where Tailscale or firewall rules provide access control.

### Protected Endpoints

When device auth is enabled, all `/api/*` device endpoints require a
valid JWT bearer token.  The health endpoint (`GET /`) remains
unauthenticated for monitoring and load-balancer health checks.

The device auth endpoints (`/api/device/auth/*`) handle their own
authentication: `/status` and `/pair` are unauthenticated (by design),
`/me` requires a JWT, and `/refresh` validates the refresh token.

## Consequences

### Positive

- Zero infrastructure — works on an isolated Pi with no internet.
- Single pairing step replaces account creation + login.
- Rate limiting protects against brute-force pairing attempts.
- Issuer separation prevents cross-mode token reuse.
- Opt-out available for development and trusted networks.

### Negative

- Single-owner model — no multi-user access control on the device.
- Tokens are preserved across service restarts (JWT secret persisted to disk
  via `DeviceJwtSecretStore`); invalidated only when the secret file is
  deleted or `PairingState.reset()` (factory reset) is called.
- Pairing secret must be communicated out-of-band (console/SSH).
- JWT secret is persisted to disk via `DeviceJwtSecretStore` (Phase 22);
  pairing user record and consumed-secret flag remain in-memory (lost on restart).

### Future Considerations

- Persist pairing user state to disk for full restart resilience (JWT secret
  is now persisted via `DeviceJwtSecretStore` — Phase 22; pairing user record
  and consumed-secret flag remain in-memory).
- QR code display on device screen for easier pairing.
- Multi-user device access with role-based permissions.
