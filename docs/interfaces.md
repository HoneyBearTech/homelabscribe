# Interfaces

HomelabScribe™ has no network API and listens on no port, so there is no OpenAPI document. Its external interfaces are below.

## Command line

`homelabscribe run | scan | render | doctor | demo | license | version`. Common option: `-c/--config PATH` (env `HOMELABSCRIBE_CONFIG`, default `homelabscribe.yaml`).

| Command | Reads | Writes |
| --- | --- | --- |
| `scan` | config; Docker/Proxmox (read-only) | `state_dir/snapshots/` |
| `render` | latest snapshot | `output_dir/**.md` |
| `run [--every 6h]` | both | both (`--every` is Pro) |
| `doctor` | config; Docker/Proxmox (connection test) | nothing |
| `demo [-o DIR]` | bundled sample data | `DIR` |
| `license` | config | nothing (prints license status) |
| `version` | nothing | nothing |

Exit status is non-zero on usage errors; per-host collection errors are printed and the run continues with the other hosts.

## Configuration file (`homelabscribe.yaml`)

Keys: `output_dir`, `state_dir`, `docker_hosts[]` (`name`, `url`, optional `link_host`: the hostname or IP service links should use, default `name`), `proxmox[]` (`name`, `host`, `port`, `user`, `token_name`, `token_value`, `verify_ssl`), `manual_services[]` (hand-described services: `name`, `host`, optional `title`, `description`, `category`, `url`, `restart`, `dns`), `catalog_file`, `license_key`, `breakglass` (`owner`, `contacts[]`, `critical[]`). `${VAR}` values are read from the environment. `HOMELABSCRIBE_LICENSE` supplies the license key. Example: [`homelabscribe.example.yaml`](../homelabscribe.example.yaml).

## Container labels

Set on your containers: `homelabscribe.name`, `homelabscribe.description`, `homelabscribe.category`, `homelabscribe.url`, `homelabscribe.hidden`.

## Files

- Input: config, optional `catalog_file`.
- Output: Markdown pages (`index.md`, `hosts/`, `services/`, `archive/`, and Pro `CHANGELOG.md`, `break-glass.md`, `topology.md`), each ending with `<!-- homelabscribe:notes:start -->` / `<!-- homelabscribe:notes:end -->`.
- State: `state_dir/snapshots/*.json`, `state_dir/changelog.json`.

## Network

Outbound only, and only to the Docker/Proxmox endpoints you configure. No telemetry, update checks or license calls.
