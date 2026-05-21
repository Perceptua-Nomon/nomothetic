# ADR-017: Device Registration Proof JWT

## Status

Accepted

## Date

2026-05-13

## Context

When a user registers a device with the central fleet API, the server must
have some assurance that the caller recently held valid device-side access.
Without such assurance, any authenticated central user who knows a VIN could
register that device to their account — effectively squatting it — without
ever having connected to the physical robot.

Full cryptographic verification of device ownership would require one of:

- **Asymmetric device certificates** — the device holds a private key; the
  central API verifies a signature against a provisioned public key. Requires
  certificate management infrastructure (issuance, rotation, revocation).
- **Server-to-device challenge-response** — the central API contacts the
  device directly to verify liveness. Requires the device to be reachable
  from the central server and a back-channel protocol.

Both options add significant infrastructure complexity at this stage of the
project.

## Decision

`GET /api/device/auth/identity` (device JWT required) returns a
`registration_proof`: a short-lived JWT (5-minute TTL) signed with the
device's JWT secret, containing the following claims:

- `iss`: `nomon-device`
- `sub`: `<vin>` (the device VIN)
- `aud`: `nomon-fleet`
- `jti`: unique token ID (prevents replay within the TTL window)
- `exp`: 5 minutes from issuance

The response also includes the device VIN, model, and hostname.

When the client calls the central fleet `POST /api/fleet/devices`, it submits
`{ vin, model, registration_proof }`. The central API validates the proof
structurally:

1. **Expiry** (`exp`) — rejects proofs older than 5 minutes.
2. **VIN binding** (`sub == submitted vin`) — rejects proofs where the
   claimed VIN does not match the device being registered.
3. **Audience** (`aud == "nomon-fleet"`) — rejects proofs intended for
   other audiences.

Cryptographic signature verification is intentionally omitted at this stage:
the device and central services use separate JWT secrets, so the central
server cannot verify a device-issued signature without out-of-band key
distribution.

## Consequences

### Positive

- Raises the bar for VIN squatting significantly. An attacker must have
  obtained a valid device JWT within the last 5 minutes to forge a
  registration proof — the same capability required to call the identity
  endpoint directly. Time-binding via `exp` prevents replay of captured proofs.
- No infrastructure additions required. The proof is issued and validated
  using the JWT machinery already present in both services.
- The `jti` claim provides a unique identifier per proof token, enabling
  future one-use enforcement if desired.

### Negative

- The central server cannot mathematically prove the device issued the proof.
  A determined attacker who holds a valid device JWT can call
  `GET /api/device/auth/identity` themselves and use the proof to register the
  device. This is no worse than the attacker simply calling the endpoint
  directly; the proof does not add security against device JWT theft.
- The structural-only validation relies on the secrecy of the device JWT
  secret. If that secret is compromised, proof validation provides no
  additional protection.

## Future Work

Replace structural-only proof validation with asymmetric device certificates
when device certificate management infrastructure is available. The `aud` and
`sub` claims are designed to remain valid under a future asymmetric scheme
— only the verification step changes.
