# Assurance case

Claim: HomelabScribe™ can be pointed at a Docker host or Proxmox cluster without changing it, leaking secret values, or contacting anyone else, and its releases can be verified.

## Threat model

| Threat | Mitigation |
| --- | --- |
| HomelabScribe (or a bug) mutates a monitored system | Read-only rule; collectors use read endpoints; socket proxy `POST=0`; `PVEAuditor` role; code review via CODEOWNERS |
| Secret values leak into snapshots/docs | `redact.py` keeps env var names only, drops sensitive labels and htpasswd hashes; regression test |
| Unauthenticated Docker API exposed | Documented requirement to bind to a trusted LAN address; proxy limits verbs |
| Malicious or malformed input (labels, config, catalog) | `yaml.safe_load`, Pydantic validation, bundled Jinja2 templates with no user template path |
| Forged Pro license | Ed25519 signature check; private key never in the repo (`*.pem` ignored) |
| Compromised dependency or workflow | Dependabot, dependency review, pinned action SHAs, least-privilege tokens, CodeQL, Scorecard |
| Tampered release artifact | Keyless cosign signatures, checksums, SBOM, provenance |
| Loss of user notes | Notes block preserved; pages with notes archived, not deleted |

## Trust boundaries

1. Operator's machine/container ↔ **Docker socket proxy / Proxmox API** (network, read-only).
2. HomelabScribe ↔ **output and state folders** (filesystem; contents are sensitive).
3. **Release pipeline** ↔ users (signed artifacts).

Nothing else is trusted or contacted.

## Secure design principles

- *Least privilege:* read-only credentials, non-root container, `permissions: read-all` workflows.
- *Economy of mechanism:* a linear pipeline with pure parse functions, no server, no database.
- *Fail safe defaults:* Free-tier limits and redaction apply unless explicitly changed; unknown license keys grant nothing.
- *Complete mediation of secrets:* every field goes through the `Service` model, so a new field is a deliberate, test-covered change.
- *Open design:* AGPL core; security does not depend on obscurity.
- *Minimize attack surface:* no listening port, no telemetry, small dependency set.

## Common weaknesses and how they are countered

| Weakness class | Counter |
| --- | --- |
| Injection (template, YAML, shell) | Templates are bundled (no user-supplied template path; output is Markdown, so HTML autoescaping is off by design), `safe_load`, no shell invocation, CI never interpolates untrusted event data into `run:` |
| Sensitive data exposure | Redaction + tests, documented output sensitivity |
| Unsafe deserialization | JSON snapshots validated by Pydantic; no pickle |
| Path traversal in output | Page filenames come from `slug()`, which reduces names to `[a-z0-9-]` |
| Weak crypto | Standard `cryptography` Ed25519; no home-grown crypto |
| Malformed input crashing a parser | Hypothesis fuzz tests (`tests/test_fuzz_parsers.py`) run in CI |
| Known-vulnerable components | Automated updates, dependency review, remediation SLAs in [dependencies](dependencies.md) |
| Excessive CI permissions | Per-workflow minimal `permissions`, SHA-pinned actions |

Residual risk: the output is a description of your infrastructure; protect it like any other inventory.
