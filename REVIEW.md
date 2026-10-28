# Review record and current limits

Independent reviews and deployment verification are recorded below. The priority areas remain a checklist for future changes. Initial checkpoint details are retained as history; later follow-ups describe the current implementation.

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
- Reviewed nginx and IP-certificate issuance/renewal templates are included; deployment requires operator configuration and validation. No background fleet collector or database is included.
- Linux uses unprivileged icmplib probes; Windows uses its system ping executable. OS permissions or ICMP filtering can prevent ping even when API access works. Windows locale and dual-stack selection limits are documented in README.

Return actionable findings with severity, file/line, reproduction and a suggested correction. Do not insert real host details or credentials into review comments or test fixtures.

## Independent review follow-up

- **F1:** The whole read-modify-write transaction now holds a stable sidecar lock across web and CLI processes. Locking only individual reads/writes would not close the race. Key hashing occurs before locking, and rotation reads current state inside the transaction. Regression checks cover competing processes and edits made during hashing.
- **F2:** Missing, invalid or structurally malformed config at runtime returns a generic API 503 without private paths. Restoring the valid file permits requests again. Invalid config at startup still fails fast.
- **F3:** The POSIX mode assertion is a separate, platform-specific test; validation tests continue on Windows. Filesystem permission guarantees remain Linux/POSIX-specific; Windows relies on ACLs. A Windows byte-range lock supports local review tests.
- **F4:** Local HTTP uses parsed hostname matching, including IPv6 loopback, while rejecting lookalike hostnames and non-loopback addresses.
- **F5 — initial disposition, superseded by certificate onboarding:** All shared-key holders remain monitor administrators. The original editable CA path was replaced by certificate trust choices and upload/approval; the backend now chooses certificate paths. Existing saved paths remain usable and visible after authentication. Certificate contents and API secrets remain private; no separate lower-privilege viewer role is introduced.
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
- Modern TLS defaults rejected the older cluster CA's missing key-usage extension. Exact-node certificate trust, bootstrapped through verified SSH, passed without disabling verification or changing production certificates. Renewal requires fresh inspection and explicit approval of the changed node certificate.
- Real identities, addresses, token material, CA files and screenshots remain outside the public source tree. No real-host fixture or metrics history was committed.
## Certificate onboarding review checkpoint

The certificate-onboarding branch replaces manual trust-file installation with CA upload or explicit node-certificate approval. Inspection performs a metadata-only TLS handshake without verification, HTTP, or API credentials; it cannot itself save trust. Approval must be explicit, expires after ten minutes, and is signed and bound to the API URL and authenticated session. Test and save always use verified TLS. Hostname/IP and expiry checks remain enabled, including when an exact leaf is the trust anchor.

New or changed connections must pass all four read-only endpoint checks before saving. Failed verification preserves the prior host configuration and removes temporary trust files. Existing trust paths remain usable, and metadata-only edits do not require a reachable host. Certificate files are stored outside the source checkout, with private POSIX permissions. Old certificate files are retained for operator-controlled cleanup.

Review focus: approval binding and replay limits, credential handling before TLS verification, CA versus exact-leaf trust, concurrent edits during connection tests, and certificate renewal behavior. The browser must never auto-approve a fingerprint merely because inspection succeeded. The signed approval contains the public certificate, not API credentials.

Validation: 33 automated tests on Linux/Python 3.14; Windows/Python 3.13 runs the same suite with the POSIX permission test skipped. Generated loopback TLS servers cover CA chains, approved leaf trust, rejection of wrong identity, expiry and changed certificates, and absence of HTTP/API credentials during inspection or failed verification. Browser testing covers rejection, inspection, approval, test, save, reload and reuse. Existing configured hosts were checked read-only with unchanged private configuration. The independent review and follow-up disposition are recorded in the next section; the feature was merged and deployed through PR #2.


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

