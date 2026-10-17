"""Read-only Proxmox VE collector. Use an API token with the built-in PVEAuditor role."""

from __future__ import annotations

from typing import Any

from homelabscribe.config import ProxmoxConfig
from homelabscribe.models import Host, Service


def _gb(n: Any) -> float | None:
    try:
        return round(int(n) / 1024**3, 1)
    except (TypeError, ValueError):
        return None


def parse_guest(item: dict[str, Any], cfg: dict[str, Any], node: str, kind: str) -> Service:
    tags = [t for t in str(cfg.get("tags") or item.get("tags") or "").replace(",", ";").split(";") if t]
    nets = sorted(k for k in cfg if k.startswith("net") and k[3:].isdigit())
    return Service(
        name=item.get("name") or f"{kind}-{item.get('vmid')}",
        host=node,
        kind=kind,  # "vm" or "lxc"
        status=item.get("status", "unknown"),
        vmid=int(item["vmid"]) if item.get("vmid") is not None else None,
        cores=cfg.get("cores") or item.get("cpus"),
        memory_gb=_gb(item.get("maxmem")),
        tags=tags,
        networks=nets,
        notes=(cfg.get("description") or None),  # the Proxmox "Notes" panel
    )


class ProxmoxCollector:
    def __init__(self, cfg: ProxmoxConfig, api=None):
        self.cfg = cfg
        self._api = api

    @property
    def api(self):
        if self._api is None:
            from proxmoxer import ProxmoxAPI

            self._api = ProxmoxAPI(
                self.cfg.host,
                port=self.cfg.port,
                user=self.cfg.user,
                token_name=self.cfg.token_name,
                token_value=self.cfg.token_value,
                verify_ssl=self.cfg.verify_ssl,
                timeout=15,
            )
        return self._api

    def _node_addresses(self) -> dict[str, str]:
        """Each node's own IP from the cluster status; empty if the API won't say."""
        try:
            return {e["name"]: e["ip"] for e in self.api.cluster.status.get() if e.get("ip")}
        except Exception:
            return {}

    def collect(self) -> tuple[list[Host], list[Service]]:
        hosts: list[Host] = []
        services: list[Service] = []
        version = (self.api.version.get() or {}).get("version")
        nodes = self.api.nodes.get()
        addresses = self._node_addresses()
        for n in nodes:
            node = n["node"]
            # Without cluster info, the configured host is only a safe guess for a single-node setup.
            address = addresses.get(node) or (self.cfg.host if len(nodes) == 1 else None)
            hosts.append(
                Host(
                    name=node,
                    kind="proxmox-node",
                    address=address,
                    os="Proxmox VE",
                    cpus=n.get("maxcpu"),
                    memory_gb=_gb(n.get("maxmem")),
                    version=version,
                )
            )
            if n.get("status") != "online":
                continue
            for kind, endpoint in (("vm", "qemu"), ("lxc", "lxc")):
                for item in getattr(self.api.nodes(node), endpoint).get():
                    cfg = getattr(self.api.nodes(node), endpoint)(item["vmid"]).config.get()
                    services.append(parse_guest(item, cfg, node, kind))
        return hosts, services
