# Interfaces

HomelabScribe™ has no network API and listens on no port, so there is no OpenAPI document. Its external interfaces are below.

## Command line

`homelabscribe run | scan | render | doctor | demo | license | version`. Common option: `-c/--config PATH` (env `HOMELABSCRIBE_CONFIG`, default `homelabscribe.yaml`).

| Command | Reads | Writes |
| --- | --- | --- |
| `scan` | config; Docker/Proxmox (read-only) | `state_dir/snapshots/` |
| `render` | latest snapshot | `output_dir/**.md`; with `html_dir` also `html_dir/**.html` |
| `run [--every 6h]` | both | both (`--every` is Pro) |
| `doctor` | config; Docker/Proxmox (connection test) | nothing |
| `demo [-o DIR]` | bundled sample data | `DIR` |
| `license` | config | nothing (prints license status) |
| `version` | nothing | nothing |

Exit status is non-zero on usage errors. A host, Proxmox cluster or proxy that cannot be read is printed as `error:` and the run continues with the others, using the last known data for the one that failed so a short outage never looks like everything was removed. With `--strict` (`scan` and `run`) the command then exits with status 1, which suits schedulers; docs are still written.

With `html_dir` set, `render` and `run` (also with `--every`) write a static HTML copy of the pages after the Markdown and report how many HTML files changed. `demo --html DIR` does the same for the sample lab. `html_dir` has to be a folder of its own: if it is `output_dir` or `state_dir`, lies inside one of them or contains one of them (compared after resolving both), the command stops with a message and exit status 1 before it writes anything. A page whose HTML file cannot be written, because a file HomelabScribe did not write is in its place or a symbolic link is in the way, is printed as `skipped:` and the run goes on.

## Configuration file (`homelabscribe.yaml`)

Keys: `output_dir`, `state_dir`, `html_dir` (optional: a folder of its own for a static HTML copy of the pages; nothing is written without it), `docker_hosts[]` (`name`, `url`, optional `link_host`: the hostname or IP service links should use, default `name`), `proxmox[]` (`name`, `host`, `port`, `user`, `token_name`, `token_value`, `verify_ssl`), `npm[]` (`name`, `url`, `identity`, `secret`, `verify_ssl`), `manual_services[]` (hand-described services: `name`, `host`, optional `title`, `description`, `category`, `url`, `restart`, `dns`), `notify[]` (Pro; `type` `ntfy` or `discord`, `url`, optional `token`, `on` of `changes` and/or `errors`), `catalog_file`, `license_key`, `breakglass` (`owner`, `contacts[]`, `critical[]`). `${VAR}` values are read from the environment. `HOMELABSCRIBE_LICENSE` supplies the license key. Example: [`homelabscribe.example.yaml`](../homelabscribe.example.yaml).

## Container labels

Set on your containers: `homelabscribe.name`, `homelabscribe.description`, `homelabscribe.category`, `homelabscribe.url`, `homelabscribe.hidden`.

## Files

- Input: config, optional `catalog_file`.
- Output: Markdown pages (`index.md`, `hosts/`, `services/`, `archive/`, `routes.md` when a reverse proxy is configured, and Pro `CHANGELOG.md`, `break-glass.md`, `topology.md`), each ending with `<!-- homelabscribe:notes:start -->` / `<!-- homelabscribe:notes:end -->`. With `html_dir`, the same pages as HTML files under `html_dir`, with the same paths and the ending `.html` (`index.html`, `hosts/`, `services/`, `archive/`, ...): complete pages with inline styles, no script and no outside request. Only files that carry the export's generator line are ever replaced or removed there.
- State: `state_dir/snapshots/*.json`, `state_dir/changelog.json`.

## Network

Outbound only, and only to the Docker, Proxmox and Nginx Proxy Manager endpoints you configure. No telemetry, update checks or license calls.
