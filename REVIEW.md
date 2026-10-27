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


## PR 2 independent review follow-up

Reika approved the feature with two low-priority and four informational findings and independently passed the original 33 tests. Follow-up disposition:

- F1: inspection, tests and connection-changing saves now share a 12/session/minute and 60/process/minute throttle. Auth and CSRF run before throttling; blocked requests do not contact targets. Offline metadata edits and normal stats polling are unaffected. This bounds bursts, not destination authorization: shared-key holders remain trusted administrators. Limits are in-memory and per process; the supplied service has one worker.
- F2: README explicitly documents that every custom trust file, including legacy paths, uses explicit trust anchors and accepts intermediate-only chains. Hostname and expiry validation remain enabled.
- F3: retained intentional API distinction: a test returns a completed check result (`ok=false`), while a failed save returns HTTP 502. Both display the same safe upstream error to the operator and never turn a Proxmox 401 into website logout.
- F4: invalid/expired approved node certificates now report a node-certificate-specific error; CA upload errors remain specific to CA bundles.
- F5: strict validation retained and documented: every certificate in an uploaded CA bundle must be valid and unexpired.
- F6: retained operator-controlled cleanup of old certificate files; no automatic deletion added.

Three additional regression tests cover the shared throttle, global limit across sessions and leaf-specific errors. The resulting suite has 36 tests. No changes to approval binding, trust verification or credential routing were made after independent review.
## Ping reporting fix

The original ping helper passed Linux arguments to the Windows preview and attempted to launch a capability-bearing ping executable inside a service with an empty capability bounding set. Both errors became a null latency and the misleading text "No response".

The replacement uses unprivileged ICMP datagram sockets through pinned icmplib on Unix, and the system ping executable with Windows-specific arguments and a numeric destination on Windows. It never uses a shell, grants raw-socket capabilities, changes sysctls, or installs a privileged helper. Kernel permission restrictions produce an explicit local error. Probe errors are separate from API errors so CPU/RAM/storage collection and connection validation remain usable without ICMP.

Windows verification: 42 tests ran, with the POSIX file-permission test and real Linux ICMP integration test skipped. A real configured host also produced RTTs on the development status page. Tests cover one unprivileged echo, no executable on Linux, timeout versus local permission/lookup/execution errors, Windows arguments and localized RTT formats, numeric destination handling, and partial metric preservation. Reika independently approved PR #3 with no blockers and four informational findings. Her fresh Windows run passed 40 tests with two platform skips, and her Linux run passed all 42 tests, including real unprivileged IPv4/IPv6 loopback ICMP. She also checked real Windows probes to loopback, a resolved hostname and an unanswered test address. Final verification inside the deployed service sandbox is a deployment check.


PR #3 finding disposition: F1 (additional RTT locales), F3 (first-address dual-stack selection) and F4 (legacy Windows exit codes) are accepted compatibility limits and documented in README. F2 is addressed with the dependency license notice and upstream license link. No runtime code changed after independent review.

## IP HTTPS deployment review checkpoint

Configuration-only proposal: bootstrap nginx serves ACME challenges without proxying the application, then the HTTPS site uses a browser-trusted IP certificate and fixed Host/redirect values. Gunicorn stays on loopback; the app drop-in disables plaintext tunnel mode, enables secure cookies and limits trusted scheme forwarding to the loopback proxy. nginx overwrites scheme headers and removes alternate scheme headers supplied by clients. No application code or private host configuration changes are proposed.

Certbot 5.8.0 uses an isolated virtual environment because older distribution clients may lack IP-webroot support. The hourly timer targets only the monitor's certificate lineage. Renewal reloads nginx only after a configuration check; staged certificate tests must not replace the trusted production certificate. No DNS access is needed, and no real IP address or certificate belongs in this repository.

Development evidence: an extracted distribution nginx 1.28.3 binary passed configuration validation. Loopback-only integration checked verified test TLS with a generated local CA, fixed redirects, GET-only challenge handling, bootstrap isolation, rejection of direct plaintext app requests, Secure/HttpOnly/SameSite cookies, an authenticated host-list read, spoofed alternate scheme headers, and cross-origin rejection. The separate test app retained production's service restrictions, including PrivateTmp. Temporary units were stopped afterward; checksums confirmed that the production unit and private configuration stayed unchanged. Renewal unit syntax was also checked. No public listeners or ACME accounts were created for these checks.

Independent review focus: bootstrap-to-HTTPS ordering and rollback, fixed authority/redirect behavior, scheme-header trust, cookie and origin handling, certificate issuance/renewal commands, and timer/hook reliability. Login throttling currently shares nginx's loopback address; no client-IP middleware is introduced. Independent review and the real public issuance/renewal tests are pending before deployment.

## PR 4 independent review follow-up

Reika approved the configuration-only proposal with one low and four informational findings. F1 is addressed by documenting that HSTS does not apply to IP literals and that the HTTPS URL should be used directly; an HTTP redirect is not downgrade protection for an initial insecure request. F2's operational concern is accepted with daily journal/expiry checks until independent monitoring exists. Its suggested expiry-email remedy is corrected: Let's Encrypt stopped expiry notification emails in 2025, so registration does not restore them. F3 documents IPv4-only listeners and coordinated future IPv6/DNS rollout. F4 is an explicit installed-binary option check before issuance. F5 retains the existing 64 KiB body limit. No proxy or application runtime behavior changed after independent review. Public ACME validation, issuance, renewal-hook testing and final activation remain deployment checks.
