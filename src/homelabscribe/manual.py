"""Services described by hand in the config, for things no scanner can see."""

from __future__ import annotations

from homelabscribe.config import ManualServiceConfig
from homelabscribe.models import Host, Service

NOT_SCANNED = "not scanned"
DNS_NAMES = frozenset({"pihole", "pi-hole", "adguardhome"})


def build(entries: list[ManualServiceConfig], known_hosts: set[str]) -> tuple[list[Host], list[Service]]:
    """Return the hand-described services, plus a host for every machine not already scanned."""
    hosts: list[Host] = []
    services: list[Service] = []
    for e in entries:
        if e.host not in known_hosts:
            known_hosts.add(e.host)
            hosts.append(Host(name=e.host, kind="manual"))
        # Reuse the per-container label overrides so these win over the built-in catalog.
        overrides = {
            "homelabscribe.name": e.title,
            "homelabscribe.description": e.description,
            "homelabscribe.category": e.category,
            "homelabscribe.url": e.url,
        }
        services.append(
            Service(
                name=e.name,
                host=e.host,
                kind="manual",
                status=NOT_SCANNED,
                labels={k: v for k, v in overrides.items() if v},
                restart_hint=e.restart,
                is_dns=e.dns or e.name.lower() in DNS_NAMES,
            )
        )
    return hosts, services
