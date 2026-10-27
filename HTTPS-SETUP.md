# HTTPS on a public IP address

This deployment keeps Gunicorn on `127.0.0.1:8080` and puts nginx on public TCP 80/443. The existing shared access key still protects the application. No DNS record is required. All addresses below are documentation examples; substitute the deployment's actual public IPv4 address outside the repository.

**Status:** independently reviewed and approved with documented findings. Development checks passed using nginx 1.28.3 and loopback-only test listeners: syntax, verified test TLS, redirects, ACME path handling, secure cookies, authenticated access, spoofed scheme headers and cross-origin rejection. No public certificate was requested. Production nginx checks, public ACME validation and renewal dry-run are still required before activation. Do not treat these templates as an already deployed public service.

## Preflight and recovery

Read the actual OS version, web listeners, nginx sites, firewall and service unit first. Confirm TCP 80/443 are available and can be reached externally. Preserve SSH access and existing firewall rules. Do not replace an existing site's default-server configuration without inspecting it.

Back up any nginx files and the app service configuration that will change. Keep the current application release and private configuration in place. The private host configuration, tokens and Proxmox trust files do not need changes.

Install nginx through the machine's package manager. For the ACME client, use a separate root-owned virtual environment so the application dependencies are unaffected:

```sh
sudo python3 -m venv /opt/phm-certbot
sudo /opt/phm-certbot/bin/python -m pip install 'certbot==5.8.0'
sudo /opt/phm-certbot/bin/certbot --version
sudo install -d -m 0755 /var/lib/proxmox-host-monitor-acme/.well-known/acme-challenge
```

Certbot 5.4+ supports the IP-address webroot flow. The pin above was checked against Certbot's published 5.8.0 release; maintain this isolated client with normal security updates.

## Bootstrap HTTP validation

Customize `deploy/nginx-acme.conf` with the public IP and install it as the monitor's nginx site using the host's existing include layout. It serves only ACME challenges; other requests return 404. Enable it only after `sudo nginx -t` passes. Reload an already running nginx, or start it if this is a new installation.

Place a random test file under `.well-known/acme-challenge/` and retrieve it from an external connection at `http://192.0.2.10/.well-known/acme-challenge/<test-name>`. Remove only that test file after the check. A loopback curl does not establish public reachability.

## Validate issuance, then obtain the trusted certificate

Use the installed client's `certbot --help all` to confirm `--ip-address`, `--preferred-profile` and webroot support before issuance. Review the Let's Encrypt Subscriber Agreement before accepting it. Certificate issuance publishes the IP address in public certificate-transparency records. The following commands omit a contact email rather than guessing one; provide an operator-approved email instead if desired.

First perform a staging validation without saving an untrusted certificate as the production lineage:

```sh
sudo /opt/phm-certbot/bin/certbot certonly --dry-run \
  --non-interactive --agree-tos --register-unsafely-without-email \
  --preferred-profile shortlived --preferred-challenges http \
  --webroot --webroot-path /var/lib/proxmox-host-monitor-acme \
  --cert-name phm-ip --ip-address 192.0.2.10
```

After independent review and successful staging validation, obtain the browser-trusted production certificate:

```sh
sudo /opt/phm-certbot/bin/certbot certonly \
  --non-interactive --agree-tos --register-unsafely-without-email \
  --preferred-profile shortlived --preferred-challenges http \
  --webroot --webroot-path /var/lib/proxmox-host-monitor-acme \
  --cert-name phm-ip --ip-address 192.0.2.10
```

Verify the issuing chain, IP subjectAltName, validity dates and private-key permissions. Never install the staging certificate in nginx or bypass certificate verification. IP certificates last about six days and require automatic renewal.

## Activate HTTPS

Customize `deploy/nginx-https-ip.conf`, using the same IP and certificate name, then replace the monitor's bootstrap site. Install `deploy/https.conf` as `/etc/systemd/system/proxmox-host-monitor.service.d/https.conf`. This enables secure cookies and disables the app's tunnel-only plaintext exception. The base service must keep its loopback bind.

Gunicorn accepts `X-Forwarded-Proto: https` only from the explicitly allowed loopback proxy. nginx overwrites that header and removes alternate scheme headers supplied by clients. Host forwarding is fixed to the configured public IP. The HTTPS-only app rejects direct plaintext access; the old plain HTTP SSH preview will therefore stop working after activation.

Run `nginx -t`, reload systemd, restart the monitor, and reload nginx. Verify all of the following before considering activation complete:

- Public HTTP redirects to the fixed HTTPS IP, while the ACME challenge path stays on HTTP.
- An ordinary TLS client verifies the public certificate without a custom CA or insecure flag.
- Login works through HTTPS; the session cookie has Secure, HttpOnly and SameSite=Strict attributes.
- An authenticated host list and stats request work; CPU/RAM/storage and ping remain available.
- Cross-origin writes are rejected, and spoofed forwarding headers cannot turn plaintext traffic into trusted HTTPS.
- Gunicorn is still bound only to loopback, and the private host configuration is unchanged.

The existing Flask login throttle sees nginx's loopback address; users share that login-attempt bucket. No forwarded-client-IP middleware is introduced in this deployment. The intended deployment uses a shared administrative key, not independent user accounts.

If activation fails, restore the previous nginx site and remove only this deployment's app drop-in, then reload systemd and restart the app. Recheck the former loopback service. Preserve issued certificates and logs for diagnosis.

## Automatic renewal

Install `deploy/phm-reload-nginx` as root-owned executable `/usr/local/sbin/phm-reload-nginx`. Persist the deploy hook in this lineage with Certbot's supported reconfiguration command:

```sh
sudo /opt/phm-certbot/bin/certbot reconfigure --cert-name phm-ip \
  --deploy-hook /usr/local/sbin/phm-reload-nginx
```

Install the supplied `phm-cert-renew.service` and `.timer` under `/etc/systemd/system/`, then reload systemd and enable/start the timer. It checks hourly with up to ten minutes of jitter and catches missed checks after downtime. It targets only the `phm-ip` lineage. Do not also enable another renewal scheduler for the same lineage.

Test the complete renewal path, including the reload hook:

```sh
sudo /opt/phm-certbot/bin/certbot renew --cert-name phm-ip --dry-run --run-deploy-hooks
sudo systemctl start phm-cert-renew.service
sudo systemctl status phm-cert-renew.timer --no-pager
sudo journalctl -u phm-cert-renew.service -n 30 --no-pager
```

Confirm that nginx still presents the trusted production certificate after the dry-run and that the next timer event is scheduled. Failed renewal attempts stay visible in the service journal; there is no external alerting integration in this setup. Check the renewal service journal and certificate expiry at least daily until independent expiry monitoring is in place. Registering a contact email does not provide expiry reminders: [Let's Encrypt ended that service in 2025](https://letsencrypt.org/docs/expiration-emails/). Hourly retries do not replace monitoring.

Browsers do not apply HSTS to IP-literal hosts. Open/bookmark the HTTPS URL directly: the HTTP redirect is a convenience and cannot protect an initial HTTP request against an active downgrade attacker. A DNS hostname is needed to gain HSTS protection.

These templates listen on IPv4 only. Do not publish an AAAA record or add an IPv6 listener until the certificate covers that IPv6 address (or a validated DNS hostname).

When a DNS name becomes available, issue a certificate for that name and update nginx's server name, fixed redirect and forwarded Host together. Keep the working IP endpoint until the replacement is verified.

## References

- [Let's Encrypt IP certificate and Certbot guidance](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)
- [Certbot user guide: renewal and deploy hooks](https://eff-certbot.readthedocs.io/en/stable/using.html)
