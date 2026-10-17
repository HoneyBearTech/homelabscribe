"""Turn raw services into readable ones: friendly names, descriptions, categories and URLs."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import yaml

from homelabscribe.models import Service

_TRAEFIK_HOST = re.compile(r"Host\(`([^`]+)`\)")


def image_base(image: str | None) -> str:
    """lscr.io/linuxserver/sonarr:latest -> sonarr ; postgres:16@sha256:... -> postgres"""
    if not image:
        return ""
    name = image.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    return last.split(":", 1)[0].lower()


def load_catalog(extra: Path | None = None) -> list[dict]:
    text = resources.files("homelabscribe").joinpath("catalog.yaml").read_text()
    entries = yaml.safe_load(text) or []
    if extra and extra.exists():
        # User entries go first so they win.
        entries = (yaml.safe_load(extra.read_text()) or []) + entries
    return entries


def image_owner(image: str | None) -> str:
    """vaultwarden/server:1.32 -> vaultwarden (some images are named "server" or "app")."""
    parts = (image or "").split("@", 1)[0].split(":", 1)[0].split("/")
    return parts[-2].lower() if len(parts) >= 2 else ""


def _match(names: list[str], catalog: list[dict]) -> dict | None:
    for name in names:
        if not name:
            continue
        for entry in catalog:
            if name in entry.get("match", []):
                return entry
    return None


def traefik_url(labels: dict[str, str]) -> str | None:
    for key, value in labels.items():
        if key.startswith("traefik.http.routers.") and key.endswith(".rule"):
            m = _TRAEFIK_HOST.search(value)
            if m:
                return f"https://{m.group(1)}"
    return None


def enrich(services: list[Service], catalog: list[dict]) -> list[Service]:
    for s in services:
        if s.kind == "container":
            entry = _match([image_base(s.image), image_owner(s.image)], catalog)
        else:
            entry = _match([s.name.lower()], catalog)
        if entry:
            s.display_name = entry.get("name")
            s.description = entry.get("description")
            s.category = entry.get("category", "Other")
        elif s.kind in ("vm", "lxc"):
            s.category = "Virtual machines" if s.kind == "vm" else "Containers (LXC)"
            s.description = s.notes.splitlines()[0][:200] if s.notes else None
        s.url = traefik_url(s.labels) or s.url
        # Per-container overrides win over everything.
        lb = s.labels
        s.display_name = lb.get("homelabscribe.name", s.display_name)
        s.description = lb.get("homelabscribe.description", s.description)
        s.category = lb.get("homelabscribe.category", s.category)
        s.url = lb.get("homelabscribe.url", s.url)
        s.hidden = lb.get("homelabscribe.hidden", "").lower() in ("1", "true", "yes")
    return services
