<!-- Source for the Docker Hub repository overview (docker.io/honeybeartech/homelabscribe). Paste it into
     Docker Hub > the repository > Overview. Images use absolute URLs because Docker Hub cannot resolve
     relative paths. -->

<p align="center"><img src="https://homelabscribe.com/assets/banner.png" alt="HomelabScribe: documentation for your homelab that writes itself, and keeps your notes" width="100%"></p>

# HomelabScribe™

**Documentation for your homelab that writes itself, and keeps your notes.**

HomelabScribe™ scans your Docker hosts (and, with Pro, your Proxmox cluster) and writes readable Markdown: one page per service, one per host, and an overview. Run it again tomorrow and the docs update themselves, while anything you wrote in a page's **Notes** section stays exactly as you left it. The output drops straight into an Obsidian vault, a Git repo or any Markdown viewer.

- **Read-only.** It never changes a container, VM or host.
- **Private.** Runs entirely on your network. No telemetry, no update checks, no license calls.
- **No secrets on disk.** Environment variable *names* are recorded, never their values.

![Overview page](https://homelabscribe.com/assets/screenshot-overview.png)

## Quick start

HomelabScribe reaches Docker through a [socket proxy](https://github.com/Tecnativa/docker-socket-proxy) that only allows read calls. Save this as `docker-compose.yml`:

```yaml
services:
  socket-proxy:
    image: tecnativa/docker-socket-proxy:latest
    restart: unless-stopped
    environment:
      CONTAINERS: 1   # read container details
      INFO: 1         # read host info
      POST: 0         # nothing can be changed
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro

  homelabscribe:
    image: honeybeartech/homelabscribe:latest
    depends_on: [socket-proxy]
    user: "1000:1000"   # must be able to write to the docs and state folders
    volumes:
      - ./config:/config:ro                    # contains homelabscribe.yaml
      - ./state:/data/.homelabscribe           # snapshots and change history
      - /path/to/your/vault/Homelab:/docs      # where the docs are written
```

Create the folders, then `config/homelabscribe.yaml` (a full example is [in the repository](https://github.com/HoneyBearTech/homelabscribe/blob/main/homelabscribe.example.yaml)):

```bash
mkdir -p config state && sudo chown 1000:1000 state
```

```yaml
output_dir: /docs
state_dir: /data/.homelabscribe
docker_hosts:
  - name: myserver
    url: tcp://socket-proxy:2375
```

Then run it once, or from cron:

```bash
docker compose run --rm homelabscribe
```

Want to see the output first? `pipx install homelabscribe && homelabscribe demo` renders a made-up lab, no Docker access needed.

## Tags

| Tag | Meaning |
| --- | --- |
| `X.Y.Z` | An exact release, for example `0.2.1` |
| `X.Y` | The newest patch release of that minor version |
| `latest` | The newest release |

Images are built for `linux/amd64` and `linux/arm64`, run as a non-root user (uid 1000), and are also published to GitHub Container Registry (`ghcr.io/honeybeartech/homelabscribe`). Both are the same image: identical digest.

## Volumes and configuration

| Path in the container | Purpose |
| --- | --- |
| `/config` | `homelabscribe.yaml` (set `HOMELABSCRIBE_CONFIG` to use another path) |
| `/docs` | The generated Markdown. Put your Obsidian vault folder here |
| `/data` | Working directory; state lives in `/data/.homelabscribe` |

Optional: `HOMELABSCRIBE_LICENSE` for a Pro key. Commands: `run` (default), `scan`, `render`, `doctor`, `demo`, `version`; add `--strict` to exit non-zero when a host can't be read.

## Free and Pro

Free covers one Docker host with service, host and overview pages and your preserved notes. Pro adds unlimited hosts, Proxmox, the changelog, a plain-English break-glass guide for family, the topology diagram and a built-in scheduler. Licenses are keys checked offline. Details: [homelabscribe.com](https://homelabscribe.com).

## Verify what you pull

Every release image has a signed build attestation tying it to the release workflow in this repository:

```bash
gh attestation verify oci://docker.io/honeybeartech/homelabscribe:X.Y.Z --repo HoneyBearTech/homelabscribe
```

More in [verifying releases](https://github.com/HoneyBearTech/homelabscribe/blob/main/docs/verifying-releases.md).

## Links

[Website](https://homelabscribe.com) · [Source and docs](https://github.com/HoneyBearTech/homelabscribe) · [Changelog](https://github.com/HoneyBearTech/homelabscribe/blob/main/CHANGELOG.md) · [Report a problem](https://github.com/HoneyBearTech/homelabscribe/issues) · [Security policy](https://github.com/HoneyBearTech/homelabscribe/blob/main/SECURITY.md)

## License

The core is [AGPL-3.0](https://github.com/HoneyBearTech/homelabscribe/blob/main/LICENSE). The Pro features are source-available under a separate license. HomelabScribe™ and its logo are trademarks of HoneyBearTech; the code licenses do not grant rights to the name or logo.
