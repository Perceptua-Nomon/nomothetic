# ADR-001: Self-Signed TLS Certificates for HTTPS

**Status:** Accepted  
**Date:** 2024-02-17  
**Deciders:** Perceptua  

---

## Context

The nomon REST API must use HTTPS to protect camera control commands and responses in transit. Each Raspberry Pi node needs a TLS certificate. Options evaluated:

1. **Self-signed certificate** — auto-generated on first run, no CA required
2. **Let's Encrypt** — free, trusted, requires a public domain and ACME challenge
3. **Private CA** — centrally issued certs, requires maintaining a CA infrastructure
4. **No TLS** — HTTP only, rely entirely on Tailscale for encryption

## Decision

Use **auto-generated self-signed certificates** stored in `.certs/cert.pem` and `.certs/key.pem`.

## Rationale

- Pi nodes operate on private networks (Tailscale VPN) — no public domain is required or available
- Tailscale already provides one layer of network-level encryption; TLS adds a second
- Auto-generation on first run requires zero manual setup per device
- The `cryptography` package (already a dependency) handles RSA 2048 + SHA-256 cert generation
- Self-signed certs are valid for 10 years — no rotation complexity for private deployments
- Mobile clients can be instructed to trust the cert on first use (TOFU) or pin it

## Trade-offs

- Clients must explicitly trust or bypass the self-signed cert warning (use `-k` with curl)
- Not suitable for public-facing deployments without replacement with a CA-signed cert
- No automatic revocation/rotation mechanism

## Future

Replace with proper CA-signed certs if devices are ever exposed publicly. The `cert_dir` parameter on `APIServer` allows dropping in replacement certs without code changes.

**Note (Phase 22 / ADR-016 amended 2026-05-11):** AP mode uses plain HTTP bound
to `192.168.4.1:8080` — no dedicated AP certificate. The initial Phase 22
HTTPS+TOFU design (separate AP self-signed cert, bootstrap service) was reverted.
See ADR-015 and ADR-016 for history.
