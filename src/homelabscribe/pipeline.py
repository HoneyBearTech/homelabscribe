"""Scan → enrich → save → diff → render. The CLI and the Docker image both call these."""

from __future__ import annotations

from dataclasses import dataclass, field

from homelabscribe import manual, store
from homelabscribe.collectors.docker import DockerCollector
from homelabscribe.collectors.npm import NpmCollector
from homelabscribe.collectors.proxmox import ProxmoxCollector
from homelabscribe.config import Config
from homelabscribe.diff import Diff, compare
from homelabscribe.enrich import enrich, link_guests, link_routes, load_catalog
from homelabscribe.models import Host, Service, Snapshot
from homelabscribe.renderer import Renderer


@dataclass
class ScanResult:
    snapshot: Snapshot
    diff: Diff | None
    skipped: list[str] = field(default_factory=list)  # sources skipped (license or error)
    errors: list[str] = field(default_factory=list)


def _reason(e: Exception) -> str:
    """A short, readable cause for an unreadable source (the raw urllib3 text is several lines long)."""
    text = str(e)
    for needle, short in (
        ("Connection refused", "connection refused"),
        ("timed out", "timed out"),
        ("No route to host", "no route to host"),
        ("Name or service not known", "name not found"),
    ):
        if needle in text:
            return short
    return (text.splitlines() or [type(e).__name__])[0][:120]


def _carry_over(previous: Snapshot, collector, snap: Snapshot) -> tuple[list[Host], list[Service]]:
    """The last known hosts and services of a source that just failed, if we can tell which they were."""
    if isinstance(collector, DockerCollector):
        hosts = [h for h in previous.hosts if h.kind == "docker" and h.name == collector.cfg.name]
    elif isinstance(collector, ProxmoxCollector):
        hosts = [h for h in previous.hosts if h.kind == "proxmox-node"]
    else:
        return [], []
    present = {h.name for h in snap.hosts}
    hosts = [h for h in hosts if h.name not in present]
    names = {h.name for h in hosts}
    return hosts, [s for s in previous.services if s.host in names]


def scan(cfg: Config, features: frozenset[str], collectors=None) -> ScanResult:
    snap = Snapshot()
    skipped: list[str] = []
    errors: list[str] = []

    if collectors is None:
        collectors = []
        docker_hosts = cfg.docker_hosts
        if "multi_host" not in features and len(docker_hosts) > 1:
            skipped += [f"Docker host '{h.name}' (multiple hosts need Pro)" for h in docker_hosts[1:]]
            docker_hosts = docker_hosts[:1]
        collectors += [(f"Docker host '{h.name}'", DockerCollector(h)) for h in docker_hosts]
        if cfg.proxmox and "proxmox" not in features:
            skipped += [f"Proxmox '{p.name}' (Proxmox needs Pro)" for p in cfg.proxmox]
        elif cfg.proxmox:
            collectors += [(f"Proxmox '{p.name}'", ProxmoxCollector(p)) for p in cfg.proxmox]

    last = store.recent(cfg.state_dir, 1)
    previous = last[0] if last else None

    for label, collector in collectors:
        try:
            hosts, services = collector.collect()
        except Exception as e:  # one bad source shouldn't stop the others
            errors.append(f"{label}: {_reason(e)}")
            if previous:
                # Keep what we last knew, so one outage doesn't mark the whole host as removed.
                carried_hosts, carried_services = _carry_over(previous, collector, snap)
                snap.hosts += carried_hosts
                snap.services += carried_services
            continue
        snap.hosts += hosts
        snap.services += services

    if cfg.manual_services:
        manual_hosts, manual_services = manual.build(cfg.manual_services, {h.name for h in snap.hosts})
        snap.hosts += manual_hosts
        snap.services += manual_services

    enrich(snap.services, load_catalog(cfg.catalog_file))
    link_guests(snap.hosts, snap.services)

    for proxy in cfg.npm:
        try:
            snap.routes += NpmCollector(proxy).collect()
        except Exception as e:  # a proxy that is down shouldn't stop the scan
            errors.append(f"Nginx Proxy Manager '{proxy.name}': {type(e).__name__}")
            if previous:
                snap.routes += [r for r in previous.routes if r.proxy == proxy.name]
    link_routes(snap.routes, snap.hosts, snap.services)

    store.save(snap, cfg.state_dir)

    diff = None
    if previous:
        diff = compare(previous, snap)
        if not diff.empty:
            log = store.load_changelog(cfg.state_dir)
            log.append({"at": snap.taken_at.isoformat(), "changes": [c.model_dump() for c in diff.changes]})
            store.save_changelog(log, cfg.state_dir)
    return ScanResult(snapshot=snap, diff=diff, skipped=skipped, errors=errors)


def render(cfg: Config, features: frozenset[str], snapshot: Snapshot | None = None):
    if snapshot is None:
        latest = store.recent(cfg.state_dir, 1)
        if not latest:
            raise RuntimeError("No snapshot yet. Run `homelabscribe scan` first.")
        snapshot = latest[0]
    r = Renderer(cfg.output_dir, features)
    return r.render(snapshot, store.load_changelog(cfg.state_dir), cfg.breakglass)
