---
name: check
description: Run the full pre-commit check for HomelabScribe (ruff lint, format check, pytest, demo render). Use before committing or when asked to verify the project.
allowed-tools: Bash(ruff *) Bash(pytest *) Bash(homelabscribe demo *)
---

Run these in order from the repo root with the virtualenv active, and stop at the first failure:

1. `ruff check .`
2. `ruff format --check .` (if it fails, run `ruff format .` and say which files changed)
3. `pytest -q`
4. `homelabscribe demo -o /tmp/homelabscribe-demo` and confirm `/tmp/homelabscribe-demo/index.md` exists

Report one line per step (pass/fail). On a failure, show the relevant error and propose a fix.
