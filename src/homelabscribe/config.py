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


class Config(BaseModel):
    output_dir: Path = Path("homelabscribe-docs")
    state_dir: Path = Path(".homelabscribe")
    docker_hosts: list[DockerHostConfig] = Field(default_factory=lambda: [DockerHostConfig(name="docker")])
    proxmox: list[ProxmoxConfig] = Field(default_factory=list)
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
