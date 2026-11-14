# Roadmap

Plans for roughly the next twelve months. Dates are intentions, not promises; this is a one-person project.

## Shipped
- Detect reverse-proxy URLs for proxies that do not use labels: Nginx Proxy Manager, with `npm` in the config (0.2.0)
- First public releases, signed and verifiable: 0.2.0 was the first public release, 0.2.1 the first with signatures ([verifying releases](verifying-releases.md))
- Notifications when a scheduled run finds changes (Pro; not yet in a release)
- Static HTML export for readers without Obsidian: `html_dir` in the config (not yet in a release)
- `homelabscribe doctor --bundle` for redacted bug reports (not yet in a release)
- A draft of the 1.0 [compatibility promise](compatibility.md)

## Next (0-3 months)
- Run against real multi-host labs and fix what the generated pages get wrong
- Grow the built-in app catalog from real-world use

## Later (3-12 months)
- Proxmox coverage validated on real clusters
- More collectors (Synology, Unraid) based on demand
- 1.0: a stable config and output format, documented compatibility promise (the [draft](compatibility.md) says what it is meant to cover)

## Security and project health
- Keep Baseline and Best Practices badges (Passing, Silver) current
- Extend the property-based (fuzz) tests as new parsers are added
- Keep the container base image pinned by digest (Renovate updates it)
- Reproducible release builds where practical. CI already builds the package twice and fails if the files differ; the container image is not checked this way yet

## What HomelabScribe™ will not do
- Change, restart, create or delete anything on your hosts. It is read-only by design
- Phone home: no telemetry, update checks or license calls
- Store secret values (env var values, tokens, passwords)
- Run as a hosted service that receives your lab data
- Replace monitoring or configuration management; it documents, it does not manage
