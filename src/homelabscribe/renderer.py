"""Render a snapshot to Markdown. Writes only files whose content changed; keeps human notes."""

from __future__ import annotations

import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader

from homelabscribe import __version__, notes
from homelabscribe.config import BreakGlassConfig
from homelabscribe.enrich import image_base
from homelabscribe.models import Host, Service, Snapshot

WEB_PORTS = {
    80,
    443,
    3000,
    5000,
    5055,
    7878,
    8000,
    8080,
    8081,
    8096,
    8123,
    8181,
    8443,
    8686,
    8787,
    8989,
    9000,
    9090,
    9696,
    32400,
}
DNS_IMAGES = frozenset({"pihole", "pi-hole", "adguardhome"})  # exact image names, so exporters do not match


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "item"


# Variables every image sets for itself; listing them buries the ones the user actually configured.
NOISE_ENV = frozenset(
    {"PATH", "HOME", "TERM", "PS1", "LANG", "LANGUAGE", "TMPDIR", "HOSTNAME", "PWD", "SHLVL"}
    | {"VIRTUAL_ENV", "LSIO_FIRST_PARTY"}
)
NOISE_ENV_PREFIXES = ("S6_", "XDG_", "LC_")


def config_vars(names: list[str]) -> list[str]:
    """Environment variable names worth showing: drops image defaults such as PATH and S6_*."""
    return [n for n in names if n not in NOISE_ENV and not n.startswith(NOISE_ENV_PREFIXES)]


def code_list(items: list[str]) -> str:
    return ", ".join(f"`{i}`" for i in items)


def _when(dt: datetime) -> str:
    local = dt.astimezone()
    return f"{local:%B} {local.day}, {local:%Y at %H:%M %Z}".strip()


