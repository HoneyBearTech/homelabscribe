# Contributing to HomelabScribe™

Thanks for helping. This page covers how to report problems, propose changes and get a pull request merged.

## Reporting bugs and asking for features

Use [GitHub Issues](https://github.com/HoneyBearTech/homelabscribe/issues) with the bug or feature template. For a bug include your HomelabScribe version, how you run it (pip or Docker) and the output of `homelabscribe doctor` (safe to paste: it contains no secret values). **Security problems go through [SECURITY.md](SECURITY.md), not Issues.**

Questions and ideas that are not bugs: see [SUPPORT.md](SUPPORT.md).

## Development setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest -q                     # tests must pass
ruff check . && ruff format . # lint + format (CI runs `ruff format --check`)
homelabscribe demo -o /tmp/demo   # confirm the demo still renders
```

Python 3.11 or newer. Line length is 110.

## Building from source

Requirements: Python 3.11+, `pip`, and (optional) Docker for the image. Runtime dependencies are declared in `pyproject.toml` and installed by `pip`; there is no other SDK or system library to install.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'      # editable install with test and lint tools
python -m pip install build && python -m build   # wheel and sdist into dist/
docker build -t homelabscribe .  # container image
```

When you change dependencies in `pyproject.toml`, regenerate the hash-pinned lockfiles in `requirements/` (commands in [docs/dependencies.md](docs/dependencies.md)) and commit them with the change.

## What a pull request must have

1. **Tests.** New behavior ships with tests; bug fixes with a test that failed before. New parsers also get a property-based test in `tests/test_fuzz_parsers.py` (Hypothesis). Tests use fakes: no network and no Docker daemon. Bug fixes add a regression test; the project aims for at least half of its bug fixes to carry one, and every change must keep the suite green. CI enforces a coverage floor of 80%.
2. **Green CI.** Lint, format check, tests (Python 3.11-3.13), CodeQL and dependency review must pass.
3. **DCO sign-off** on every commit (`git commit -s`), certifying the [Developer Certificate of Origin](https://developercertificate.org/). A bot-free check in CI fails PRs with unsigned commits.
4. **Small, focused commits** with imperative subject lines.
5. **Docs and changelog** updated when behavior changes (`docs/`, `CHANGELOG.md` under "Unreleased").

## Rules the project holds itself to

- **Read-only.** Collectors must never call anything that changes a container, VM or host.
- **No secrets on disk.** Never store env var values, tokens or passwords in snapshots or docs. Extend `tests/test_collectors.py::test_secrets_never_stored` when adding fields.
- **No phone-home.** No telemetry, update checks or license calls over the network.
- **Licensing boundary.** Everything is AGPL-3.0-only except `src/homelabscribe/pro/`, which is under the separate [commercial license](src/homelabscribe/pro/LICENSE-COMMERCIAL.md). Contributions outside `pro/` are accepted under AGPL-3.0-only. Please open an issue before changing anything in `pro/`.
- **No generated binaries** or credentials in commits.

## Adding an app to the catalog

Add an entry to `src/homelabscribe/catalog.yaml` (friendly name, description, category) and a test if matching is non-obvious.

## Conduct

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). Project decisions follow [GOVERNANCE.md](GOVERNANCE.md).
