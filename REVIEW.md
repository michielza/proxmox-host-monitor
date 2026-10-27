# Initial review checkpoint

Review the initial implementation before adding real host credentials or exposing it through a public reverse proxy.

## Priority areas

1. Shared-key verification, login throttling, session lifetime and revocation after rotation (`monitor/app.py`, `manage.py`).
2. Same-origin/CSRF protection and cookie flags. The local HTTP exception must remain limited to loopback SSH-tunnel development.
3. Credential boundaries: secrets must not appear in GET responses, error messages, logs or browser storage. Connection edits must not silently send an existing secret to a new origin.
4. Proxmox client: fixed GET endpoint allowlist, verified certificates, no redirects, bounded requests, no host or guest mutation code.
5. Configuration validation, atomic writes and private file permissions. The config path must stay outside the checkout.
6. Browser polling: selected server only, pause on hidden tab/configuration, immediate refresh on return, no persistent metrics, gaps rather than invented data.
7. Safe text rendering of server names and labels. Test unusual punctuation and HTML-like strings.

## Current limits

- No live Proxmox compatibility test has been performed yet; API mocks and explicit demo data are not a substitute.
- Counts include only token-visible guests; templates are excluded. Storage rows can share underlying capacity.
- Physical link speed and network throughput are unavailable in this build. No historical Proxmox RRD endpoint is used.
- No separate settings administrator: possession of the shared key grants monitor configuration access.
- One Gunicorn worker is required for the in-memory throttle and configuration lock. Rate limits reset on restart and are shared by clients behind the same proxy address.
- No TLS provisioning, public reverse proxy, background fleet collector or database is included.
- Ping is Linux/iputils-specific and may be blocked even when API access works.

Return actionable findings with severity, file/line, reproduction and a suggested correction. Do not insert real host details or credentials into review comments or test fixtures.
