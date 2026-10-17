# Architecture

## Actors and actions

| Actor | Does |
| --- | --- |
| **Operator** | Writes `homelabscribe.yaml`, runs `homelabscribe run` (or schedules it), reads and annotates the Markdown |
| **HomelabScribe** | Reads Docker/Proxmox state, writes snapshots and Markdown. Never changes a monitored system |
| **Docker daemon / socket proxy** | Answers read-only API calls (containers, info, networks) |
| **Proxmox API** | Answers read-only calls for VMs and LXCs (Pro) |
| **Maintainer** | Signs Pro license keys offline with a private key that is never in the repository |

## Data flow

```
config ─▶ collectors ─▶ redact ─▶ enrich ─▶ snapshot (JSON) ─▶ diff ─▶ renderers ─▶ Markdown
          (Docker,                   (catalog,      (state_dir)      (added/moved/    (Jinja2,
           Proxmox)                   labels)                         changed/removed) notes kept)
```

| Module | Role |
| --- | --- |
| `collectors/` | Read-only collection; pure `parse_*` functions for testing |
| `models.py` | Pydantic `Host`, `Service`, `Snapshot` shared by all stages |
| `redact.py` | Keeps env var **names** only; drops sensitive labels and htpasswd hashes |
| `enrich.py`, `catalog.yaml` | Friendly names, descriptions, categories, `homelabscribe.*` label overrides |
| `store.py` | Timestamped snapshots (keeps 60) and `changelog.json` |
| `diff.py` | Changes between snapshots |
| `notes.py` | The `homelabscribe:notes` block preserved across renders |
| `renderer.py`, `templates/` | Markdown output; writes only changed files; archives removed pages that hold notes |
| `pro/` | Pro-only templates under a separate license |
| `licensing.py` | Offline Ed25519 license verification |
| `pipeline.py`, `cli.py` | Orchestration, Free-tier limits, command line |

## Deployment shape

A single process or container, run on demand, from cron or (Pro) on an `--every` schedule. No server, no database, no listening port. Container: non-root, three volumes (`/config`, `/docs`, `/data`).
