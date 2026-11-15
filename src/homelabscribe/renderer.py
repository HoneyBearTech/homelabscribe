"""Render a snapshot to Markdown. Writes only files whose content changed; keeps human notes."""

from __future__ import annotations

import contextlib
import itertools
import os
import re
import unicodedata
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


def tag_text(category: str) -> str:
    """A category as a tag: lower case, letters and digits of any language (with their combining marks)
    and `_`, `-` and `/` kept, every run of other characters one hyphen, hyphens and slashes at the
    start and end removed, and `item` when nothing is left.
    """
    kept = "".join(
        c if c.isalnum() or c in "_-/" or unicodedata.category(c)[0] == "M" else "\0"
        for c in str(category).lower()
    )
    tag = re.sub(r"\0+", "-", kept).strip("-/")
    return tag or "item"


# Words a YAML 1.1 reader (PyYAML) or a 1.2 reader turns into a boolean or null, in any letter case.
_YAML_WORDS = frozenset({"true", "false", "yes", "no", "on", "off", "y", "n", "null", "~"})
_PLAIN_ASCII = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-/:@+()")


def _plain_ok(text: str) -> bool:
    """True when the text is clearly a string to a YAML 1.1 and a 1.2 reader, as a plain scalar.

    A conservative allow-list: starts with a letter, holds only letters, digits and a few harmless
    punctuation marks (no commas, brackets, quotes, `#`, `&`, `*`, `!`, `|`, `>`, `%`, `?`, `=`),
    no `: ` and no trailing `:`, no leading or trailing space, and is not a boolean or null word.
    """
    if not text or not text[0].isalpha() or text[0] == " " or text[-1] in " :":
        return False
    if text.lower() in _YAML_WORDS or ": " in text:
        return False
    return all(c in _PLAIN_ASCII or (not c.isascii() and unicodedata.category(c)[0] in "LMN") for c in text)


_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t", "\0": "\\0"}
_NOT_RAW = frozenset("\u2028\u2029\ufeff\ufffe\uffff")


def _escape_char(c: str) -> str:
    if c in _ESCAPES:
        return _ESCAPES[c]
    n = ord(c)
    printable = 0x20 <= n <= 0x7E or 0xA0 <= n <= 0xD7FF or 0xE000 <= n or n >= 0x10000
    if printable and c not in _NOT_RAW:
        return c
    if n <= 0xFF:
        return f"\\x{n:02X}"
    return f"\\u{n:04X}" if n <= 0xFFFF else f"\\U{n:08X}"


_SURROGATES = re.compile("[\ud800-\udfff]")


def yaml_text(value: object) -> str:
    """A text as a one-line YAML scalar that every YAML reader reads back as exactly the same str.

    Ordinary text stays plain, so existing pages do not change. Anything else is written as a
    double-quoted scalar with escapes for line breaks, quotes, backslashes, control characters and the
    characters YAML does not allow raw or treats as line breaks. A lone surrogate cannot be part of a
    text file, so U+FFFD (the replacement character) is written in its place; every other text reads
    back as exactly the same str.
    """
    text = value if isinstance(value, str) else str(value)
    text = _SURROGATES.sub("\ufffd", text)
    if _plain_ok(text):
        return text
    return '"' + "".join(_escape_char(c) for c in text) + '"'


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


# Create the file itself, never what a link in its place points at (and as bytes, where that is a choice).
_NEW_FILE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)


def _fill(fd: int, data: bytes) -> None:
    """Write all of `data` to the new file open at `fd` and close it."""
    try:
        f = os.fdopen(fd, "wb")
    except BaseException:
        os.close(fd)
        raise
    with f:
        f.write(data)


def _archive(page: Path, folder: Path) -> Path:
    """Copy `page` into `folder` under a name nothing has, then remove it. Returns the archived copy.

    The first name tried is the page's own (`plex.md`), then `plex-2.md`, `plex-3.md` and so on. A name is
    taken when anything is there, even a link that points nowhere, and whatever is there is left as it is:
    each name is taken by creating a new file in one step, which fails if the name exists. The copy holds
    the page's bytes. The page is removed only once its copy is complete; if anything fails, the error is
    raised, the page stays where it was and the file this call created is removed. A page that is a link
    is copied as the text it points to, and then only the link is removed.
    """
    data = page.read_bytes()
    base = page.name.removesuffix(".md")
    for n in itertools.count(1):
        dest = folder / (page.name if n == 1 else f"{base}-{n}.md")
        try:
            fd = os.open(dest, _NEW_FILE, 0o666)
        except FileExistsError:
            continue
        break
    try:
        _fill(fd, data)
        page.unlink()
    except BaseException:
        with contextlib.suppress(OSError):  # the file this call created: never one that was there before
            dest.unlink()
        raise
    return dest


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
        self.env.filters["yaml_text"] = yaml_text
        self.env.filters["tag_text"] = tag_text
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

        by_id = {s.id: s for s in snap.services}

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
            "service_by_id": by_id.get,
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
                dest = self.out / "archive" / folder
                dest.mkdir(parents=True, exist_ok=True)
                _archive(f, dest)
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
            dns_servers = [s for s in services if s.is_dns or image_base(s.image) in DNS_IMAGES]
            self._write(
                "break-glass.md",
                self.env.get_template("breakglass.md.j2").render(
                    **ctx,
                    bg=bg,
                    critical=critical,
                    dns_servers=dns_servers,
                    count_on=lambda h: sum(1 for s in services if s.host == h.name),
                ),
            )

        if "diagrams" in self.features:
            ids: dict[str, str] = {}

            def node_id(key: str) -> str:
                return ids.setdefault(key, f"n{len(ids)}")

            edges = [(s, d) for s in services for d in ctx["deps"](s)]
            guest_edges = [(s, s.runs_host) for s in services if s.runs_host]
            self._write(
                "topology.md",
                self.env.get_template("topology.md.j2").render(
                    hosts=hosts,
                    edges=edges,
                    guest_edges=guest_edges,
                    node_id=node_id,
                    on_host=lambda h: [s for s in services if s.host == h.name],
                ),
            )

        self._sweep("services")
        self._sweep("hosts")
        return self.written
