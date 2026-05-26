# ADR-015: Plain HTTP for Soft AP Pairing

**Status:** Superseded by ADR-016 (Self-Signed HTTPS for Soft AP Mode)  
> **Note (2026-05-11):** ADR-016 was initially accepted (HTTPS+TOFU) then amended 2026-05-11 to restore plain HTTP with interface binding fix. The net effect is that the ADR-015 approach is retained with the binding fix.  
**Date:** 2026-05-09  
**Deciders:** Perceptua  

---

## Context

During initial device setup the nomon Pi broadcasts a WPA2 Soft AP hotspot
(`nomon-<last4>` SSID, see nomopractic ADR-005).  The nomotactic app connects
to this hotspot and calls `POST /api/device/auth/pair/ap` at the device's fixed
AP gateway address (`192.168.4.1`) to complete pairing.

Previously this endpoint was served by the main `nomothetic-api` service on
port 8443 with HTTPS.  The self-signed TLS certificate included `192.168.4.1`
in its Subject Alternative Name list via `NOMON_TLS_EXTRA_HOSTS`.  However,
when Tailscale is active on the device, `provision_tls_cert()` successfully
obtains a Let's Encrypt certificate via `tailscale cert`, which covers only the
device's Tailscale FQDN — not the AP IP.  The browser/app then receives
`ERR_CERT_COMMON_NAME_INVALID` when connecting to `https://192.168.4.1:8443`.

Options evaluated:

1. **Self-signed cert covering all access patterns** — generate a single
   self-signed cert whose SAN list includes both the Tailscale FQDN and
   `192.168.4.1`.  Neither access mode gets a browser-trusted cert; loses the
   benefit of Tailscale's Let's Encrypt integration.

2. **Two TLS listeners** — bind the Tailscale cert on the WiFi interface and a
   self-signed cert on `192.168.4.1`.  Requires running two uvicorn processes
   with different certs and managing them as separate services.

3. **Plain HTTP for AP mode** — serve a second API instance on port 8080
   without TLS.  The primary `nomothetic-api` service on port 8443 keeps its
   Tailscale-issued TLS cert and serves all normal (post-pairing) traffic.

## Decision

Use **Option 3**: run a dedicated `nomothetic-ap` systemd service on
`0.0.0.0:8080` without TLS, alongside the existing TLS service on port 8443.

The nomotactic app's `SOFT_AP_URL` constant is updated from
`https://192.168.4.1:8443` to `http://192.168.4.1:8080`.

## Rationale

- The Soft AP is a closed WPA2 hotspot with physical proximity as the primary
  access control: the client must know the WPA2 passphrase (= pairing secret)
  to join the AP network.  No external attacker can reach `192.168.4.1` without
  first breaking into the hotspot.
- The AP pairing flow is intentionally short-lived: after pairing the user
  provisions WiFi credentials and the device leaves AP mode.  The window of
  exposure is minutes, not hours.
- The `POST /api/device/auth/pair/ap` endpoint already enforces that the client
  IP is in the `192.168.4.0/24` subnet (nomothetic ADR-014), providing
  application-layer defence-in-depth even if the HTTP port is reachable from
  other interfaces.
- JWT tokens are still required for all protected device endpoints — HTTP
  transport does not change the application-level auth model.
- This unblocks use of Tailscale-issued (Let's Encrypt-backed, browser-trusted)
  certificates for all normal device operations on the WiFi/Tailscale interface
  without any compromise to certificate trust.

## Security Risks Accepted

The following risks are accepted and recorded for future consideration:

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|-----------|--------|------------|
| R1 | **HTTP port reachable from LAN in WiFi mode** — if port 8080 were always running, it would remain accessible from the home LAN when the Soft AP is down. | **Resolved** — `nomothetic-ap.service` has no `[Install]` section and is not boot-enabled. It is started exclusively by `ap-mode.sh up` and stopped by `ap-mode.sh down`. Port 8080 is only live while the Soft AP is active. | N/A | `ap-mode.sh` start/stop hooks + no boot enable. |
| R2 | **Plaintext API responses over AP network** — while a client is on the AP hotspot, all API traffic (including JWT tokens in responses) is in cleartext on the wireless link. | Low (requires WPA2 passphrase to join) | Medium (token interception possible for a co-present attacker) | WPA2-PSK encrypts the wireless link; short-lived pairing window. |
| R3 | **Android `usesCleartextTraffic: true` is broad** — the Android network security config flag allows cleartext HTTP to any host, not just `192.168.4.1`. | Low (app is closed-source, private) | Low (internal fleet app) | Future: replace with a targeted network security config XML via a custom Expo config plugin that restricts cleartext to `192.168.4.1` only. |
| R4 | **iOS ATS exception is limited but broad for that domain** — the ATS exception covers all ports on `192.168.4.1`. | Negligible | Low | Acceptable given AP subnet restriction at the application layer. |

## Consequences

### Positive

- Tailscale Let's Encrypt certs work on the main API with no workaround.
- AP pairing is reliable and requires no `NOMON_TLS_EXTRA_HOSTS` configuration.
- Clear separation of concerns: HTTPS for normal operation, HTTP only for
  the bootstrap handshake.

### Negative

- HTTP port 8080 is open on all interfaces (see R1).
- Android cleartext flag is broader than needed (see R3).
- One additional systemd service to manage.

## Future

- **Targeted Android network security config**: create a custom Expo config
  plugin that generates `res/xml/network_security_config.xml` allowing cleartext
  only for `192.168.4.1`, replacing the global `usesCleartextTraffic` flag.
  This resolves R3.
- **Trusted certs everywhere**: once Tailscale certs are issued for all access
  paths, retire the HTTP AP service entirely and revert to HTTPS-only.
