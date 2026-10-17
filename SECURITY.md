# Security Policy

## Supported versions

Security fixes go to the latest released minor version. While HomelabScribe™ is pre-1.0, older versions are not patched; upgrade instead. See [SUPPORT.md](SUPPORT.md).

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately with GitHub's **[Report a vulnerability](https://github.com/HoneyBearTech/homelabscribe/security/advisories/new)** form (Security tab → Report a vulnerability). That reaches the maintainer directly and keeps the discussion private until a fix is ready.

Please include the version (`homelabscribe doctor` output is safe to paste), what you did, what you expected, and what happened. A short proof of concept helps.

## What to expect

| Step | Target |
| --- | --- |
| Acknowledge your report | within 3 days |
| Initial assessment (accepted / declined / need more info) | within 14 days |
| Fix or mitigation for confirmed medium-or-higher issues | within 60 days |

HomelabScribe is maintained by one person in spare time, so these are targets, not guarantees; if a deadline will be missed you will be told why.

## Disclosure

Confirmed vulnerabilities are published as [GitHub Security Advisories](https://github.com/HoneyBearTech/homelabscribe/security/advisories), with a CVE where appropriate, and listed in the [CHANGELOG](CHANGELOG.md) under "Security" for the release that fixes them. We credit reporters who want credit. Please allow a fix to ship before public disclosure.

## Scope

In scope: the `homelabscribe` CLI and package, its container image, the release workflow, and anything that could let HomelabScribe write to a monitored system, leak secret values into snapshots or docs, or make unexpected network calls.

Out of scope: weaknesses in Docker, Proxmox or the socket proxy themselves; exposing an unauthenticated Docker API to an untrusted network (see [docs/security.md](docs/security.md)).

Design details: [docs/security.md](docs/security.md) and [docs/assurance-case.md](docs/assurance-case.md).
