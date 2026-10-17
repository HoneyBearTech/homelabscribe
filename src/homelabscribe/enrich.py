"""Turn raw services into readable ones: friendly names, descriptions, categories and URLs."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from urllib.parse import urlparse

import yaml

from homelabscribe.models import Host, Route, Service

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
    _disambiguate(services)
    return services


def _disambiguate(services: list[Service]) -> None:
    """Two containers can map to the same catalog name (e.g. sonarr and sonarr4k, both "Sonarr").

    On one host they would share a title, and with it a page. Keep the plain title for the container
    whose own name matches it and append the container name to the others: "Sonarr (sonarr4k)".
    """
    groups: dict[tuple[str, str], list[Service]] = {}
    for s in services:
        groups.setdefault((s.host, s.title.lower()), []).append(s)
    for group in groups.values():
        if len(group) < 2:
            continue
        for s in group:
            if s.name.lower() != s.title.lower():
                s.display_name = f"{s.title} ({s.name})"


def _host_names(host: Host) -> set[str]:
    names = {host.name.lower()}
    if host.link_host:
        names.add(host.link_host.lower())
    if host.address and (parsed := urlparse(host.address).hostname):
        names.add(parsed.lower())
    return names


def link_routes(routes: list[Route], hosts: list[Host], services: list[Service]) -> None:
    """Match each reverse-proxy route to the service it forwards to, and give that service its URL.

    A route forwards to a host (name or IP) and a port. That is a service when the host is the
    service's machine and the port is one it publishes, or when the host is the container's own
    name (Docker networks resolve it) and the port is the container's port. A URL set by a label
    or Traefik rule always wins.
    """
    names = {h.name: _host_names(h) for h in hosts}
    for route in routes:
        target = route.forward_host.lower()
        match = None
        for s in services:
            published = {p.host for p in s.ports if p.host}
            on_machine = target in names.get(s.host, {s.host.lower()}) and route.forward_port in published
            by_container = target == s.name.lower() and route.forward_port in {p.container for p in s.ports}
            if on_machine or by_container:
                match = s
                break
        if match is None:
            continue
        route.service = match.id
        if match.url is None and route.enabled:
            scheme = "https" if route.https else "http"
            match.url = f"{scheme}://{route.domains[0]}"
