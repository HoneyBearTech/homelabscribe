"""Configuration loaded from homelabscribe.yaml. `${VAR}` values are read from the environment."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class DockerHostConfig(BaseModel):
    name: str
    url: str = "unix:///var/run/docker.sock"
    link_host: str | None = None  # hostname or IP used in generated service links; defaults to `name`


class ProxmoxConfig(BaseModel):
    name: str = "proxmox"
    host: str  # e.g. 192.168.1.10 or pve.lan
    port: int = 8006
    user: str  # e.g. homelabscribe@pve
    token_name: str
    token_value: str
    verify_ssl: bool = True


class BreakGlassConfig(BaseModel):
    owner: str = "the person who runs this lab"
    contacts: list[str] = Field(default_factory=list)  # "Name — phone — what they can help with"
    critical: list[str] = Field(default_factory=list)  # service names that matter most


class NpmConfig(BaseModel):
    """Nginx Proxy Manager. Use a dedicated NPM user with view-only access."""

    name: str = "npm"
    url: str  # the admin UI/API, e.g. http://192.168.1.52:81
    identity: str  # the NPM user's email
    secret: str  # its password; use ${NPM_PASSWORD} so it stays out of the file
    verify_ssl: bool = True


class ManualServiceConfig(BaseModel):
    """Something the scanners can't see (Pi-hole on a Raspberry Pi, a NAS, a router...)."""

    name: str  # unique per host; also used to look up the built-in catalog description
    host: str  # a scanned host's name, or any new name to add a hand-described machine
    title: str | None = None
    description: str | None = None
    category: str | None = None
    url: str | None = None
    restart: str | None = None  # plain-language "how to restart it" for the page and the break-glass guide
    dns: bool = False  # this is the house's DNS server (Pi-hole and AdGuard Home are detected by name)


class Config(BaseModel):
    output_dir: Path = Path("homelabscribe-docs")
    state_dir: Path = Path(".homelabscribe")
    docker_hosts: list[DockerHostConfig] = Field(default_factory=lambda: [DockerHostConfig(name="docker")])
    proxmox: list[ProxmoxConfig] = Field(default_factory=list)
    npm: list[NpmConfig] = Field(default_factory=list)
    manual_services: list[ManualServiceConfig] = Field(default_factory=list)
    catalog_file: Path | None = None  # extra/override catalog entries
    license_key: str | None = None
    breakglass: BreakGlassConfig = Field(default_factory=BreakGlassConfig)


def _expand(value):
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def load_config(path: Path | None) -> Config:
    if path is None or not path.exists():
        return Config()
    data = yaml.safe_load(path.read_text()) or {}
    cfg = Config.model_validate(_expand(data))
    if cfg.license_key is None and os.environ.get("HOMELABSCRIBE_LICENSE"):
        cfg.license_key = os.environ["HOMELABSCRIBE_LICENSE"]
    return cfg
