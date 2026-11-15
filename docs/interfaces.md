# Interfaces

HomelabScribe™ has no network API and listens on no port, so there is no OpenAPI document. Its external interfaces are below.

## Command line

`homelabscribe run | scan | render | doctor | demo | license | version`. Common option: `-c/--config PATH` (env `HOMELABSCRIBE_CONFIG`, default `homelabscribe.yaml`).

| Command | Reads | Writes |
| --- | --- | --- |
| `scan` | config; Docker/Proxmox (read-only) | `state_dir/snapshots/` |
| `render` | latest snapshot | `output_dir/**.md` |
| `run [--every 6h]` | both | both (`--every` is Pro) |
| `doctor [--bundle [-o FILE]]` | config; Docker/Proxmox (connection test); with `--bundle` also the newest snapshot | nothing; with `--bundle` a redacted archive for a bug report |
| `demo [-o DIR]` | bundled sample data | `DIR` |
| `license` | config | nothing (prints license status) |
| `version` | nothing | nothing |

Exit status is non-zero on usage errors. A host, Proxmox cluster or proxy that cannot be read is printed as `error:` and the run continues with the others, using the last known data for the one that failed so a short outage never looks like everything was removed. With `--strict` (`scan` and `run`) the command then exits with status 1, which suits schedulers; docs are still written.

## Configuration file (`homelabscribe.yaml`)

Keys: `output_dir`, `state_dir`, `docker_hosts[]` (`name`, `url`, optional `link_host`: the hostname or IP service links should use, default `name`), `proxmox[]` (`name`, `host`, `port`, `user`, `token_name`, `token_value`, `verify_ssl`), `npm[]` (`name`, `url`, `identity`, `secret`, `verify_ssl`), `manual_services[]` (hand-described services: `name`, `host`, optional `title`, `description`, `category`, `url`, `restart`, `dns`), `notify[]` (Pro; `type` `ntfy` or `discord`, `url`, optional `token`, `on` of `changes` and/or `errors`), `catalog_file`, `license_key`, `breakglass` (`owner`, `contacts[]`, `critical[]`). `${VAR}` values are read from the environment. `HOMELABSCRIBE_LICENSE` supplies the license key. Example: [`homelabscribe.example.yaml`](../homelabscribe.example.yaml).

## Container labels

Set on your containers: `homelabscribe.name`, `homelabscribe.description`, `homelabscribe.category`, `homelabscribe.url`, `homelabscribe.hidden`.

## Files

- Input: config, optional `catalog_file`.
- Output: Markdown pages (`index.md`, `hosts/`, `services/`, `archive/`, `routes.md` when a reverse proxy is configured, and Pro `CHANGELOG.md`, `break-glass.md`, `topology.md`), each ending with `<!-- homelabscribe:notes:start -->` / `<!-- homelabscribe:notes:end -->`.
- State: `state_dir/snapshots/*.json`, `state_dir/changelog.json`.
- Bug-report bundle: `doctor --bundle` runs the same checks as `doctor`, then writes `homelabscribe-bundle-<UTC time>.zip` to the current folder (`-o/--out FILE` picks the file). It holds text files only: `README.txt`, `doctor.txt`, `environment.json`, `config.redacted.yaml` and `snapshot-summary.json`, with names, addresses, secrets and paths masked ([what it holds and what is removed](security.md#the-bug-report-bundle)). An existing file is never overwritten: the command says so and exits with status 1. `--out` without `--bundle` is a usage error. It reads the config, the names of the snapshot files and the newest snapshot, the built-in catalog's categories, the installed package versions, and whether it runs in a container; it creates no folder.

## Network

Outbound only, and only to the Docker, Proxmox and Nginx Proxy Manager endpoints you configure. No telemetry, update checks or license calls.
