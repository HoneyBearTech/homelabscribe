"""A made-up homelab used by `homelabscribe demo`, the README screenshots and the tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homelabscribe.models import Host, Mount, Port, Service, Snapshot


def _c(name, host, image, ports=(), mounts=(), project=None, deps=(), labels=None, env=(), status="running"):
    return Service(
        name=name,
        host=host,
        kind="container",
        status=status,
        image=image,
        ports=[Port(container=c, host=h) for h, c in ports],
        mounts=[Mount(source=s, destination=d) for s, d in mounts],
        networks=[f"{project}_default"] if project else ["bridge"],
        env_names=sorted(env),
        labels=labels or {},
        restart_policy="unless-stopped",
        compose_project=project,
        compose_service=name if project else None,
        depends_on=list(deps),
    )


def demo_snapshots() -> tuple[Snapshot, Snapshot]:
    """Return (last week, today) so the demo has a changelog."""
    now = datetime(2026, 10, 17, 7, 0, tzinfo=UTC)
    hosts = [
        Host(name="nova", kind="docker", os="Ubuntu 24.04 LTS", cpus=8, memory_gb=31.2, version="27.3.1"),
        Host(
            name="pve1",
            kind="proxmox-node",
            address="192.168.1.10",
            os="Proxmox VE",
            cpus=12,
            memory_gb=62.7,
            version="8.2.4",
        ),
    ]
    media = [
        _c(
            "plex",
            "nova",
            "lscr.io/linuxserver/plex:latest",
            [(32400, 32400)],
            [("/mnt/media", "/media"), ("plex_config", "/config")],
            "media",
        ),
        _c(
            "sonarr",
            "nova",
            "lscr.io/linuxserver/sonarr:latest",
            [(8989, 8989)],
            [("/mnt/media/tv", "/tv"), ("/mnt/downloads", "/downloads")],
            "media",
            ["prowlarr", "qbittorrent"],
            env=["PUID", "PGID", "TZ"],
        ),
        _c(
            "radarr",
            "nova",
            "lscr.io/linuxserver/radarr:latest",
            [(7878, 7878)],
            [("/mnt/media/movies", "/movies"), ("/mnt/downloads", "/downloads")],
            "media",
            ["prowlarr", "qbittorrent"],
            env=["PUID", "PGID", "TZ"],
        ),
        _c("prowlarr", "nova", "lscr.io/linuxserver/prowlarr:latest", [(9696, 9696)], [], "media"),
        _c(
            "qbittorrent",
            "nova",
            "lscr.io/linuxserver/qbittorrent:latest",
            [(8080, 8080)],
            [("/mnt/downloads", "/downloads")],
            "media",
            env=["WEBUI_PORT"],
        ),
        _c(
            "overseerr",
            "nova",
            "lscr.io/linuxserver/overseerr:latest",
            [(5055, 5055)],
            [],
            "media",
            ["plex"],
        ),
    ]
    infra = [
        _c(
            "pihole",
            "nova",
            "pihole/pihole:2024.07.0",
            [(53, 53), (8053, 80)],
            [("pihole_etc", "/etc/pihole")],
            "dns",
            env=["TZ", "WEBPASSWORD"],
        ),
        _c(
            "traefik",
            "nova",
            "traefik:v3.1",
            [(80, 80), (443, 443)],
            [("/var/run/docker.sock", "/var/run/docker.sock")],
            "proxy",
        ),
        _c(
            "vaultwarden",
            "nova",
            "vaultwarden/server:1.32.0",
            [],
            [("vw_data", "/data")],
            "vault",
            labels={"traefik.http.routers.vw.rule": "Host(`vault.home.example`)"},
            env=["ADMIN_TOKEN", "DOMAIN"],
        ),
        _c(
            "grafana",
            "nova",
            "grafana/grafana:11.2.0",
            [(3000, 3000)],
            [("grafana_data", "/var/lib/grafana")],
            "monitoring",
            ["prometheus"],
        ),
        _c(
            "prometheus",
            "nova",
            "prom/prometheus:v2.54.1",
            [(9090, 9090)],
            [("prom_data", "/prometheus")],
            "monitoring",
        ),
    ]
    guests = [
        Service(
            name="homeassistant",
            host="pve1",
            kind="vm",
            status="running",
            vmid=101,
            cores=2,
            memory_gb=4.0,
            tags=["home"],
            notes="Home Assistant OS. Zigbee stick passed through on USB 2.",
        ),
        Service(
            name="docker-nova",
            host="pve1",
            kind="vm",
            status="running",
            vmid=102,
            cores=8,
            memory_gb=32.0,
            tags=["docker"],
            notes="Ubuntu VM that runs all Docker containers (nova).",
        ),
        Service(
            name="pbs",
            host="pve1",
            kind="lxc",
            status="stopped",
            vmid=200,
            cores=2,
            memory_gb=2.0,
            tags=["backup"],
            notes="Proxmox Backup Server, test install.",
        ),
    ]
    old = Snapshot(
        taken_at=now - timedelta(days=7),
        hosts=hosts,
        services=[s.model_copy(deep=True) for s in media[:-1] + infra + guests],
    )
    for s in old.services:
        if s.name == "grafana":
            s.image = "grafana/grafana:11.1.4"
    new = Snapshot(taken_at=now, hosts=hosts, services=media + infra + guests)
    return old, new
