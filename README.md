# Proxmox Host Monitor

A small, self-hosted, read-only Proxmox resource monitor. CPU, RAM and storage first. No database, user accounts, or historical metrics store.

## The interface

- Expand **Provider → Location → Server** in the sidebar. Select a server to see its resources.
- CPU model, sockets, cores, threads and current usage.
- RAM total, used and available. Older APIs without available memory show free memory separately.
- Every API-visible Proxmox storage entry, including inactive entries, with its individual capacity. Shared backing disks are never summed.
- Running/stopped VM and container counts, with templates excluded.
- Reported physical/bond interface state and one ICMP ping from the monitoring machine.
- Custom labels for the server, CPU, RAM, storage entries and interfaces.
- A small configuration page for server connections, grouping and labels.

Only the selected server is sampled, ten seconds after the previous request completes, while the monitor page is visible. After three consecutive failed requests, automatic retries slow to thirty seconds; manual Refresh and returning to the page still try immediately. A successful request restores ten-second sampling. Configuration and hidden tabs pause sampling. CPU, RAM and ping charts hold at most five minutes in browser memory. Switching servers or reloading clears the buffer; failures and pauses create gaps. No background collector or metrics database runs.

## Development status

Initial implementation and independent follow-up review complete. Live read-only compatibility has been verified with **Proxmox VE 9.2.11** from **Linux/Python 3.14**, covering node status, storage, network interfaces and cluster resources through a dedicated token with only `Sys.Audit`, `VM.Audit` and `Datastore.Audit`. The authenticated dashboard displayed the resulting CPU, memory, storage, guest counts and ping successfully. Other PVE versions and storage backends still need testing.

Demo data is explicitly fictional and does not contact any host. It is optional and must be enabled at initialization.

The standard interface endpoint does not supply physical link speed or traffic counters suitable for this implementation. These values are intentionally unavailable rather than estimated or replaced with VM traffic totals. ICMP failure does not mean the API is down. All counts and storage entries reflect the monitoring token's visibility.

## Run locally on Linux

Requires Python 3.11+ with venv support and an iputils-compatible `ping`. Dependencies install into an isolated environment. The backend uses Flask, requests and Gunicorn; the browser uses plain JavaScript and CSS with no CDN dependencies.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py init --demo
PHM_LOCAL_HTTP=1 .venv/bin/gunicorn --workers 1 --threads 8 --bind 127.0.0.1:8080 wsgi:app
```

Enter an access key of at least 20 characters at the hidden prompts. The default config path is `~/.config/proxmox-host-monitor/config.json`, **outside the checkout**. Override it with `PHM_CONFIG`. Runtime config is written atomically with mode `0600`; use a private directory owned by the service account. Never commit or upload it.

For a remote development machine, forward its loopback port over SSH and open `http://127.0.0.1:8080`. `PHM_LOCAL_HTTP=1` permits HTTP only for loopback clients with a localhost/127.0.0.1 Host header. Never bind this development mode to a public interface.

## Access key and HTTPS

There is one shared access key. Anyone holding it can view hosts and edit monitor configuration. There are no separate administrator roles.

Keys are stored as salted scrypt hashes. Successful entry creates a signed, HttpOnly, SameSite=Strict session cookie with an eight-hour lifetime. Normal deployment requires HTTPS and Secure cookies. Writes require a session CSRF token, and foreign origins are rejected. Login attempts are rate limited in memory. Use **one Gunicorn worker** so the rate limit is shared by all threads; restarting resets the limit. Add upstream rate limiting before broad exposure.

Configuration updates hold a sidecar file lock across the entire read-modify-write transaction. This serializes web edits and CLI key rotation across processes, preventing lost updates. Hash generation happens before taking the lock. Use local filesystem storage for the private config and run operator commands as its owning service account. The sidecar is part of runtime state and must not be removed while writers are running.

For normal deployment, omit `PHM_LOCAL_HTTP`, keep Gunicorn bound to loopback, and put an HTTPS reverse proxy in front. It must overwrite `X-Forwarded-Proto` with the actual external scheme and preserve the Host header. Gunicorn trusts forwarded scheme headers only from loopback by default. Use a dedicated unprivileged service user; the application does not need sudo. HTTPS/domain deployment is operator-specific and is not automated here.

Rotate the access key locally:

```sh
.venv/bin/python manage.py rotate-key
```

Rotation invalidates existing sessions on their next request. The raw key is never saved. API tokens remain separate from the website key. If the private configuration becomes unreadable while running, the API returns a generic HTTP 503 until the operator repairs it; existing configuration is never silently replaced.

Remove the optional demo server without editing the private config manually:

```sh
.venv/bin/python manage.py disable-demo
```

Reload the browser afterward to refresh the server list.

## Add a Proxmox connection

Start with the [step-by-step Proxmox user, role and token setup](PROXMOX-SETUP.md). It lists the exact menu choices, the three privileges to select, and the separate permission entries required for the user and token.

Open Configuration and supply an HTTPS API origin, the exact Proxmox node name, a token ID (`user@realm!token`), and its secret. Token secrets are write-only in the UI and never returned by the host-list endpoint. Saving configuration does not test or modify Proxmox; select the server to fetch stats.

