"""Read-only Docker collector. Point it at a docker-socket-proxy for least privilege."""

from __future__ import annotations

from typing import Any

from homelabscribe.config import DockerHostConfig
from homelabscribe.models import Host, Mount, Port, Service
from homelabscribe.redact import clean_labels, env_names


def parse_ports(raw: dict[str, Any] | None) -> list[Port]:
    """Docker's NetworkSettings.Ports: {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]}."""
    ports: dict[tuple, Port] = {}
    for spec, bindings in (raw or {}).items():
        num, _, proto = spec.partition("/")
        if not bindings:
            p = Port(container=int(num), protocol=proto or "tcp")
            ports.setdefault((p.container, p.protocol, None), p)
            continue
        for b in bindings:
            host_port = int(b["HostPort"]) if b.get("HostPort") else None
            p = Port(container=int(num), protocol=proto or "tcp", host=host_port, host_ip=b.get("HostIp"))
            # Docker lists IPv4 and IPv6 bindings separately; keep one.
            ports.setdefault((p.container, p.protocol, p.host), p)
    return sorted(ports.values(), key=lambda p: (p.host or 0, p.container))


def parse_depends_on(value: str | None) -> list[str]:
    """com.docker.compose.depends_on: "db:service_started:false,redis:service_healthy:true"."""
    if not value:
        return []
    return sorted({part.split(":", 1)[0] for part in value.split(",") if part})


def parse_container(attrs: dict[str, Any], host: str) -> Service:
    cfg = attrs.get("Config") or {}
    net = attrs.get("NetworkSettings") or {}
    raw_labels = cfg.get("Labels") or {}
    live_ports = net.get("Ports")
    if not live_ports:
        # Stopped containers report no live bindings; fall back to what's configured.
        live_ports = {k: None for k in cfg.get("ExposedPorts") or {}}
        live_ports.update((attrs.get("HostConfig") or {}).get("PortBindings") or {})
    return Service(
        name=(attrs.get("Name") or "").lstrip("/") or attrs.get("Id", "")[:12],
        host=host,
        kind="container",
        status=(attrs.get("State") or {}).get("Status", "unknown"),
        image=cfg.get("Image"),
        ports=parse_ports(live_ports),
        mounts=[
            Mount(
                source=m.get("Name") or m.get("Source", ""),
                destination=m.get("Destination", ""),
                type=m.get("Type", "bind"),
                read_only=not m.get("RW", True),
            )
            for m in attrs.get("Mounts") or []
        ],
        networks=sorted((net.get("Networks") or {}).keys()),
        env_names=env_names(cfg.get("Env")),
        labels=clean_labels(raw_labels),
        restart_policy=((attrs.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name") or None,
        compose_project=raw_labels.get("com.docker.compose.project"),
        compose_service=raw_labels.get("com.docker.compose.service"),
        depends_on=parse_depends_on(raw_labels.get("com.docker.compose.depends_on")),
    )


def parse_host(info: dict[str, Any], name: str, url: str, link_host: str | None = None) -> Host:
    mem = info.get("MemTotal")
    return Host(
        name=name,
        kind="docker",
        address=None if url.startswith("unix://") else url,
        link_host=link_host,
        os=info.get("OperatingSystem"),
        cpus=info.get("NCPU"),
        memory_gb=round(mem / 1024**3, 1) if mem else None,
        version=info.get("ServerVersion"),
    )


class DockerCollector:
    def __init__(self, cfg: DockerHostConfig, client=None):
        self.cfg = cfg
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import docker  # imported lazily so tests don't need a daemon

            self._client = docker.DockerClient(base_url=self.cfg.url, timeout=15)
        return self._client

    def collect(self) -> tuple[list[Host], list[Service]]:
        # Low-level API returns the same attrs as `docker inspect` without extra calls.
        api = self.client.api
        host = parse_host(api.info(), self.cfg.name, self.cfg.url, self.cfg.link_host)
        services = [
            parse_container(api.inspect_container(c["Id"]), self.cfg.name) for c in api.containers(all=True)
        ]
        return [host], services
