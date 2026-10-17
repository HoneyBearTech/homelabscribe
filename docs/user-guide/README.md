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

Write inside the block. HomelabScribe™ rewrites everything else but copies the block forward. If a service disappears and its page has notes, the page moves to `archive/`.

## Naming and describing things

HomelabScribe recognizes common self-hosted apps by image. Override per container with labels (`homelabscribe.name`, `homelabscribe.description`, `homelabscribe.category`, `homelabscribe.url`, `homelabscribe.hidden: "true"`), or add your own catalog file via `catalog_file` (same format as the built-in `catalog.yaml`).

## Web addresses from Nginx Proxy Manager

Docker only knows ports, not the domain names you reach a service by. Configure `npm:` (see `homelabscribe.example.yaml`) and HomelabScribe™ reads the proxy's list of proxy hosts, matches each to a service by machine and port (or by container name or Docker network alias, when exactly one service answers to it), and uses the domain as that service's address. It also writes `routes.md`, listing every domain and where it goes (unmatched entries are shown as such). A URL set by a label or a Traefik rule always wins. Create an NPM user that can only *view* proxy hosts, and keep its password in an environment variable (`secret: ${NPM_PASSWORD}`).

## Things the scanners can't see

Pi-hole on a Raspberry Pi, a NAS or a router can't be scanned, so describe them in the config under `manual_services` (see `homelabscribe.example.yaml`). They get a normal page, appear in the index (marked "not scanned") and can be listed under `breakglass.critical`. Naming one `pihole` or `adguardhome`, or setting `dns: true`, makes the break-glass guide say it is the house's DNS server and which machine to restart.

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
