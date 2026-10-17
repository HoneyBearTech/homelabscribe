---
name: release
description: Cut a HomelabScribe release - bump the version, tag it, and push so GitHub Actions publishes the Docker image and PyPI package.
argument-hint: "[version, e.g. 0.2.0]"
disable-model-invocation: true
---

Release version `$ARGUMENTS`.

1. Confirm the working tree is clean and on `main`, and that `/check` passes.
2. Set the version in both `pyproject.toml` (`version = ...`) and `src/homelabscribe/__init__.py`
   (`__version__ = ...`). They must match.
3. Commit: `Release v$ARGUMENTS`.
4. Show the user the commit and the tag command, and **ask for confirmation before pushing**:
   `git tag v$ARGUMENTS && git push origin main --tags`
5. After the push, tell the user to watch the "Release" workflow in GitHub Actions. It builds the
   multi-arch image to `ghcr.io/honeybeartech/homelabscribe`, publishes to PyPI (needs the one-time
   trusted-publisher setup on pypi.org for this repo and the `pypi` environment) and creates the GitHub release.
