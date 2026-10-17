"""Scan → enrich → save → diff → render. The CLI and the Docker image both call these."""

from __future__ import annotations

from dataclasses import dataclass, field

from homelabscribe import store
from homelabscribe.collectors.docker import DockerCollector
from homelabscribe.collectors.proxmox import ProxmoxCollector
from homelabscribe.config import Config
from homelabscribe.diff import Diff, compare
from homelabscribe.enrich import enrich, load_catalog
from homelabscribe.models import Snapshot
from homelabscribe.renderer import Renderer


@dataclass
class ScanResult:
    snapshot: Snapshot
    diff: Diff | None
    skipped: list[str] = field(default_factory=list)  # sources skipped (license or error)
    errors: list[str] = field(default_factory=list)


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

    for label, collector in collectors:
        try:
            hosts, services = collector.collect()
        except Exception as e:  # one bad source shouldn't stop the others
            errors.append(f"{label}: {e}")
            continue
        snap.hosts += hosts
        snap.services += services

    enrich(snap.services, load_catalog(cfg.catalog_file))

    previous = store.recent(cfg.state_dir, 1)
    store.save(snap, cfg.state_dir)

    diff = None
    if previous:
        diff = compare(previous[0], snap)
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
