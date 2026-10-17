# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Security fixes are listed under **Security**.

## [Unreleased]

### Added
- `.gitlab-ci.yml` for the copy of the repository on the maintainer's self-hosted GitLab: a scheduled job mirrors `main` and the tags from GitHub (fast-forward only), and the lint, tests, secret scan and workflow lint re-run on every commit it receives. GitHub stays the home.
- An access table in GOVERNANCE.md listing who and what can reach each sensitive resource.
- The image is also on Docker Hub (`honeybeartech/homelabscribe`). A workflow that mirrors each release's container image from GHCR to Docker Hub (same digest, so the release attestation applies to both).

## [0.2.1] - 2026-10-17

First release with signed artifacts. Functionally identical to 0.2.0.

### Fixed
- CI, the release workflow and the container image now install from hash-pinned lockfiles (`requirements/`), so a changed or tampered package is refused; Dependabot keeps them current.
- The release workflow failed to sign and attest the container image because the registry name has to be lowercase. 0.2.0 was published to PyPI and GHCR but has no signatures, checksums, SBOM or GitHub release; use 0.2.1 or later if you want to verify what you install ([verifying releases](docs/verifying-releases.md)).

## [0.2.0] - 2026-10-17

First public release (PyPI and GHCR only; see 0.2.1).

### Added
- Project documentation in `docs/`, security policy, governance, contribution and support guides.
- CI security workflows: CodeQL, OpenSSF Scorecard, dependency review, DCO check, secret scanning.
- Signed releases: cosign keyless signatures, checksums, SBOM and build provenance.
- `link_host` per Docker host: the hostname or IP that service links use (default: the host's `name`).
- `npm` in the config: read Nginx Proxy Manager's proxy hosts to give services their real web address and write a `routes.md` page of every domain and where it goes.
- Website source in `site/` (static page for homelabscribe.com, with a pre-launch switch).
- Release workflow: a manual dry run that builds and checks everything and publishes nothing; the real run now checks that the tag matches the package version and smoke-tests the wheel. CI smoke-tests the container image.
- `--strict` for `scan` and `run`: exit with status 1 when a host, cluster or proxy could not be read, so a scheduler can notice.
- `doctor` tests the Nginx Proxy Manager login. Proxy routes also match hand-described services by the address in their URL (when only one service has that address).
- Catalog entries for Seerr, Whisparr, Dozzle, Autoheal, Argus Agent, trash-watch and the Prometheus exporters and tools in the Argus stack (Blackbox, Varken, Exportarr, SNMP, Tdarr, Proxmox, qBittorrent, GitLab CI, UniFi Poller, Pi-hole, PeaNUT).
- `manual_services` in the config: describe things the scanners can't see (Pi-hole on a Raspberry Pi, a NAS, a router). They get pages, show in the index and can be marked critical or as the DNS server in the break-glass guide.

### Fixed
- Two containers that match the same catalog entry on one host (for example `sonarr` and `sonarr4k`) no longer share one page. The second is titled "Sonarr (sonarr4k)" and gets its own page.
- A host, cluster or proxy that is down for one scan keeps its last known services and routes instead of being recorded as removed (which deleted or archived its pages).
- The break-glass guide names every DNS server (for example two Pi-holes), not just the first.
- The break-glass guide no longer mistakes a Pi-hole exporter for the DNS server; only Pi-hole and AdGuard Home images (or a manual entry) count.

- The source distribution is now lean (about 68 KB, was 3.4 MB): it no longer includes the Hypothesis cache, `.claude/`, `.github/`, `site/` or images.
- The README shown on PyPI no longer has dead relative links or images: links are rewritten to the repository and the images load from homelabscribe.com.

### Security
- The container base image is pinned by digest.
- Property-based (Hypothesis) fuzz tests cover the Docker, Proxmox, redaction, notes and license parsers.

### Changed
- The container image now runs on Python 3.14, and CI tests Python 3.11 to 3.14.
- Image defaults such as `PATH`, `HOME`, `S6_*` and `XDG_*` are left out of the "Configuration variables" list.
- The default `output_dir` is now `homelabscribe-docs` (was `docs`), so generated output no longer collides with the project's own `docs/` folder. Set `output_dir` explicitly if you relied on the old default.

## [0.1.0] - 2026-10-17 (internal, never published)

Initial scaffold: Docker collector, Proxmox collector (Pro), app catalog, snapshots and diff, Markdown renderer with preserved notes, offline license keys, CLI.
