# Dependencies

## Selection

Direct runtime dependencies are listed in [`pyproject.toml`](../pyproject.toml): `docker`, `proxmoxer`, `requests`, `pydantic`, `typer`, `jinja2`, `pyyaml`, `cryptography`. Each is a widely used, actively maintained, FLOSS-licensed library that replaces code that is hard to get right (crypto, YAML, API clients). Adding a dependency requires a stated reason in the pull request, a license compatible with AGPL-3.0, and a check that it is maintained. Prefer the standard library when it is enough. Cryptography is never hand-rolled.

## Obtaining

Dependencies are installed with pip from PyPI by the standard build tooling (`pip install .` / `python -m build`); the Docker image is built from the official `python:3.12-slim` image, pinned by digest in the `Dockerfile` (Dependabot proposes digest updates). There are no vendored copies and no committed binaries.

## Tracking

- Versions are declared as minimums in `pyproject.toml`; resolved versions appear in each release's SBOM.
- **Dependabot** opens weekly update PRs for pip, GitHub Actions and Docker.
- **Dependency review** blocks PRs that add known-vulnerable dependencies.
- **CodeQL** and the **OpenSSF Scorecard** run on a schedule and on changes.

## Remediation targets

| Severity of a known vulnerability in a dependency | Target to fix or mitigate |
| --- | --- |
| Critical / High | 14 days |
| Medium | 60 days |
| Low | next regular release |

If a fix is not available upstream, the project documents a mitigation or replaces the dependency. Fixed vulnerabilities are listed under **Security** in the [changelog](../CHANGELOG.md).
