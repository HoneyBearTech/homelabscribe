# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Security fixes are listed under **Security**.

## [Unreleased]

### Added
- Project documentation in `docs/`, security policy, governance, contribution and support guides.
- CI security workflows: CodeQL, OpenSSF Scorecard, dependency review, DCO check, secret scanning.
- Signed releases: cosign keyless signatures, checksums, SBOM and build provenance.

### Security
- The container base image is pinned by digest.
- Property-based (Hypothesis) fuzz tests cover the Docker, Proxmox, redaction, notes and license parsers.

### Changed
- The default `output_dir` is now `homelabscribe-docs` (was `docs`), so generated output no longer collides with the project's own `docs/` folder. Set `output_dir` explicitly if you relied on the old default.

## [0.1.0] - unreleased

Initial scaffold: Docker collector, Proxmox collector (Pro), app catalog, snapshots and diff, Markdown renderer with preserved notes, offline license keys, CLI.
