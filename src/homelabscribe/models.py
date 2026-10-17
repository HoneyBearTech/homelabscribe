"""Normalized data model. Every collector produces these; every renderer reads them."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

SNAPSHOT_VERSION = 1


class Port(BaseModel):
    container: int
    protocol: str = "tcp"
    host: int | None = None
    host_ip: str | None = None

    def label(self) -> str:
        if self.host is None:
            return f"{self.container}/{self.protocol} (internal)"
        return f"{self.host} → {self.container}/{self.protocol}"


class Mount(BaseModel):
    source: str
    destination: str
    type: str = "bind"
    read_only: bool = False


class Host(BaseModel):
    name: str
    kind: Literal["docker", "proxmox-node", "manual"]
    address: str | None = None
    link_host: str | None = None  # what service links should point at (hostname or IP)
    os: str | None = None
    cpus: int | None = None
    memory_gb: float | None = None
    version: str | None = None

    @property
    def type_label(self) -> str:
        return {"docker": "Docker host", "proxmox-node": "Proxmox node"}.get(self.kind, "Added by hand")


class Service(BaseModel):
    """A container, VM or LXC. `name` is unique per host."""

    name: str
    host: str
    kind: Literal["container", "vm", "lxc", "manual"]
    status: str = "unknown"
    image: str | None = None
    ports: list[Port] = Field(default_factory=list)
    mounts: list[Mount] = Field(default_factory=list)
    networks: list[str] = Field(default_factory=list)
    env_names: list[str] = Field(default_factory=list)  # names only, never values
    labels: dict[str, str] = Field(default_factory=dict)  # sensitive labels removed
    restart_policy: str | None = None
    compose_project: str | None = None
    compose_service: str | None = None
    aliases: list[str] = Field(default_factory=list)  # extra DNS names on its Docker networks
    depends_on: list[str] = Field(default_factory=list)
    # Proxmox guests
    vmid: int | None = None
    cores: int | None = None
    memory_gb: float | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    # Hand-described services (config `manual_services`)
    restart_hint: str | None = None
    is_dns: bool = False
    # Filled in by enrichment
    display_name: str | None = None
    description: str | None = None
    category: str = "Other"
    url: str | None = None
    hidden: bool = False

    @property
    def id(self) -> str:
        return f"{self.host}/{self.name}"

    @property
    def title(self) -> str:
        return self.display_name or self.name


class Route(BaseModel):
    """A reverse-proxy entry: these domain names are forwarded to a host and port."""

    proxy: str  # name of the proxy it came from (config `npm[].name`)
    domains: list[str]
    forward_scheme: str = "http"
    forward_host: str
    forward_port: int
    https: bool = False  # the proxy serves these names over HTTPS
    enabled: bool = True
    service: str | None = None  # `Service.id` this route was matched to, if any


class Snapshot(BaseModel):
    version: int = SNAPSHOT_VERSION
    taken_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    hosts: list[Host] = Field(default_factory=list)
    services: list[Service] = Field(default_factory=list)
    routes: list[Route] = Field(default_factory=list)

    def visible_services(self) -> list[Service]:
        return sorted((s for s in self.services if not s.hidden), key=lambda s: (s.category, s.title.lower()))
