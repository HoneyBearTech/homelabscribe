# Upgrading

```bash
pipx upgrade homelabscribe          # pip install
docker compose pull && docker compose run --rm homelabscribe   # Docker
```

Read the [changelog](../CHANGELOG.md) first. Before 1.0, minor releases may change configuration or defaults; the changelog says so under **Changed**.

## What state exists

| Location | Contents | Safe to delete? |
| --- | --- | --- |
| `state_dir` (default `.homelabscribe/`, `/data/.homelabscribe` in Docker) | `snapshots/` (last 60 JSON snapshots) and `changelog.json` | Yes, but you lose history that feeds the Pro changelog |
| `output_dir` (default `homelabscribe-docs/`) | Generated Markdown, including **your notes** | **No**: your notes live here |

## Back up and restore

Back up both folders (they are plain files): `tar czf homelabscribe-backup.tgz homelabscribe-docs .homelabscribe`. To restore, extract them back and run `homelabscribe render`. Your notes are inside the generated pages; archived pages keep notes for services that disappeared.

## Default output folder changed

Newer versions default to `homelabscribe-docs/` instead of `docs/`. If you relied on the old default, set `output_dir: docs` in your config or move the folder.
