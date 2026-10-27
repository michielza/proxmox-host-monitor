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

Open Configuration and supply an HTTPS API origin, the exact Proxmox node name, a token ID (`user@realm!token`), and its secret. Token secrets are write-only in the UI and never returned by the host-list endpoint. Saving configuration does not test or modify Proxmox; select the server to fetch stats.

Use a dedicated monitoring user and a privilege-separated token. Both need the relevant read permissions. `PVEAuditor` is a convenient built-in read-only role; tighter ACLs can restrict visibility. Check the permissions for your version in the [Proxmox API viewer](https://pve.proxmox.com/pve-docs/api-viewer/) and [user-management documentation](https://pve.proxmox.com/pve-docs/chapter-pveum.html).

The client supports only these GET endpoints:

```text
/nodes/{node}/status
/nodes/{node}/storage
/nodes/{node}/network
/cluster/resources
```

TLS verification is always on. For a private Proxmox CA, copy the trusted CA certificate to a service-readable file outside the checkout and enter its absolute path. That path is visible to shared-key administrators so they can edit it; certificate contents and token secrets are not returned. Redirects are rejected. Proxy and netrc environment settings are ignored to keep credential routing explicit. Changing a stored token's API URL or ID requires re-entering its secret.

The live test encountered an older cluster CA without the key-usage extension required by current Python TLS defaults. On the tested Python 3.14 runtime, trusting the exact node certificate obtained over an independently verified SSH connection worked with certificate, expiry and IP-identity checks still enabled. This narrower trust requires refreshing the trust file when the node certificate renews. Do not disable verification or alter the cluster CA merely to connect the monitor.

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

[MIT](LICENSE).
