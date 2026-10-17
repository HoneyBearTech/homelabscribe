# Roadmap

Plans for roughly the next twelve months. Dates are intentions, not promises; this is a one-person project.

## Next (0-3 months)
- Run against real multi-host labs and fix what the generated pages get wrong
- Grow the built-in app catalog from real-world use
- Detect reverse-proxy URLs for proxies that do not use labels (e.g. Nginx Proxy Manager)
- First public releases, signed and verifiable
- Notifications when a scheduled run finds changes

## Later (3-12 months)
- Proxmox coverage validated on real clusters
- Static HTML export for readers without Obsidian
- `homelabscribe doctor --bundle` for redacted bug reports
- More collectors (Synology, Unraid) based on demand
- 1.0: a stable config and output format, documented compatibility promise

## Security and project health
- Keep Baseline and Best Practices badges (Passing, Silver) current
- Extend the property-based (fuzz) tests as new parsers are added
- Keep the container base image pinned by digest (Dependabot updates it)
- Reproducible release builds where practical

## What HomelabScribe will not do
- Change, restart, create or delete anything on your hosts. It is read-only by design
- Phone home: no telemetry, update checks or license calls
- Store secret values (env var values, tokens, passwords)
- Run as a hosted service that receives your lab data
- Replace monitoring or configuration management; it documents, it does not manage
