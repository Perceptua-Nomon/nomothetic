# ADR-019: Plugin Authentication via Ed25519 Challenge-Response

**Status:** Accepted
**Date:** 2026-06-18
**Deciders:** Perceptua

---

## Context

On-device autonomy plugins (the `autonomon` brain, see autonomon ADR-004) call
the device's nomothetic REST API for sensor reads and actuator commands. Those
endpoints sit behind `jwt_required`, so a plugin needs a device JWT.

The deploy process must give the plugin a credential. The naive approach —
mint a JWT during deploy and write it to an on-device env file — puts a
long-lived bearer token on disk and bakes its lifecycle into the deploy. We
wanted a scheme where **only an on-device plugin process can obtain a token**,
no token sits on disk, and the source being open does not help an attacker.

This mirrors the existing device-auth model (ADR-014): pairing proves physical
presence; here, possession of an on-device private key proves plugin identity.

## Decision

**A plugin authenticates by proving possession of an Ed25519 private key that
was generated on the device and never leaves it. nomothetic stores only the
public key and issues a short-lived device JWT in exchange for a signature over
a server nonce.**

### Protocol

1. **Register (deploy-time, localhost only):** the deploy script generates an
   Ed25519 keypair on the Pi and `POST`s the *public* key to
   `POST /api/plugin/register`. nomothetic accepts it only from the loopback
   interface and stores it at `/var/lib/nomon/plugin_keys/<plugin>.pub`.
2. **Challenge (runtime):** `GET /api/plugin/challenge?plugin=<name>` returns a
   single-use, short-lived (30 s) nonce for a registered plugin.
3. **Token (runtime):** the plugin signs the nonce and `POST`s
   `{plugin, nonce, signature}` to `POST /api/plugin/token`. nomothetic verifies
   the signature against the stored public key, consumes the nonce, and returns
   a 60-minute device JWT (`sub = "plugin:<name>"`).

The `autonomon` client (`PluginTokenAuth`, an `httpx.Auth`) runs steps 2–3 on
the first request and again automatically on any `401`, so a long-running
pipeline survives token expiry transparently.

### Security properties

- **Open protocol, secret key.** Security rests on possession of the private
  key, not protocol secrecy (same as SSH/TLS). Publishing the source does not
  help an attacker.
- **Registration is localhost-only.** A remote caller cannot register a key, so
  it cannot enrol an impersonating identity.
- **Registration is key-stable.** Re-registering the *same* key is a no-op
  (idempotent redeploys); a *different* key for an already-registered plugin is
  rejected (409) — blocking key-swap attacks. Rotating requires deleting the
  stored `.pub` on the device.
- **Nonces are single-use and short-lived**, so a captured token request cannot
  be replayed.
- **Tokens are device-scoped by construction.** The JWT is signed with this
  device's JWT secret (ADR-016), so a key extracted from one Pi yields a token
  no other device in the fleet will accept. One compromised Pi does not cascade.

## Rationale

**Why Ed25519 challenge-response over a static token on disk?**
A static token is a bearer secret at rest with a long life; anyone who reads the
env file owns the API. Challenge-response keeps only a private key on disk and
issues short-lived tokens on demand — and the private key, unlike a JWT, is
never transmitted, so it cannot be skimmed off the wire during deploy.

**Why localhost-only registration instead of admin-authenticated?**
The deploy step runs on the device; loopback is a property an attacker cannot
forge remotely, and it needs no shared admin secret in the deploy path. Combined
with key-stability, it blocks both remote enrolment and key swapping.

**Why reuse the device JWT secret rather than a separate plugin secret?**
It makes plugin tokens automatically device-scoped and lets `jwt_required`
accept them unchanged — no second verification path. Re-pairing rotates the
secret and invalidates outstanding plugin tokens, but the plugin re-acquires on
its next `401`, so this is self-healing.

**Why Ed25519 (not RSA/ECDSA)?** Small keys, fast verification on a Pi Zero 2W,
no curve/padding choices to get wrong, deterministic signatures.

## Trade-offs

| Benefit | Cost |
|---------|------|
| No bearer token at rest; only a private key | New crypto surface (key store, nonce store, signature verify) |
| Only an on-device process can obtain a token | Deploy must register the key while nomothetic is up |
| Tokens device-scoped automatically | Re-pair invalidates live plugin tokens (mitigated by refresh-on-401) |
| Open-source-safe | Operators must delete the `.pub` to rotate a key |

## Alternatives Considered

- **Static JWT written to an env file during deploy.** Rejected: long-lived
  bearer secret at rest; token crosses the wire at deploy; lifecycle coupled to
  deploys.
- **Pairing-secret exchange.** Rejected: overloads the Wi-Fi AP pairing secret
  (ADR-014/nomopractic ADR-005) with a second purpose and couples plugin auth to
  its rotation.
- **mTLS client certificates.** Rejected for now: strongest option, but adds
  client-cert verification to the FastAPI/TLS stack and more cert-expiry ops than
  this milestone warrants. Could supersede this ADR later.

## Consequences

- New nomothetic modules `plugin_auth.py` (key store, nonce store, signature
  verify) and `plugin_auth_routes.py` (register/challenge/token), wired into the
  device-mode app alongside device auth.
- `AuthService.create_plugin_token()` issues the `plugin:<name>` JWT.
- `autonomon` gains `plugin_auth.py` (keygen, signing, `PluginTokenAuth`, a
  deploy CLI) and prefers `NOMON_PLUGIN_KEY` over `NOMON_PLUGIN_TOKEN`.
- `autonomon/scripts/deploy.sh` generates the key, writes the plugin env file
  (no token), and registers the public key over loopback.
- Proposals to add an *interpretation* endpoint to nomothetic are still governed
  by autonomon ADR-004; this ADR only adds raw auth plumbing, not cognition.

## References

- ADR-014: Device-Mode Authentication (pairing model this mirrors)
- ADR-016: Persisted device JWT secret (what plugin tokens are signed with)
- ADR-010: Self-hosted JWT auth (`AuthService`)
- autonomon ADR-004: autonomon is the brain (the plugin consuming this auth)
