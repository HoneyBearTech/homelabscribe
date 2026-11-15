"""Scan → enrich → save → diff → render. The CLI and the Docker image both call these."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from homelabscribe import htmlsite, manual, notes, store
from homelabscribe.collectors.docker import DockerCollector
from homelabscribe.collectors.npm import NpmCollector
from homelabscribe.collectors.proxmox import ProxmoxCollector
from homelabscribe.config import Config
from homelabscribe.diff import Diff, compare
from homelabscribe.enrich import enrich, link_guests, link_routes, load_catalog
from homelabscribe.models import Host, Service, Snapshot
from homelabscribe.renderer import Renderer, replace_surrogates


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
    """The last known hosts and services of a source that just failed, if we can tell which they were.

    `previous` is cleaned, so names from the config and from the new scan are compared in their cleaned form.
    """
    if isinstance(collector, DockerCollector):
        name = without_surrogates_or_markers(collector.cfg.name)
        hosts = [h for h in previous.hosts if h.kind == "docker" and h.name == name]
    elif isinstance(collector, ProxmoxCollector):
        hosts = [h for h in previous.hosts if h.kind == "proxmox-node"]
    else:
        return [], []
    present = {without_surrogates_or_markers(h.name) for h in snap.hosts}
    hosts = [h for h in hosts if h.name not in present]
    names = {h.name for h in hosts}
    return hosts, [s for s in previous.services if s.host in names]


_HARMLESS_TOKEN = "homelabscribe-notes:"  # what the notes-marker token in lab text becomes


def without_surrogates_or_markers(text: str) -> str:
    """The text with U+FFFD for every lone surrogate and without the token every notes marker needs.

    Each `homelabscribe:notes:` becomes `homelabscribe-notes:`, so text from the lab cannot be taken for
    the notes block of a page. One pass is enough: the replacement cannot form a new token.
    """
    return replace_surrogates(text).replace(notes.MARKER_TOKEN, _HARMLESS_TOKEN)


def _value_without_surrogates_or_markers(value):
    """`value` with every str in it, dict keys included, passed through `without_surrogates_or_markers`.

    Lists, tuples, sets, frozensets and dicts are walked and given back as the same kind.
    """
    if isinstance(value, str):
        return without_surrogates_or_markers(value)
    if isinstance(value, list | tuple | set | frozenset):
        kind = next(k for k in (list, tuple, set, frozenset) if isinstance(value, k))
        return kind(_value_without_surrogates_or_markers(v) for v in value)
    if isinstance(value, dict):
        return {
            _value_without_surrogates_or_markers(k): _value_without_surrogates_or_markers(v)
            for k, v in value.items()
        }
    return value


def snapshot_without_surrogates_or_markers(snap: Snapshot) -> Snapshot:
    """A copy of the snapshot that can be saved and rendered: text read from a source may hold a lone
    surrogate, or a notes marker.

    The snapshot is turned into plain lists, dicts and values, cleaned, and read back into the models, so
    every field of every model is covered, fields added later included.
    Not covered: a snapshot passed to a Renderer or `render_pages`; entries in changelog.json are kept as is.
    """
    return Snapshot.model_validate(_value_without_surrogates_or_markers(snap.model_dump()))


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
    # Cleaned as the new one is: older text cannot bring a marker in, and the cleaning is not a change.
    previous = snapshot_without_surrogates_or_markers(last[0]) if last else None

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
        known = {h.name for h in snap.hosts}
        cleaned = {without_surrogates_or_markers(n) for n in known}  # a host kept from `previous` is cleaned
        known |= {e.host for e in cfg.manual_services if without_surrogates_or_markers(e.host) in cleaned}
        manual_hosts, manual_services = manual.build(cfg.manual_services, known)
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
                name = without_surrogates_or_markers(proxy.name)  # `previous` is cleaned
                snap.routes += [r for r in previous.routes if r.proxy == name]
    link_routes(snap.routes, snap.hosts, snap.services)
    snap = snapshot_without_surrogates_or_markers(snap)

    store.save(snap, cfg.state_dir)

    diff = None
    if previous:
        diff = compare(previous, snap)
        if not diff.empty:
            log = store.load_changelog(cfg.state_dir)
            log.append({"at": snap.taken_at.isoformat(), "changes": [c.model_dump() for c in diff.changes]})
            store.save_changelog(log, cfg.state_dir)
    return ScanResult(snapshot=snap, diff=diff, skipped=skipped, errors=errors)


@dataclass
class Rendered:
    written: list[Path]  # Markdown files that changed
    html: htmlsite.Export | None = None  # the static HTML copy, when `html_dir` is set


def html_dir_problem(cfg: Config) -> str | None:
    """Why `html_dir` cannot be used (it has to be a folder of its own), or None."""
    if cfg.html_dir is None:
        return None
    others = {"output_dir": cfg.output_dir, "state_dir": cfg.state_dir}
    return htmlsite.folder_problem(cfg.html_dir, others)


def render_pages(cfg: Config, features: frozenset[str], snapshot: Snapshot | None = None) -> Rendered:
    """Write the Markdown pages and then, when `html_dir` is set, their HTML copy.

    The HTML copy is part of the free core: it does not depend on `features`.
    """
    problem = html_dir_problem(cfg)
    if problem:  # before anything is written
        raise ValueError(problem)
    if snapshot is None:
        latest = store.recent(cfg.state_dir, 1)
        if not latest:
            raise RuntimeError("No snapshot yet. Run `homelabscribe scan` first.")
        snapshot = snapshot_without_surrogates_or_markers(latest[0])  # it may have been saved uncleaned
    r = Renderer(cfg.output_dir, features)
    # The names of the critical services are compared with the cleaned names in the snapshot.
    critical = [without_surrogates_or_markers(n) for n in cfg.breakglass.critical]
    breakglass = cfg.breakglass.model_copy(update={"critical": critical})
    written = r.render(snapshot, store.load_changelog(cfg.state_dir), breakglass)
    html = htmlsite.export(cfg.output_dir, cfg.html_dir, r.generated) if cfg.html_dir is not None else None
    return Rendered(written=written, html=html)


def render(cfg: Config, features: frozenset[str], snapshot: Snapshot | None = None):
    """The Markdown files that changed. Also writes the HTML copy when `html_dir` is set."""
    return render_pages(cfg, features, snapshot).written