Windows verification: 42 tests ran, with the POSIX file-permission test and real Linux ICMP integration test skipped. A real configured host also produced RTTs on the development status page. Tests cover one unprivileged echo, no executable on Linux, timeout versus local permission/lookup/execution errors, Windows arguments and localized RTT formats, numeric destination handling, and partial metric preservation. Reika independently approved PR #3 with no blockers and four informational findings. Her fresh Windows run passed 40 tests with two platform skips, and her Linux run passed all 42 tests, including real unprivileged IPv4/IPv6 loopback ICMP. She also checked real Windows probes to loopback, a resolved hostname and an unanswered test address. Subsequent deployment verification confirmed live ICMP through the hardened production service, with the private configuration and base service unit unchanged.


PR #3 finding disposition: F1 (additional RTT locales), F3 (first-address dual-stack selection) and F4 (legacy Windows exit codes) are accepted compatibility limits and documented in README. F2 is addressed with the dependency license notice and upstream license link. No runtime code changed after independent review.

## IP HTTPS deployment review checkpoint

Configuration-only proposal: bootstrap nginx serves ACME challenges without proxying the application, then the HTTPS site uses a browser-trusted IP certificate and fixed Host/redirect values. Gunicorn stays on loopback; the app drop-in disables plaintext tunnel mode, enables secure cookies and limits trusted scheme forwarding to the loopback proxy. nginx overwrites scheme headers and removes alternate scheme headers supplied by clients. No application code or private host configuration changes are proposed.

Certbot 5.8.0 uses an isolated virtual environment because older distribution clients may lack IP-webroot support. The hourly timer targets only the monitor's certificate lineage. Renewal reloads nginx only after a configuration check; staged certificate tests must not replace the trusted production certificate. No DNS access is needed, and no real IP address or certificate belongs in this repository.

Development evidence: an extracted distribution nginx 1.28.3 binary passed configuration validation. Loopback-only integration checked verified test TLS with a generated local CA, fixed redirects, GET-only challenge handling, bootstrap isolation, rejection of direct plaintext app requests, Secure/HttpOnly/SameSite cookies, an authenticated host-list read, spoofed alternate scheme headers, and cross-origin rejection. The separate test app retained production's service restrictions, including PrivateTmp. Temporary units were stopped afterward; checksums confirmed that the production unit and private configuration stayed unchanged. Renewal unit syntax was also checked. No public listeners or ACME accounts were created for these checks.

Independent review focus: bootstrap-to-HTTPS ordering and rollback, fixed authority/redirect behavior, scheme-header trust, cookie and origin handling, certificate issuance/renewal commands, and timer/hook reliability. Login throttling currently shares nginx's loopback address; no client-IP middleware is introduced. The independent review and completed public issuance/renewal checks are recorded in the following sections.

## PR 4 independent review follow-up

Reika approved the configuration-only proposal with one low and four informational findings. F1 is addressed by documenting that HSTS does not apply to IP literals and that the HTTPS URL should be used directly; an HTTP redirect is not downgrade protection for an initial insecure request. F2's operational concern is accepted with daily journal/expiry checks until independent monitoring exists. Its suggested expiry-email remedy is corrected: Let's Encrypt stopped expiry notification emails in 2025, so registration does not restore them. F3 documents IPv4-only listeners and coordinated future IPv6/DNS rollout. F4 is an explicit installed-binary option check before issuance. F5 retains the existing 64 KiB body limit. No proxy or application runtime behavior changed after independent review. Public ACME validation, issuance, renewal-hook testing and final activation were subsequently completed as recorded below.

## HTTPS deployment verification

The reference rollout completed staging validation, production IP certificate issuance, ordinary external TLS verification, secure-cookie/authenticated API checks, scheme-header and origin checks, a rollback rehearsal, and a successful renewal dry-run including the nginx reload hook. Hourly renewal checks were enabled. Checksums confirmed that private host configuration and the base app service unit were unchanged; secure-proxy settings are confined to the reviewed drop-in.