class Renderer:
    def __init__(
        self, output_dir: Path, features: frozenset[str] = frozenset(), template_dir: Path | None = None
    ):
        self.out = output_dir
        self.features = features
        loaders = [
            PackageLoader("homelabscribe", "templates"),
            PackageLoader("homelabscribe.pro", "templates"),
        ]
        if template_dir:
            loaders.insert(0, FileSystemLoader(str(template_dir)))
        self.env = Environment(
            loader=ChoiceLoader(loaders),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            autoescape=False,
        )
        self.env.filters["code_list"] = code_list
        self.env.filters["config_vars"] = config_vars
        self.written: list[Path] = []
        self.generated: set[Path] = set()

    # ---- helpers exposed to templates -------------------------------------------------

    def _context(self, snap: Snapshot) -> dict:
        services = snap.visible_services()
        hosts = sorted(snap.hosts, key=lambda h: h.name)
        titles = defaultdict(int)
        for s in services:
            titles[s.title.lower()] += 1
        host_slugs = {h.name: slug(h.name) for h in hosts}
        link_hosts = {h.name: h.link_host or h.name for h in hosts}
        by_name = {(s.host, s.compose_project, s.compose_service): s for s in services if s.compose_service}

        def page(s: Service) -> str:
            # Add the host only when two services share a title.
            return slug(s.title) if titles[s.title.lower()] == 1 else slug(f"{s.title}-{s.host}")

        def address(s: Service) -> str | None:
            if s.url:
                return s.url
            for p in s.ports:
                if p.host and p.container in WEB_PORTS:
                    scheme = "https" if p.container in (443, 8443) else "http"
                    return f"{scheme}://{link_hosts.get(s.host, s.host)}:{p.host}"
            return None

        def deps(s: Service) -> list[Service]:
            return [
                d
                for name in s.depends_on
                if (d := by_name.get((s.host, s.compose_project, name))) is not None
            ]

        def users(s: Service) -> list[Service]:
            return [u for u in services if s in deps(u)]

        return {
            "version": __version__,
            "generated": _when(snap.taken_at),
            "generated_iso": snap.taken_at.isoformat(timespec="seconds"),
            "hosts": hosts,
            "services": services,
            "page": page,
            "address": address,
            "deps": deps,
            "users": users,
            "host_slug": lambda h: host_slugs.get(h.name, slug(h.name)),
            "host_slug_by_name": lambda name: host_slugs.get(name, slug(name)),
        }

    # ---- writing ----------------------------------------------------------------------

    def _write(self, rel: str, content: str) -> None:
        path = self.out / rel
        self.generated.add(path)
        previous = path.read_text() if path.exists() else None
        merged = notes.merge(content, previous)
        if merged == previous:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(merged)
        self.written.append(path)

    def _sweep(self, folder: str) -> None:
        """Pages for services that no longer exist: archive if they hold notes, else delete."""
        d = self.out / folder
        if not d.exists():
            return
        for f in d.glob("*.md"):
            if f in self.generated:
                continue
            kept = notes.extract(f.read_text())
            if kept and kept.strip() != notes.PLACEHOLDER:
                dest = self.out / "archive" / folder / f.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(f, dest)
            else:
                f.unlink()
            self.written.append(f)

    def render(
        self,
        snap: Snapshot,
        changelog: list[dict] | None = None,
        breakglass: BreakGlassConfig | None = None,
    ) -> list[Path]:
        ctx = self._context(snap)
        services: list[Service] = ctx["services"]
        hosts: list[Host] = ctx["hosts"]

        pages = []
        if snap.routes:
            pages.append(("Reverse proxy routes", "routes.md"))
        if "changelog" in self.features:
            pages.append(("What changed", "CHANGELOG.md"))
        if "breakglass" in self.features:
            pages.append(("Break-glass guide for family", "break-glass.md"))
        if "diagrams" in self.features:
            pages.append(("Topology diagram", "topology.md"))

        by_cat: dict[str, list[Service]] = defaultdict(list)
        for s in services:
            by_cat[s.category].append(s)
        self._write(
            "index.md",
            self.env.get_template("index.md.j2").render(
                **ctx,
                by_category=sorted(by_cat.items()),
                pages=pages,
                running=sum(1 for s in services if s.status == "running"),
                by_hand=sum(1 for s in services if s.kind == "manual"),
            ),
        )
        for s in services:
            self._write(
                f"services/{ctx['page'](s)}.md", self.env.get_template("service.md.j2").render(**ctx, s=s)
            )
        for h in hosts:
            on_host = [s for s in services if s.host == h.name]
            self._write(
                f"hosts/{ctx['host_slug'](h)}.md",
                self.env.get_template("host.md.j2").render(**ctx, h=h, on_host=on_host),
            )

        if snap.routes:
            by_id = {s.id: s for s in services}
            self._write(
                "routes.md",
                self.env.get_template("routes.md.j2").render(
                    **ctx, routes=snap.routes, service_of=lambda route: by_id.get(route.service or "")
                ),
            )

        if "changelog" in self.features:
            entries = [
                {"when": _when(datetime.fromisoformat(e["at"])), "changes": e["changes"]}
                for e in reversed(changelog or [])
            ]
            self._write("CHANGELOG.md", self.env.get_template("changelog.md.j2").render(entries=entries))

        if "breakglass" in self.features:
            bg = breakglass or BreakGlassConfig()
            wanted = {n.lower() for n in bg.critical}
            critical = [s for s in services if s.name.lower() in wanted or s.title.lower() in wanted]
            if not critical:
                critical = [s for s in services if s.category not in ("Databases", "Infrastructure", "Other")]
            dns = next((s for s in services if s.is_dns), None) or next(
                (s for s in services if image_base(s.image) in DNS_IMAGES), None
            )
            self._write(
                "break-glass.md",
                self.env.get_template("breakglass.md.j2").render(
                    **ctx,
                    bg=bg,
                    critical=critical,
                    dns=dns,
                    count_on=lambda h: sum(1 for s in services if s.host == h.name),
                ),
            )

        if "diagrams" in self.features:
            ids: dict[str, str] = {}

            def node_id(key: str) -> str:
                return ids.setdefault(key, f"n{len(ids)}")

            edges = [(s, d) for s in services for d in ctx["deps"](s)]
            self._write(
                "topology.md",
                self.env.get_template("topology.md.j2").render(
                    hosts=hosts,
                    edges=edges,
                    node_id=node_id,
                    on_host=lambda h: [s for s in services if s.host == h.name],
                ),
            )

        self._sweep("services")
        self._sweep("hosts")
        return self.written
