# Proxmox monitoring user and API token

Do this once per Proxmox cluster, or once on each standalone host. Use an administrator account for setup. A cluster's user/token can serve all its nodes. No Linux account or group is needed for this setup.

The examples use `hostmonitor@pve` and token `dashboard`. If you already created a monitoring user with another name, use that name consistently instead of creating a duplicate.

## 1. Create the user

Open **Datacenter → Permissions → Users → Add**.

| Field | Select / enter |
| --- | --- |
| User name | `hostmonitor` |
| Realm | **Proxmox VE authentication server** (`pve`), not Linux PAM |
| Password / Confirm password | Leave blank for a token-only account; a user password is not needed by this monitor |
| Group | Leave blank |
| Expire | Leave blank unless you require account expiry |
| Enabled | Checked |
| First name / Last name / E-Mail | Leave blank |
| Comment | Optional, e.g. `Read-only host monitor` |

Click **Add**. The resulting user ID is `hostmonitor@pve`.

There are no permission selections in this dialog. Assign them in step 3.

## 2. Create the read-only role

Open **Datacenter → Permissions → Roles → Create**.

- **Name:** `HostMonitorAudit`
- **Privileges:** select exactly these three:
  - `Sys.Audit`
  - `VM.Audit`
  - `Datastore.Audit`

Click **Create**. If this role already exists with exactly these privileges, reuse it.

These cover node statistics, guest visibility and storage statistics. No power, configuration or guest-agent privileges are required by the monitor.

## 3. Give the user permission

Open **Datacenter → Permissions → Add → User Permission**.

| Field | Select / enter |
| --- | --- |
| Path | `/` |
| User | `hostmonitor@pve` |
| Role | `HostMonitorAudit` |
| Propagate | Checked |

Click **Add**. `/` with propagation grants these three read-only privileges throughout this cluster.

Choose **User Permission** here, not Group Permission or API Token Permission.

## 4. Create the API token

Open **Datacenter → Permissions → API Tokens → Add**.

| Field | Select / enter |
| --- | --- |
| User | `hostmonitor@pve` |
| Token ID | `dashboard` |
| Privilege Separation | Checked |
| Expire | Leave blank unless you require token expiry |
| Comment | Optional |

Click **Add**. Copy the full token ID and secret from the result into your private credential store. The secret is shown only once.

- Full token ID: `hostmonitor@pve!dashboard`
- Secret: the generated value, not the user's password

Keep Privilege Separation enabled. The token gets its own explicit permission in the next step.

## 5. Give the token permission

Open **Datacenter → Permissions → Add → API Token Permission**.

| Field | Select / enter |
| --- | --- |
| Path | `/` |
| API Token | `hostmonitor@pve!dashboard` |
| Role | `HostMonitorAudit` |
| Propagate | Checked |

Click **Add**. Choose **API Token Permission**, not User Permission, for this step.

The Permissions table should now contain both entries:

| Path | Type | User / token | Role | Propagate |
| --- | --- | --- | --- | --- |
| `/` | User | `hostmonitor@pve` | `HostMonitorAudit` | Yes |
| `/` | API Token | `hostmonitor@pve!dashboard` | `HostMonitorAudit` | Yes |

Both entries are required: a separated token can use only permissions allowed to both the user and the token.

## 6. Add the connection in Host Monitor

Open **Configuration → Add server** in the monitor.

- Fill in your display name, provider and location.
- Enter the exact Proxmox node name and HTTPS API URL, e.g. `https://pve.example.net:8006`.
- **Token ID:** `hostmonitor@pve!dashboard`
- **Token secret:** the generated token secret
- **Certificate trust:** choose **Publicly trusted certificate** for a public CA matching the API URL, or **Upload trusted CA** and select the cluster's verified public CA PEM file.
- For a default/private node certificate, click **Inspect server certificate**. Compare the SHA-256 fingerprint with a trusted source (for example the administrator over a verified SSH connection), then tick **I verified this fingerprint and trust this certificate**. Never approve solely because the monitor displayed it.

Click **Test connection**. It checks TLS, node status, storage, network and guest-list access. Correct any errors, then **Save server**. Saving repeats verification when the connection or trust changed, stores trust automatically, and never changes Proxmox. Inspection sends no API credentials; test and save send them only over verified TLS.

When editing an existing server, **Keep saved trust** reuses its current certificate configuration. CA certificates and approved node certificates are stored privately on the monitor. Approval expires after ten minutes; inspect again if needed. An approved node certificate that renews or changes needs fresh approval. Hostname/IP mismatch and expiry still block access.

For another node in the same cluster, reuse the token but enter that node's own name, API URL and appropriate certificate trust. For another cluster or standalone host, repeat steps 1–5 there.

## If access fails

- **401/403 or API access denied:** check that the user is enabled, neither user nor token has expired, the token ID includes `@pve!dashboard`, the secret is correct, and both permission rows above exist. More-specific existing ACLs can override inherited permissions.
- **Certificate verification failed:** check certificate trust, hostname/IP matching and expiry. API permissions do not fix a TLS error.
- **Only some data appears:** confirm all three audit privileges are present on both sides and the exact node name is correct.

Keep real addresses, certificates and secrets in private runtime configuration, never in the repository or issue comments.

## References

Field names and permission behaviour were checked against the official [Proxmox user-management documentation](https://pve.proxmox.com/pve-docs/chapter-pveum.html) and the upstream [user form](https://github.com/proxmox/pve-manager/blob/master/www/manager6/dc/UserEdit.js), [role form](https://github.com/proxmox/pve-manager/blob/master/www/manager6/dc/RoleEdit.js), [permission form](https://github.com/proxmox/pve-manager/blob/master/www/manager6/dc/ACLView.js) and [token form](https://github.com/proxmox/pve-manager/blob/master/www/manager6/dc/TokenEdit.js). Menu wording may vary slightly by PVE version or language.
