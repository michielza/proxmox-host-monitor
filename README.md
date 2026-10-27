# Proxmox Host Monitor

A small, fast, self-hosted website that reads Proxmox host statistics and displays available resources.

## Scope

- Keep it simple (KISS).
- Show CPU, memory, and storage information per host.
- Fetch statistics through the Proxmox API using restricted, read-only API tokens.
- Keep API credentials on the backend, out of the browser and repository.
- Verify Proxmox HTTPS certificates.
- Monitoring only: no controls to modify hosts or guests.

## Status

Product design comes first. The layout, exact fields, refresh behaviour, and implementation stack are still to be agreed.

No application has been implemented or deployed. A VM will be supplied for development and hosting after the design is settled.

## License

[MIT](LICENSE).
