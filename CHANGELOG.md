# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Security fixes are listed under **Security**.

## [Unreleased]

### Added
- Project documentation in `docs/`, security policy, governance, contribution and support guides.
- CI security workflows: CodeQL, OpenSSF Scorecard, dependency review, DCO check, secret scanning.
- Signed releases: cosign keyless signatures, checksums, SBOM and build provenance.
- `link_host` per Docker host: the hostname or IP that service links use (default: the host's `name`).
- Catalog entries for Seerr, Whisparr, Dozzle, Autoheal, Argus Agent, trash-watch and the Prometheus exporters and tools in the Argus stack (Blackbox, Varken, Exportarr, SNMP, Tdarr, Proxmox, qBittorrent, GitLab CI, UniFi Poller, Pi-hole, PeaNUT).
- `manual_services` in the config: describe things the scanners can't see (Pi-hole on a Raspberry Pi, a NAS, a router). They get pages, show in the index and can be marked critical or as the DNS server in the break-glass guide.

### Fixed
- Two containers that match the same catalog entry on one host (for example `sonarr` and `sonarr4k`) no longer share one page. The second is titled "Sonarr (sonarr4k)" and gets its own page.
- The break-glass guide no longer mistakes a Pi-hole exporter for the DNS server; only Pi-hole and AdGuard Home images (or a manual entry) count.

### Security
- The container base image is pinned by digest.
- Property-based (Hypothesis) fuzz tests cover the Docker, Proxmox, redaction, notes and license parsers.

### Changed
- The container image now runs on Python 3.14, and CI tests Python 3.11 to 3.14.
- Image defaults such as `PATH`, `HOME`, `S6_*` and `XDG_*` are left out of the "Configuration variables" list.
- The default `output_dir` is now `homelabscribe-docs` (was `docs`), so generated output no longer collides with the project's own `docs/` folder. Set `output_dir` explicitly if you relied on the old default.

## [0.1.0] - unreleased

Initial scaffold: Docker collector, Proxmox collector (Pro), app catalog, snapshots and diff, Markdown renderer with preserved notes, offline license keys, CLI.
