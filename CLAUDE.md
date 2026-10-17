# HomelabScribe

Documentation for homelabs that writes itself. Scans Docker hosts (and Proxmox, Pro) and writes
readable Markdown docs (Obsidian-friendly) that refresh on every run while preserving the user's
own notes. Open-core side project by HoneyBearTech.

## Before making structural changes

Read the project notes first. They live outside this repo, in the owner's Obsidian vault **Chronos** at
`~/Chronos/Projects/HomelabScribe/` (every file prefixed `homelabscribe-`):
- `homelabscribe-roadmap.md`: phases, checkboxes, OpenSSF phase, Icebox
- `homelabscribe-Pass-Map.md`: delivery log; add a row when a pass ships
- `homelabscribe-Architecture.md`, `homelabscribe-Security-Considerations.md` (load-bearing), `homelabscribe-Decisions-Log.md`

Update them as work lands (tick roadmap boxes, add dated Decisions-Log entries). **Notes never go into
this repo**; only public, user-facing docs go in `docs/`. `.obsidian-docs/` stays gitignored. Chronos is its
own private git repo: only commit or push it when the owner asks. Never touch `~/Chronos/H/`.

## Commands

```bash
source .venv/bin/activate          # Python 3.11+ (macOS: python3 -m venv .venv && pip install -e '.[dev]')
pytest -q                          # all tests; must pass before every commit
ruff check . && ruff format .      # lint + format (CI runs `ruff format --check`)
homelabscribe demo -o homelabscribe-demo   # render the made-up demo lab with every Pro feature on
homelabscribe run -c homelabscribe.yaml    # scan + render using the local (git-ignored) config
homelabscribe doctor -c homelabscribe.yaml # connectivity + license check
```

Run `/check` to do lint, format, tests and the demo render in one go.

## Architecture

Pipeline: **collectors → enrichment → snapshot (JSON) → diff → renderers**.

| Module | Role |
| --- | --- |
| `collectors/docker.py`, `collectors/proxmox.py` | Read-only data collection. Pure `parse_*` functions do the work so tests never need a daemon. A new source = one new collector with `collect() -> (hosts, services)`. |
| `models.py` | Pydantic models: `Host`, `Service`, `Snapshot`. The contract between every stage. |
| `redact.py` | Env var **names only**, sensitive labels and htpasswd hashes dropped. |
| `enrich.py` + `catalog.yaml` | Friendly names, plain-English descriptions, categories, Traefik URLs, `homelabscribe.*` label overrides. Matches image base name, then image owner. |
| `store.py` | Snapshots as timestamped JSON in `state_dir/snapshots/` (keeps 60) and `changelog.json`. |
| `diff.py` | added / moved / changed / removed between snapshots. |
| `notes.py` | The `<!-- homelabscribe:notes:start/end -->` block that survives every re-render. |
| `renderer.py` + `templates/` | Jinja2 → Markdown. Writes only changed files; archives removed pages that hold notes. |
| `pro/` | Pro-only templates (changelog, break-glass, topology). **Separate commercial license.** |
| `licensing.py` | Offline Ed25519 license keys (`LS1.<payload>.<sig>`); `features()` returns enabled Pro features. |
| `pipeline.py` | `scan()` and `render()`; enforces Free-tier limits (1 Docker host, no Proxmox). |
| `cli.py` | Typer CLI. `demo.py` holds the sample lab used by the demo and tests. |

## Rules

- **Read-only, always.** Collectors must never call anything that changes a container, VM or host.
- **No secrets on disk.** Never store env var values, tokens or passwords in snapshots or docs.
  `tests/test_collectors.py::test_secrets_never_stored` must keep passing; extend it when adding fields.
- **No phone-home.** No telemetry, update checks or license calls over the network.
- **Never read, print or commit `private_key.pem`** (the license signing key) or any `*.pem`.
  `homelabscribe.yaml` is git-ignored because it contains the owner's Pro key; don't commit it either.
- **Licensing boundary:** code in `src/homelabscribe/pro/` is under `LICENSE-COMMERCIAL.md`; everything
  else is AGPL-3.0. New Pro features: add the flag to `PRO_FEATURES`, gate it in `pipeline.py` or
  `renderer.py`, and put any Pro-only templates in `pro/templates/`.
- Templates use `trim_blocks`/`lstrip_blocks`; check rendered output with `homelabscribe demo` after edits.
  Every generated page must end with the notes markers.
- Tests: pytest with fakes (see `FakeDockerClient`, `_Endpoint`). No network or Docker daemon in tests.
- Style: ruff, line length 110, Python 3.11+ (`datetime.UTC`, `X | None`).
- Public docs live in `docs/` (tracked). Generated output defaults to `homelabscribe-docs/` (git-ignored).
- Commits: small, imperative subject lines, signed off (`git commit -s`, DCO). Release by tagging `vX.Y.Z` (see `/release`).

## Context

- Owner: HoneyBearTech (GitHub). Repo is private until launch, then public. Treat everything
  committed as public: no real IPs, hostnames, emails or keys. Personal and lab-specific notes
  belong in `CLAUDE.local.md` (git-ignored), not here.
- Commit with the GitHub noreply email (`31805425+HoneyBearTech@users.noreply.github.com`).
