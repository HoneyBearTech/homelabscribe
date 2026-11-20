# Dependencies

## Selection

Direct runtime dependencies are listed in [`pyproject.toml`](../pyproject.toml): `docker`, `proxmoxer`, `requests`, `pydantic`, `typer`, `jinja2`, `markdown-it-py`, `pyyaml`, `cryptography`. Each is a widely used, actively maintained, FLOSS-licensed library that replaces code that is hard to get right (crypto, YAML, Markdown, API clients). Adding a dependency requires a stated reason in the pull request, a license compatible with AGPL-3.0, and a check that it is maintained. Prefer the standard library when it is enough. Cryptography is never hand-rolled.

`markdown-it-py` converts the generated Markdown pages to HTML for the static HTML copy (`html_dir`). A Markdown parser written by hand, fed with text from container labels and notes, is exactly the code this section says to take from a library. It is MIT-licensed and maintained, and it was already installed and hash-pinned in every lockfile before it became a direct dependency, because `rich` (which `typer` needs) depends on it. Raw HTML is turned off in it, and HomelabScribe's own rules decide which links and images are written.

## Obtaining

Dependencies are installed with pip from PyPI by the standard build tooling (`pip install .` / `python -m build`); the Docker image is built from the official `python:3.14-slim` image, pinned by digest in the `Dockerfile` (Renovate proposes digest updates). There are no vendored copies and no committed binaries.

## Pinning

Everything CI, the release workflow and the container image install comes from **hash-pinned lockfiles** in [`requirements/`](../requirements): `runtime.txt` (what the image installs), `ci.txt` (runtime plus test and lint tools), `fuzz.txt` (`ci.txt` plus Atheris, for the fuzz workflow) and `release.txt` (build and twine). They are installed with `pip install --require-hashes`, so a changed or tampered package is refused. The container image does not pip-install the project itself (it is pure Python and runs from its source directory with `PYTHONPATH`), so there is no unpinned install step. The base image is pinned by digest and every GitHub Action by commit SHA. The lockfiles are universal (all supported Python versions and platforms) and are regenerated with [uv](https://docs.astral.sh/uv/); the exact command is in the header uv writes at the top of each file. For example:

```bash
uv pip compile pyproject.toml --extra=dev --universal --generate-hashes --python-version=3.11 --output-file=requirements/ci.txt
```

Renovate reads that header to find each lockfile's source (`pyproject.toml`, or `requirements/release.in` for `release.txt`) and re-runs the same command to update it, so every pull request it opens carries a complete, freshly resolved lockfile with hashes. For that to work the header has to stay as uv writes it: don't pass `--no-header`, write options that take a value with an equals sign (`--extra=dev`, `--python-version=3.11`) and spell out `--output-file=`. Anything else added to the top of a lockfile is lost the next time it is regenerated.

Renovate refreshes all four lockfiles weekly, in one grouped pull request (`lockfile-updates`).

The images used by the pipeline of the GitLab copy (Python, git, gitleaks, actionlint in [`.gitlab-ci.yml`](../.gitlab-ci.yml)) are pinned by version tag and digest. Renovate reads that file and proposes tag and digest updates for them.

## Tracking

- Versions are declared as minimums in `pyproject.toml`; resolved versions appear in each release's SBOM.
- **Renovate** ([`renovate.json`](../renovate.json)) checks every Monday before 6am (`America/Chicago`) and opens labeled update PRs for the Python dependencies and lockfiles, GitHub Actions, the `Dockerfile` base image, the images in `docker-compose.example.yml` and the images in `.gitlab-ci.yml`. GitHub Actions minor and patch updates arrive as one PR, and the CodeQL actions always move together. A fix for a known vulnerability is opened as soon as it is found, labeled `security`. Renovate merges every update itself (patch, minor and major, lockfile refreshes and image rebuilds included) once every required check on the PR has passed; a new version waits three days after its release before it is proposed, while security fixes and image rebuilds (a new digest under the same tag) skip the wait. There are no exceptions. While the `main protection` ruleset still requires an approving review, a Renovate PR waits for the maintainer to merge it.
- **Dependency review** blocks PRs that add known-vulnerable dependencies.
- **CodeQL** and the **OpenSSF Scorecard** run on a schedule and on changes.

## Remediation targets

| Severity of a known vulnerability in a dependency | Target to fix or mitigate |
| --- | --- |
| Critical / High | 14 days |
| Medium | 60 days |
| Low | next regular release |

If a fix is not available upstream, the project documents a mitigation or replaces the dependency. Fixed vulnerabilities are listed under **Security** in the [changelog](../CHANGELOG.md).
