# User guide

## What you get

`index.md` (every host and service by category), `hosts/<host>.md`, `services/<service>.md`. Pro adds `CHANGELOG.md`, `break-glass.md` and `topology.md`.

## Your notes

Every page ends with:

```markdown
<!-- homelabscribe:notes:start -->
Your notes here.
<!-- homelabscribe:notes:end -->
```

Write inside the block. HomelabScribe rewrites everything else but copies the block forward. If a service disappears and its page has notes, the page moves to `archive/`.

## Naming and describing things

HomelabScribe recognizes common self-hosted apps by image. Override per container with labels (`homelabscribe.name`, `homelabscribe.description`, `homelabscribe.category`, `homelabscribe.url`, `homelabscribe.hidden: "true"`), or add your own catalog file via `catalog_file` (same format as the built-in `catalog.yaml`).

## Pro features

Multiple Docker hosts, Proxmox VMs/LXCs, the changelog page, the break-glass guide (configure `breakglass:`), the topology diagram and `--every` scheduling. Pro is unlocked by a license key checked offline ([security](../security.md)).

## Commands

| Command | What it does |
| --- | --- |
| `homelabscribe run` | Scan, then write docs |
| `homelabscribe scan` | Take a snapshot only |
| `homelabscribe render` | Rewrite docs from the latest snapshot |
| `homelabscribe doctor` | Check config, connections and license; safe to paste into an issue |
| `homelabscribe demo` | Render a sample homelab |

## Keeping it up to date

Run from cron (`0 3 * * * homelabscribe run -c /path/homelabscribe.yaml`) or, with Pro, `homelabscribe run --every 1d`.
