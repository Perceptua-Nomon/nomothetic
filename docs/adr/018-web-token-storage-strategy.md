# ADR-018: Web Token Storage Strategy

## Status

Accepted

## Date

2026-05-13

## Context

ADR-010 originally stored all JWT tokens (central access, central refresh,
device access, device refresh) in `localStorage` on web, acknowledging it
as an XSS risk to address later:

> On web platforms, tokens are stored in `localStorage`. This is accessible
> to any JavaScript on the same origin. Mitigation: Content Security Policy
> headers will be added in a future phase to restrict script sources.

`localStorage` is synchronous, persistent across browser sessions, and
accessible to any JavaScript executing on the same origin. An XSS
vulnerability anywhere in the application — including in a third-party
dependency — exposes all stored tokens to exfiltration. Because access tokens
are long-lived enough to make API calls and refresh tokens can be used to
obtain new access tokens, a single XSS event can result in persistent account
compromise.

The original design accepted this as a development-phase trade-off. Phase 23
resolves it.

## Decision

A three-tier storage model is applied for web:

1. **Access tokens** (central and device): **memory-only** — stored in React
   component state, never written to any browser storage. Lost on page reload;
   recovered automatically by a refresh token round-trip on app mount.

2. **Refresh tokens** (central and device): **`sessionStorage`** — tab-scoped,
   cleared when the browser tab or window is closed. Not accessible across
   tabs or after the session ends. Survives page reload within the same tab.

3. **Non-sensitive configuration** (device URL): **`localStorage`** — persistent
   across sessions. No security-sensitive data.

On native platforms (Android, iOS), all tokens continue to use
`expo-secure-store` (OS keychain / Android Keystore), which is unchanged.

## Consequences

### Positive

- Access token XSS extraction is eliminated: tokens are not written to any
  inspectable browser storage. An XSS payload cannot read access tokens from
  `localStorage` or `sessionStorage`.
- Refresh token exposure window is bounded to the browser session. Closing
  the tab invalidates the stored refresh token in the client; the server-side
  record remains valid until expiry or explicit logout, but the client cannot
  silently reuse it after the session ends.
- No new dependencies required. `sessionStorage` is a browser standard
  available in all supported environments.

### Negative

- Page reload within a tab triggers a refresh token round-trip before the
  app becomes fully functional. This is a minor latency cost (~1 network
  request on mount).
- If `sessionStorage` is unavailable or cleared (e.g., private browsing mode
  closed mid-session), the user must log in again. This is considered an
  acceptable UX trade-off for a security-conscious deployment.
- Content Security Policy headers are still recommended as a defence-in-depth
  measure, but are no longer the primary mitigation for the token storage
  concern.

## Supersedes

The "Known Limitations" point in ADR-010 regarding `localStorage` on web.
That limitation is resolved by this decision; the pending CSP mitigation is
no longer the primary remediation path for token storage exposure.
