# Dependencies

## Selection

Direct runtime dependencies are listed in [`pyproject.toml`](../pyproject.toml): `docker`, `proxmoxer`, `requests`, `pydantic`, `typer`, `jinja2`, `pyyaml`, `cryptography`. Each is a widely used, actively maintained, FLOSS-licensed library that replaces code that is hard to get right (crypto, YAML, API clients). Adding a dependency requires a stated reason in the pull request, a license compatible with AGPL-3.0, and a check that it is maintained. Prefer the standard library when it is enough. Cryptography is never hand-rolled.

## Obtaining

Dependencies are installed with pip from PyPI by the standard build tooling (`pip install .` / `python -m build`); the Docker image is built from the official `python:3.14-slim` image, pinned by digest in the `Dockerfile` (Dependabot proposes digest updates). There are no vendored copies and no committed binaries.

## Pinning

Everything CI, the release workflow and the container image install comes from **hash-pinned lockfiles** in [`requirements/`](../requirements): `runtime.txt` (what the image installs), `ci.txt` (runtime plus test and lint tools), `fuzz.txt` (`ci.txt` plus Atheris, for the fuzz workflow) and `release.txt` (build and twine). They are installed with `pip install --require-hashes`, so a changed or tampered package is refused. The container image does not pip-install the project itself (it is pure Python and runs from its source directory with `PYTHONPATH`), so there is no unpinned install step. The base image is pinned by digest and every GitHub Action by commit SHA. The lockfiles are universal (all supported Python versions and platforms) and are regenerated with [uv](https://docs.astral.sh/uv/); the exact command is the first line of each file. For example:

```bash
uv pip compile pyproject.toml --extra dev --universal --generate-hashes --python-version 3.11 --no-header -o requirements/ci.txt
```

Dependabot proposes updates to the lockfiles weekly, in one grouped pull request.

The images used by the pipeline of the GitLab copy (Python, git, gitleaks, actionlint in [`.gitlab-ci.yml`](../.gitlab-ci.yml)) are pinned by version tag and digest. Dependabot does not read that file, so they are bumped by hand at least every quarter.

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
