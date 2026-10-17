"""Nginx Proxy Manager collector: which domain names point at which host and port.

It only reads. Logging in creates a short-lived session token on NPM and nothing else, and the
token is never stored. Use an NPM user whose role can only view proxy hosts.
"""

from __future__ import annotations

from typing import Any

from homelabscribe.config import NpmConfig
from homelabscribe.models import Route


def parse_proxy_hosts(items: list[dict[str, Any]], proxy: str) -> list[Route]:
    """NPM's /api/nginx/proxy-hosts: [{"domain_names": [...], "forward_host": ..., ...}]."""
    routes = []
    for item in items:
        domains = [d for d in item.get("domain_names") or [] if d]
        host, port = item.get("forward_host"), item.get("forward_port")
        if not domains or not host or port is None:
            continue
        routes.append(
            Route(
                proxy=proxy,
                domains=domains,
                forward_scheme=item.get("forward_scheme") or "http",
                forward_host=str(host),
                forward_port=int(port),
                https=bool(item.get("certificate_id")) and item.get("certificate_id") != "new",
                enabled=bool(item.get("enabled", True)),
            )
        )
    return routes


class NpmCollector:
    def __init__(self, cfg: NpmConfig, session=None):
        self.cfg = cfg
        self._session = session

    @property
    def session(self):
        if self._session is None:
            import requests  # imported lazily so tests don't need the network

            self._session = requests.Session()
        return self._session

    def collect(self) -> list[Route]:
        base = self.cfg.url.rstrip("/")
        opts = {"timeout": 15, "verify": self.cfg.verify_ssl}
        login = self.session.post(
            f"{base}/api/tokens", json={"identity": self.cfg.identity, "secret": self.cfg.secret}, **opts
        )
        login.raise_for_status()
        token = login.json()["token"]
        resp = self.session.get(
            f"{base}/api/nginx/proxy-hosts", headers={"Authorization": f"Bearer {token}"}, **opts
        )
        resp.raise_for_status()
        return parse_proxy_hosts(resp.json(), self.cfg.name)
