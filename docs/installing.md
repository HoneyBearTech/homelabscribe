# Installing

Requirements: Python 3.11+ (pip/pipx) or Docker. HomelabScribe™ only needs *read* access to Docker, and read-only API access to Proxmox.

## pipx

```bash
pipx install homelabscribe
homelabscribe --help
```

## Docker Compose

The image is published to both GitHub Container Registry (`ghcr.io/honeybeartech/homelabscribe`) and Docker Hub (`honeybeartech/homelabscribe`); they are the same image. The example compose file uses GHCR; swap the image name if you prefer Docker Hub.

HomelabScribe reaches Docker through a [socket proxy](https://github.com/Tecnativa/docker-socket-proxy) that only allows read calls.

```bash
mkdir homelabscribe && cd homelabscribe
curl -O https://raw.githubusercontent.com/HoneyBearTech/homelabscribe/main/docker-compose.example.yml
curl -O https://raw.githubusercontent.com/HoneyBearTech/homelabscribe/main/homelabscribe.example.yaml
mv docker-compose.example.yml docker-compose.yml
mkdir config state && mv homelabscribe.example.yaml config/homelabscribe.yaml
sudo chown 1000:1000 state
docker compose run --rm homelabscribe
```

The image runs as a non-root user (uid 1000). The folder mounted at `/docs` must be writable by that user.

To scan a *remote* host, run the proxy there with `POST=0`, publish port 2375 **bound to the host's LAN IP only**, and set `url: tcp://<lan-ip>:2375`. Never expose that port beyond a network you trust: the Docker API has no authentication ([security](security.md)).

## Proxmox (Pro)

Create a read-only token with the `PVEAuditor` role:

```bash
pveum user add homelabscribe@pve
pveum acl modify / --users homelabscribe@pve --roles PVEAuditor
pveum user token add homelabscribe@pve docs --privsep 0
```

Put the token value in an environment variable and reference it as `${PVE_TOKEN}` in the config.

## Verify what you downloaded

See [verifying releases](verifying-releases.md).