Use a dedicated monitoring user and a privilege-separated token. Assign the custom `HostMonitorAudit` role containing `Sys.Audit`, `VM.Audit` and `Datastore.Audit` to both the user and token, as shown in the setup guide. Check the permissions for your version in the [Proxmox API viewer](https://pve.proxmox.com/pve-docs/api-viewer/) and [user-management documentation](https://pve.proxmox.com/pve-docs/chapter-pveum.html).

The client supports only these GET endpoints:

```text
/nodes/{node}/status
/nodes/{node}/storage
/nodes/{node}/network
/cluster/resources
```

TLS verification is always on. Configuration offers four trust choices:

- **Keep saved trust** retains an existing server's trust configuration.
- **Publicly trusted certificate** uses the monitor's standard certificate authorities.
- **Upload trusted CA** accepts a PEM CA file (up to 32 KiB), stored privately beside the runtime configuration and reused automatically.
- **Approve this server certificate** retrieves the certificate without sending HTTP requests or API credentials. Compare its SHA-256 fingerprint through an independent trusted source, then tick explicit approval. Inspection alone does not prove identity.

Connection inspection, tests and saves that change connection settings share a limit of 12 checks per browser session per minute and 60 per process per minute. HTTP 429 includes a 60-second Retry-After. These limits bound bursts; anyone with the shared key remains a trusted administrator able to configure reachable HTTPS destinations. Limits are in memory and reset on restart; the supplied deployment uses one worker.

**Test connection** checks TLS and all four read-only API endpoints without saving. New servers and changes to connection credentials or trust must pass these checks when saved. Label and grouping edits retain saved trust and can be made while the host is offline. No Proxmox settings are changed.

Certificate approvals expire after ten minutes and are bound to the API URL and authenticated browser session. Uploaded or approved trust is saved only after verified API checks pass. The backend chooses certificate paths; no manual file installation is needed. Existing configured trust files continue working. All custom trust files, including legacy paths, act as explicit trust anchors: intermediate-only chains are accepted without requiring a self-signed root in the file. Hostname/IP and expiry checks still apply. CA uploads require every included certificate to be a currently valid CA; remove expired certificates from mixed bundles before uploading.

Approving an exact node certificate permits it as a trust anchor while retaining hostname/IP and validity checks. A changed or renewed node certificate requires a new inspection and explicit approval. A valid issuing CA generally survives normal leaf renewal. Some older cluster CAs lack extensions required by modern TLS; approving a verified node certificate is an alternative without changing the cluster CA or disabling verification.

Redirects are rejected. Proxy and netrc environment settings are ignored to keep credential routing explicit. Changing a stored token's API URL or ID requires re-entering its secret.

The application sets HSTS for its own hostname only. It deliberately does not impose HTTPS policy on other subdomains, which may host unrelated services. Any wider HSTS policy belongs to the operator's reverse-proxy configuration.

Use the label editor to choose a component type, enter its storage/interface name when relevant, and write a free-text description. For example, label RAM as `ECC · DDR4` or storage `local-lvm` as `SSD`.

The private configuration stores labels as a simple object:

```json
{
  "server": "Production",
  "cpu": "EPYC",
  "ram": "ECC · DDR4",
  "storage:local-lvm": "SSD",
  "network:eno1": "Uplink"
}
```

Labels belong to the monitor and never modify Proxmox. Provider, location, server names and example domains in this repository are generic. Real infrastructure data belongs only in private runtime configuration.

## Checks and review

```sh
.venv/bin/python -m unittest discover -s tests -v
```

See [REVIEW.md](REVIEW.md) for the first independent-review checkpoint and known limits. This initial build should remain reachable only through an SSH tunnel until reviewed and configured for HTTPS.

## License

The application is [MIT licensed](LICENSE). The separately installed dependency [icmplib 3.0.4](https://github.com/ValentinBELYN/icmplib/blob/main/LICENSE) is distributed under LGPL-3.0; its upstream license and notices accompany the dependency.

## ICMP ping

Ping is measured from the machine running the backend, not the browser. A Windows development preview therefore measures a different network path from a Linux VM deployment.

Linux uses [icmplib](https://github.com/ValentinBELYN/icmplib) with unprivileged ICMP datagram sockets (`privileged=False`), one echo request and a two-second reply timeout. The supplied service keeps `NoNewPrivileges=true` and an empty capability bounding set: no raw-socket capability or privileged helper is required. The OS must allow the service group to use ICMP datagram sockets through `net.ipv4.ping_group_range`; if it does not, the page reports ping as unavailable. The application does not change sysctls or permissions automatically.

Windows uses the system ping executable with Windows arguments, a numeric destination and no shell. RTTs below one millisecond are shown as the reported one-millisecond upper bound. Unrecognized output is reported as unavailable rather than inventing a latency.

The page distinguishes **No reply** (no successful ICMP echo reply) from **Unavailable** (a local lookup, permission or probe execution error). Ping failure does not prevent CPU, RAM or storage reads or block connection saving. ICMP success does not establish API health, and a missing reply does not prove the host is offline.

Windows compatibility limits: RTT unit names outside `ms`/`毫秒` may be reported as unavailable. For dual-stack hostnames the first resolved address is selected, which may differ from the API connection. Older Windows versions may classify some local failures as exit code 1 (shown as no reply). These limits do not affect the Linux deployment.

## Public HTTPS deployment

[HTTPS-SETUP.md](HTTPS-SETUP.md) describes a reviewed rollout of nginx, a browser-trusted IP certificate and automatic renewal. Templates are under `deploy/`. They require customization, independent review and live validation before installation; they do not change the application or deploy themselves.
