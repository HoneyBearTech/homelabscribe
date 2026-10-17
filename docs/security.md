# Security requirements

What HomelabScribe is designed to guarantee, and what it is not.

## Requirements

1. **Read-only.** Collectors never call an API that changes a container, VM or host.
2. **No secret values on disk.** Environment variable *values*, tokens, passwords and credential-like labels are never written to snapshots or Markdown. Only variable names are kept. Tested by `tests/test_collectors.py::test_secrets_never_stored`.
3. **No phone-home.** No telemetry, update checks or license calls. Licenses verify offline against an embedded public key.
4. **Least privilege.** Docker access through a socket proxy with `POST=0`; Proxmox access with the `PVEAuditor` role only. The container runs as a non-root user.
5. **Safe parsing.** YAML is loaded with `safe_load`; templates are bundled, not user-supplied.
6. **Your notes are never destroyed.** Removed pages that hold notes are archived.
7. **Verifiable releases.** Releases are signed (Sigstore), with checksums, an SBOM and provenance ([verifying releases](verifying-releases.md)).

## What HomelabScribe does not protect

- **The generated docs are a map of your lab** (hosts, ports, service names, paths). Treat the output folder as sensitive, especially if you publish it to Git.
- **An exposed Docker API is not authenticated.** Bind the proxy to a trusted LAN address only.
- **Config files may hold a Proxmox token or Pro key.** Keep `homelabscribe.yaml` out of Git and readable only by you.
- It does not harden Docker, Proxmox or the host.

Credential handling: tokens come from the config or environment (`${VAR}`) and are used only to talk to the configured endpoint; they are not logged or rendered. Report problems via [SECURITY.md](../SECURITY.md).
