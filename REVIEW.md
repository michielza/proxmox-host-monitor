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

- Live compatibility has been checked on PVE 9.2.11 from Linux/Python 3.14, including all four allowlisted endpoints and the authenticated dashboard. This does not establish compatibility with every PVE version or storage backend.
- Counts include only token-visible guests; templates are excluded. Storage rows can share underlying capacity.
- Physical link speed and network throughput are unavailable in this build. No historical Proxmox RRD endpoint is used.
- No separate settings administrator: possession of the shared key grants monitor configuration access.
- One Gunicorn worker is required for the in-memory throttle. Config transactions also use a sidecar file lock across processes. Rate limits reset on restart and are shared by clients behind the same proxy address.
- No TLS provisioning, public reverse proxy, background fleet collector or database is included.
- Ping is Linux/iputils-specific and may be blocked even when API access works.

Return actionable findings with severity, file/line, reproduction and a suggested correction. Do not insert real host details or credentials into review comments or test fixtures.

## Independent review follow-up

- **F1:** The whole read-modify-write transaction now holds a stable sidecar lock across web and CLI processes. Locking only individual reads/writes would not close the race. Key hashing occurs before locking, and rotation reads current state inside the transaction. Regression checks cover competing processes and edits made during hashing.
- **F2:** Missing, invalid or structurally malformed config at runtime returns a generic API 503 without private paths. Restoring the valid file permits requests again. Invalid config at startup still fails fast.
- **F3:** The POSIX mode assertion is a separate, platform-specific test; validation tests continue on Windows. Filesystem permission guarantees remain Linux/POSIX-specific; Windows relies on ACLs. A Windows byte-range lock supports local review tests.
- **F4:** Local HTTP uses parsed hostname matching, including IPv6 loopback, while rejecting lookalike hostnames and non-loopback addresses.
- **F5 — retained intentionally:** All shared-key holders are monitor administrators. The CA file path is an editable setting with UI value, so it remains visible after authentication. Certificate contents and API secrets remain private; no separate lower-privilege viewer role is being introduced.
- **F6:** After three consecutive failures, retries slow to 30 seconds. Success resets to 10 seconds; manual refresh and visibility return try immediately. No background collector is added.
- **F7 — retained intentionally:** HSTS remains scoped to this hostname. Automatically applying includeSubDomains could change policy for unrelated subdomains; broader policy belongs to the deployment operator.
- **F8:** `disable-demo` is available without a key prompt. Missing config produces a friendly CLI error; initialization also checks for concurrent creation inside the transaction.
- **F9:** Additional checks cover session restore/logout, host-count and label limits, ping parsing, runtime config recovery, IPv6 matching, CLI behaviour and cross-process writes.
- **F10 — retained intentionally:** Static assets keep `no-store` at this scale to avoid stale client code during early development. No caching/versioning machinery is added.

Independent re-review accepted the transaction boundaries and follow-up regressions. All 23 tests passed on Linux; Windows had 22 passes and one intentional POSIX-permissions skip.

## Live compatibility check

- PVE 9.2.11; monitor on Linux/Python 3.14.
- Dedicated privilege-separated token with exactly `Sys.Audit`, `VM.Audit` and `Datastore.Audit`.
- Node status, storage, network and cluster-resource endpoints returned data without parser errors.
- CPU model/topology, RAM, individual dir/lvmthin/pbs storage entries, guest counts and ICMP ping displayed in the authenticated dashboard.
- Modern TLS defaults rejected the older cluster CA's missing key-usage extension. Exact-node certificate trust, bootstrapped through verified SSH, passed without disabling verification or changing production certificates. Renewal requires refreshing that trust file.
- Real identities, addresses, token material, CA files and screenshots remain outside the public source tree. No real-host fixture or metrics history was committed.
## Certificate onboarding review checkpoint

The certificate-onboarding branch replaces manual trust-file installation with CA upload or explicit node-certificate approval. Inspection performs a metadata-only TLS handshake without verification, HTTP, or API credentials; it cannot itself save trust. Approval must be explicit, expires after ten minutes, and is signed and bound to the API URL and authenticated session. Test and save always use verified TLS. Hostname/IP and expiry checks remain enabled, including when an exact leaf is the trust anchor.

New or changed connections must pass all four read-only endpoint checks before saving. Failed verification preserves the prior host configuration and removes temporary trust files. Existing trust paths remain usable, and metadata-only edits do not require a reachable host. Certificate files are stored outside the source checkout, with private POSIX permissions. Old certificate files are retained for operator-controlled cleanup.

Review focus: approval binding and replay limits, credential handling before TLS verification, CA versus exact-leaf trust, concurrent edits during connection tests, and certificate renewal behavior. The browser must never auto-approve a fingerprint merely because inspection succeeded. The signed approval contains the public certificate, not API credentials.

Validation: 33 automated tests on Linux/Python 3.14; Windows/Python 3.13 runs the same suite with the POSIX permission test skipped. Generated loopback TLS servers cover CA chains, approved leaf trust, rejection of wrong identity, expiry and changed certificates, and absence of HTTP/API credentials during inspection or failed verification. Browser testing covers rejection, inspection, approval, test, save, reload and reuse. Existing configured hosts were checked read-only with unchanged private configuration. Independent review remains pending before merging this feature.

