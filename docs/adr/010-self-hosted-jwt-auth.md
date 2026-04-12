# ADR-010: Self-Hosted JWT Authentication

**Status:** Accepted  
**Date:** 2026-04-10  
**Deciders:** Perceptua  

---

## Context

The nomon fleet needs user authentication for two reasons:

1. **Central mode** — a cloud-hosted nomothetic instance serving fleet
   management, device registration, and telemetry history to multiple users.
2. **Mobile app** — nomotactic connects to both device-mode and central-mode
   APIs; the central API must distinguish between users.

Device-mode endpoints (running on each Pi) currently rely on Tailscale VPN
for network-layer access control (see ADR-001). Central-mode endpoints
require application-layer authentication because they are internet-facing
and multi-tenant.

Options evaluated:

1. **Firebase Auth** — hosted, feature-rich, vendor lock-in to Google
2. **Auth0** — hosted, generous free tier, vendor lock-in
3. **Supabase Auth** — hosted, open-source server, but adds a PostgreSQL
   dependency and a separate service to operate
4. **Self-hosted JWT** — lightweight, no external dependencies beyond two
   small Python libraries (`authlib`, `bcrypt`), full control

## Decision

Use **self-hosted JWT authentication** with access + refresh token flow.

- Access tokens: HS256-signed JWT, 15-minute TTL
- Refresh tokens: opaque random string, 7-day TTL, stored hashed in ArcadeDB
- Password hashing: bcrypt (10 rounds minimum)
- JWT signing secret: `NOMON_JWT_SECRET` environment variable (required in
  central mode; not loaded in device mode)

Social login (Google, Apple) will be supported via OAuth2 authorization code
flow in a future phase. The auth module includes placeholder configuration
for OAuth2 providers but no implementation yet.

## Rationale

- **Minimal dependencies:** Two small, well-audited libraries (`authlib`,
  `bcrypt`) — no framework-level auth system to operate or upgrade.
- **No vendor lock-in:** No Google/Auth0/Supabase account required. The
  entire auth stack runs in the same process as the FastAPI server.
- **Offline-capable:** Device-mode deployments that do not use central mode
  never load the auth module. No network calls to external auth providers.
- **Fits existing patterns:** FastAPI dependency injection for `jwt_required`;
  Pydantic models for request/response; conditional import for auth deps.
- **Refresh token rotation:** Each refresh call invalidates the previous
  refresh token and issues a new one — limits blast radius of token theft.

### Known Limitations

- **Web token storage:** On web platforms, tokens are stored in
  `localStorage` (see nomotactic `lib/auth.tsx`). This is accessible to any
  JavaScript on the same origin. Mitigation: Content Security Policy headers
  will be added in a future phase to restrict script sources. On mobile
  platforms, `expo-secure-store` provides hardware-backed key storage.

## Trade-offs

- **No SSO out of the box.** Social login requires implementing OAuth2
  authorization code flow per provider. Planned as stubs now; real
  implementation deferred.
- **Secret management burden.** `NOMON_JWT_SECRET` must be set, rotated,
  and kept secure. Documented in deployment guide; validated at startup.
- **No built-in MFA.** Multi-factor auth is not included. Can be added
  later without architectural changes (TOTP library + user table column).
- **Single-instance token store.** Refresh tokens are in ArcadeDB. If the
  central DB is wiped, all sessions are invalidated. Acceptable for the
  current scale.

## Security Controls

- JWT secret must be ≥ 32 bytes; startup validation rejects shorter values
- Access tokens are short-lived (15 min) to limit exposure window
- Refresh tokens are stored as SHA-256 hashes — a DB dump does not expose
  valid tokens (the raw token has 384 bits of entropy, making brute-force
  infeasible even with a fast hash)
- Auth endpoints rate-limited per IP via sliding-window limiter in
  `rate_limit.py`: 5 req/min on login, 10 req/min on register
- `NOMON_TRUST_PROXY` env var controls whether `X-Forwarded-For` is used
  for client IP extraction (disabled by default)
- `NOMON_JWT_SECRET` must not appear in logs (security checklist R5/P5)
- CORS on auth routes uses explicit allowed origins, not `*` (checklist P11)

## Consequences

- New optional dependency group `[auth]` in `pyproject.toml`
- New module `src/nomothetic/auth.py` with `AuthService` class
- New module `src/nomothetic/auth_routes.py` with auth endpoint router
- New module `src/nomothetic/rate_limit.py` with sliding-window rate limiter
- `jwt_required` FastAPI dependency available for route protection
- Central mode requires `NOMON_JWT_SECRET` env var at startup
- Device mode ignores auth entirely — existing behaviour preserved

## Future

- OAuth2 social login providers (Google, Apple) as stub configuration
- Multi-factor authentication (TOTP)
- API key support for machine-to-machine access (management server)
- Device-mode auth if robots are exposed outside Tailscale