One provisioning correction was needed: creating a deep challenge directory under umask 0077 left its parent directories unreadable to nginx. The guide now explicitly creates every public-webroot ancestor with mode 0755. Private configuration directories remain 0700/0600. No nginx or app runtime behavior changed after independent review. The deployment still has no external expiry-alert integration; the documented daily journal/expiry check applies until monitoring is configured.

## PR 5 host-name sorting review and deployment

Reika independently approved PR #5 at commit `39b9668` with no blocking findings. Her browser collator checks confirmed natural, case-insensitive ordering and stable ordering for equal names. Source review confirmed that sorting changes only temporary location arrays, preserving the saved inventory order and host identity. The author's syntax check and Node DOM harness also covered numeric/mixed-case names, selection, click identity, filtering, empty state and unchanged source inventory.

All three informational notes are accepted: base sensitivity can also ignore accents; the browser's default locale can affect ordering; and initial selection still follows configuration order, so the selected server need not be the first displayed entry. No code changes were required after review.

PR #5 merged as `3293990` and was deployed without restarting the service. HTTPS checks confirmed the login page, the unauthenticated API boundary and an exact byte match between the served JavaScript and the reviewed merged file. Private host configuration and the base service unit were unchanged. The prior revision remains available for rollback.

## PR 6 RAM fallback and version review/deployment

Reika approved PR #6 at `7cfef25` with no blocking findings. Her independent Windows regression run passed 40 of 42 tests with two platform skips. She checked field-dependent available/free memory selection, valid zero values, no estimated availability, version formatting and safe text rendering, and stale-state resets. The duplicated version fallback hover text and redundant reset are accepted cosmetic details. Her request for repeatable UI tests is addressed by `tests/test_ui.cjs` in the next review round.

PR #6 merged as `5ee3851` and was deployed. Verified HTTPS checks confirmed the new header template, exactly one version element, the unauthenticated API boundary and exact reviewed JavaScript/CSS. Private host configuration and the base service unit were unchanged. The web service was restarted to load the template; the prior revision remains available for rollback.

## PR 7 stable ordering review checkpoint

The storage table previously rendered rows in upstream API order, so differently ordered responses could move entries between refreshes. The proposed change sorts a copy naturally by storage name before rendering; labels and metrics remain attached to each storage object. It also selects the first host in the displayed Provider/Location tree after login. Provider and location insertion order is preserved; a global host-name sort would select the wrong branch. Login clears stale search text, and returning from settings keeps a valid current selection or uses the same first-host fallback.

Seven Node UI tests execute the shipped renderer and handlers against a small DOM double: all six permutations of three storage entries, label/metric identity, unchanged frozen API arrays, empty/unavailable storage, nested tree selection, settings return, empty inventory, RAM edge cases and version/reset handling. Browser verification with a fictional API that reverses storage order between samples confirmed unchanged displayed row order and label placement; the first displayed host was selected despite reverse configuration order. Reika subsequently approved this round; the review and deployment outcome follow.


## PR 7 independent review and deployment

Reika approved `7cba4c1` with no blocking findings. She independently passed all 42 Python tests on Linux, inspected the Node harness, and exercised the exact shipped JavaScript in a real browser across all seven behavior scenarios. The author had separately passed all seven Node tests. Manual invocation of the UI tests is accepted for this small project; the README verification checklist now explicitly includes them. If the first configured host is removed, the first remaining provider/location branch becomes the selection anchor, matching the displayed tree.

The storage shuffle was reported by the user and confirmed with screenshots showing different row orders. No layout or column-width changes were needed. PR #7 merged as `c08851a` and was deployed without a restart. Trusted HTTPS checks confirmed the login/header page, the unauthenticated API boundary and an exact match between the served JavaScript and the reviewed merged file. Private configuration and the base service unit were unchanged. The live browser reached the login screen; authenticated production browser behavior was not rechecked because no active browser session was available. Ordering and selection behavior were verified with fictional fixtures and independently in the reviewer's browser. The prior revision remains available for rollback.
