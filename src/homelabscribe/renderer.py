"""Render a snapshot to Markdown. Writes only files whose content changed; keeps human notes."""

from __future__ import annotations

import contextlib
import hashlib
import itertools
import os
import re
import stat
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import TypeVar

import yaml
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader

from homelabscribe import __version__, fsutil, notes
from homelabscribe.config import BreakGlassConfig
from homelabscribe.enrich import image_base
from homelabscribe.models import Host, Service, Snapshot
from homelabscribe.skip import Skip
from homelabscribe.store import entry_ok as store_entry_ok

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

T = TypeVar("T")


SLUG_MAX = 200  # a page name stays well inside the 255 bytes most file systems allow for a file name


def slug(text: str) -> str:
    """A page name: lower-case letters, digits and single hyphens, at most `SLUG_MAX` characters.

    A longer name is cut to its first 191 characters (hyphens at the cut removed) and ends with a hyphen
    and the first 8 hex digits of the SHA-256 of the whole name, so two long names stay apart.
    """
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "item"
    if len(s) <= SLUG_MAX:
        return s
    return f"{s[: SLUG_MAX - 9].rstrip('-')}-{hashlib.sha256(s.encode()).hexdigest()[:8]}"


CODE_START = 8  # hex digits of the code a page gets when its name is already another page's


def coded_name(base: str, key: str, taken: set[str]) -> str:
    """`base` with a hyphen and a code from `key` (the first 8 hex digits of its SHA-256), at most `SLUG_MAX`
    characters, and not in `taken`.

    `base` is cut so the whole name fits (hyphens at the cut removed). When the name is taken, the code
    grows by two digits (10, 12, ...) until it is free; with all 64 digits taken, `-2`, `-3`, ... follow.
    """
    return next(name for name in _code_names(base, key) if name not in taken)


def _code_names(base: str, key: str, counted: bool = True) -> Iterator[str]:
    """The names `coded_name` tries, in order: the code with 8, 10, ... 64 hex digits, then (if `counted`)
    the full code with `-2`, `-3`, ... for ever."""
    digest = hashlib.sha256(replace_surrogates(key).encode()).hexdigest()
    for n in range(CODE_START, len(digest) + 1, 2):
        yield f"{base[: SLUG_MAX - 1 - n].rstrip('-')}-{digest[:n]}"
    if not counted:
        return
    for i in itertools.count(2):
        tail = f"{digest}-{i}"
        yield f"{base[: SLUG_MAX - 1 - len(tail)].rstrip('-')}-{tail}"


FRONT_MATTER_MAX = 64 * 1024  # bytes of a page read to find whose page it is


def front_matter(path: Path) -> dict | None:
    """The front matter of the page at `path` (the `---` block it starts with), or None. Never raises.

    Only the first `FRONT_MATTER_MAX` bytes are read, and the block has to close within them. None for a
    page that is missing, a link, not a plain file (a pipe or device is never opened) or unreadable, for a
    block that is not UTF-8 or holds a NUL, for a page without such a block, and for a block that is not
    a YAML mapping or that YAML cannot read (a date such as `2020-13-45`, a very long number, anything).
    """
    try:
        if path.is_symlink() or not stat.S_ISREG(os.stat(path).st_mode):
            return None
        with path.open("rb") as f:
            head = f.read(FRONT_MATTER_MAX)
    except (OSError, ValueError):
        return None
    if not head.startswith(b"---\n"):
        return None
    end = head.find(b"\n---\n", 3)
    if end < 0:
        return None
    try:
        text = head[4:end].decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\0" in text:
        return None
    try:
        data = yaml.safe_load(text)
    except Exception:  # whatever the reader makes of the text, the page then has no owner
        return None
    return data if isinstance(data, dict) else None


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


def replace_surrogates(text: str) -> str:
    """The text with U+FFFD (the replacement character) for every lone surrogate, which no file can hold."""
    return _SURROGATES.sub("\ufffd", text)


def yaml_text(value: object) -> str:
    """A text as a one-line YAML scalar that every YAML reader reads back as exactly the same str.

    Ordinary text stays plain, so existing pages do not change. Anything else is written as a
    double-quoted scalar with escapes for line breaks, quotes, backslashes, control characters and the
    characters YAML does not allow raw or treats as line breaks. A lone surrogate cannot be part of a
    text file, so U+FFFD (the replacement character) is written in its place; every other text reads
    back as exactly the same str.
    """
    text = value if isinstance(value, str) else str(value)
    text = replace_surrogates(text)
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
_NEW_FILE = fsutil.NEW_FILE


def _fill(fd: int, data: bytes) -> None:
    """Write all of `data` to the new file open at `fd`, flush it to disk where that is possible, close it."""
    fsutil.fill(fd, data)


def _version(st: os.stat_result) -> tuple[int, int, int, int]:
    """Which file, and which version of it: its device, inode, size and modification time."""
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def _still(path: Path, read: os.stat_result) -> bool:
    """True when `path` still is the file, in the version, that `read` describes."""
    try:
        return _version(os.stat(path)) == _version(read)
    except FileNotFoundError:
        return False


def _remove_own(path: Path, made: os.stat_result) -> None:
    """Remove `path` if it still holds the file that was created there (same device and inode), else nothing.

    The name is checked just before it is removed: a file that takes the name in the instant between that
    check and the removal is not protected.
    """
    try:
        now = os.lstat(path)
    except FileNotFoundError:
        return
    if (now.st_dev, now.st_ino) == (made.st_dev, made.st_ino):
        path.unlink()


def _archive(page: Path, folder: Path) -> Path | None:
    """Copy `page` into `folder` under a name nothing has, then remove it. Returns the archived copy.

    The first name tried is the page's own (`plex.md`), then `plex-2.md`, `plex-3.md` and so on. A name is
    taken when anything is there, even a link that points nowhere, and whatever is there is left as it is:
    each name is taken by creating a new file in one step, which fails if the name exists. The copy holds
    the page's bytes and is created with the page's permissions (the umask may narrow them). The page is
    removed only once its copy is complete and on disk, and only if it is still the file that was read,
    unchanged. If it was saved again in the meantime, the copy is removed and None is returned: the page
    stays for the next run. If anything fails, the error is raised, the page stays where it was and the
    copy is removed. Just before a copy is removed, its name is checked to still hold the file this call
    created; a file that takes the name in the instant between that check and the removal is not protected.
    A page that is a link is copied as the text it points to, and then only the link is removed.
    """
    with page.open("rb") as source:
        read = os.fstat(source.fileno())
        data = source.read()
    base = page.name.removesuffix(".md")
    for n in itertools.count(1):
        dest = folder / (page.name if n == 1 else f"{base}-{n}.md")
        try:
            fd = os.open(dest, _NEW_FILE, read.st_mode & 0o777)  # never wider than the page
        except FileExistsError:
            continue
        break
    try:
        made = os.fstat(fd)
    except BaseException:
        os.close(fd)
        raise
    try:
        _fill(fd, data)
        if not _still(page, read):  # saved again while it was copied: the copy would hold older text
            _remove_own(dest, made)
            return None
        page.unlink()
    except BaseException:
        with contextlib.suppress(OSError):  # the file this call created: never one that was there before
            _remove_own(dest, made)
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
        self.skipped: list[Skip] = []  # `<page> (<reason>)` for each page left alone
        self.notices: list[str] = []  # one line for each page whose name was already another page's

    # ---- helpers exposed to templates -------------------------------------------------

    def _context(self, snap: Snapshot) -> dict:
        services = snap.visible_services()
        hosts = sorted(snap.hosts, key=lambda h: h.name)
        titles = defaultdict(int)
        for s in services:
            titles[s.title.lower()] += 1
        link_hosts = {h.name: h.link_host or h.name for h in hosts}
        by_name = {(s.host, s.compose_project, s.compose_service): s for s in services if s.compose_service}

        def base(s: Service) -> str:
            # Add the host only when two services share a title.
            return slug(s.title) if titles[s.title.lower()] == 1 else slug(f"{s.title}-{s.host}")

        shared_ids = Counter((base(s), s.id) for s in services)

        def page_key(s: Service) -> str:
            # The id, and when another look-alike has the same id (a VM and a container named alike, two
            # guests of one name), what tells them apart: the kind, and the VM or LXC number.
            if shared_ids[(base(s), s.id)] == 1:
                return s.id
            return f"{s.id}/{s.kind}" + ("" if s.vmid is None else f"/{s.vmid}")

        def same_title_and_host(fm: dict, s: Service) -> bool:
            title, host = replace_surrogates(s.title), replace_surrogates(s.host)
            return fm.get("title") == title and fm.get("host") == host

        def service_owner(fm: dict, group: list[Service]) -> Service | None:
            # The page's `id` first; else its title and host, if they are one member's only.
            by_id = [s for s in group if fm.get("id") == replace_surrogates(page_key(s))]
            if len(by_id) == 1:
                return by_id[0]
            alike = [s for s in by_id or group if same_title_and_host(fm, s)]
            if len(alike) == 1:
                return alike[0]
            return by_id[0] if by_id else None  # an id two services share: the first in the fixed order

        def service_keys(s: Service, group: list[Service]) -> list[str]:
            # What the code is made from: host and title, and the id too when another member has both.
            short, long = f"{s.host}/{s.title}", f"{s.host}/{s.title}/{page_key(s)}"
            shared = sum(o.host == s.host and o.title == s.title for o in group) > 1
            return [long, short] if shared else [short, long]

        def service_claims(fm: dict, s: Service) -> bool:
            return fm.get("id") == replace_surrogates(page_key(s)) and same_title_and_host(fm, s)

        def fixed_order(s: Service) -> tuple:
            # A service whose own title is unique first (its name is that title), then title, host, names,
            # kind and VM or LXC number; last everything else known about it, so that only services that
            # are the same in every way could come in the order of the scan (and their pages are the same).
            vmid = -1 if s.vmid is None else s.vmid
            head = (titles[s.title.lower()] != 1, s.title, s.host, s.name, s.compose_service or "", s.kind)
            return (*head, vmid, s.model_dump_json())

        service_names, service_extra, grouped = self._names(
            "services", services, base, fixed_order, service_owner, service_keys, service_claims
        )
        host_list = list({h.name: h for h in hosts}.values())

        def host_is(fm: dict, h: Host) -> bool:
            return fm.get("title") == replace_surrogates(h.name)

        host_names, host_extra, _ = self._names(
            "hosts",
            host_list,
            lambda h: slug(h.name),
            lambda h: h.name,
            lambda fm, group: next((h for h in group if host_is(fm, h)), None),
            lambda h, group: [h.name],
            host_is,
        )
        # By the service itself: two services can share an id (a VM and a container named like its host).
        names = {id(s): n for s, n in zip(services, service_names, strict=True)}
        names_by_id: dict[str, str] = {}
        for s, n in zip(services, service_names, strict=True):
            names_by_id.setdefault(s.id, n)
        host_slugs = {h.name: n for h, n in zip(host_list, host_names, strict=True)}
        self.notices = [
            f'"{s.title}" on {s.host} has its own page services/{n}.md (its name matched "{o.title}")'
            for s, n, o in service_extra
        ] + [
            f'host "{h.name}" has its own page hosts/{n}.md (its name matched "{o.name}")'
            for h, n, o in host_extra
        ]

        def page(s: Service) -> str:
            return names.get(id(s)) or names_by_id.get(s.id) or base(s)

        def page_id(s: Service) -> str | None:
            # Only on the pages of services whose page names clashed: the others stay as they were.
            return page_key(s) if id(s) in grouped else None

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
            "page_id": page_id,
            "address": address,
            "deps": deps,
            "users": users,
            "service_by_id": by_id.get,
            "host_slug": lambda h: host_slugs.get(h.name, slug(h.name)),
            "host_slug_by_name": lambda name: host_slugs.get(name, slug(name)),
        }

    def _names(
        self,
        folder: str,
        items: list[T],
        base: Callable[[T], str],
        order: Callable[[T], tuple | str],
        owner: Callable[[dict, list[T]], T | None],
        keys: Callable[[T, list[T]], list[str]],
        claims: Callable[[dict, T], bool],
    ) -> tuple[list[str], list[tuple[T, str, T]], set[int]]:
        """The page name of each item (in the order of `items`), `(item, name, owner)` for each item whose
        base name was another's, and the `id()` of every item whose base name was shared.

        Items with the same base name share it only in name: one keeps it, each other one gets the base name
        with a code (`coded_name`). The one that keeps it is the one `owner` finds for the front matter of
        the page already in `folder` under that name; without one, the first in `order`. Each other item
        keeps a coded page already there that `claims` says was written for it (any of its `keys`, any code
        length); otherwise it gets a new one from its first key. The result does not depend on the order of
        `items`, and no two items get the same name.
        """
        groups: dict[str, list[T]] = defaultdict(list)
        for it in items:
            groups[base(it)].append(it)
        taken = set(groups)
        named: dict[int, str] = {}
        owners: dict[str, T] = {}
        grouped: set[int] = set()
        new: list[tuple[str, T, list[T]]] = []
        for name in sorted(groups):  # every owner, and every coded page kept, before any new name
            group = groups[name]
            if len(group) == 1:
                named[id(group[0])] = name
                continue
            group = sorted(group, key=order)
            grouped.update(id(it) for it in group)
            fm = front_matter(self.out / folder / f"{name}.md")
            owners[name] = (owner(fm, group) if fm is not None else None) or group[0]
            named[id(owners[name])] = name
            for it in group:
                if it is owners[name]:
                    continue
                kept = self._kept_page(folder, name, keys(it, group), taken, lambda fm, it=it: claims(fm, it))
                if kept is None:
                    new.append((name, it, group))
                else:
                    taken.add(kept)
                    named[id(it)] = kept
        for name, it, group in new:
            coded = coded_name(name, keys(it, group)[0], taken)
            taken.add(coded)
            named[id(it)] = coded
        extra = [
            (it, named[id(it)], owners[name])
            for name in sorted(owners)
            for it in sorted(groups[name], key=order)
            if it is not owners[name]
        ]
        return [named[id(it)] for it in items], extra, grouped

    def _kept_page(
        self, folder: str, base: str, keys: list[str], taken: set[str], claims: Callable[[dict], bool]
    ) -> str | None:
        """The name of a coded page already in `folder` that `claims` says is this item's, or None."""
        for key in keys:
            for name in _code_names(base, key, counted=False):
                if name in taken or not os.path.lexists(self.out / folder / f"{name}.md"):
                    continue
                fm = front_matter(self.out / folder / f"{name}.md")
                if fm is not None and claims(fm):
                    return name
        return None

    # ---- writing ----------------------------------------------------------------------

    def _skip(self, path: Path, reason: str) -> None:
        self.skipped.append(Skip(path.relative_to(self.out).as_posix(), reason))

    def _write(self, rel: str, content: str) -> None:
        path = self.out / rel
        self.generated.add(path)
        reason = self._write_problem(path)
        if reason:
            self._skip(path, reason)
            return
        try:
            previous = self._read(path) if os.path.lexists(path) else None
        except UnicodeDecodeError:
            self._skip(path, "not UTF-8, left as it is")
            return
        except OSError:
            self._skip(path, "could not be read, left as it is")
            return
        merged = notes.merge(replace_surrogates(content), previous)
        if merged == previous:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fsutil.atomic_write(path, merged.encode("utf-8"))
        except OSError:
            self._skip(path, "could not be written, left as it is")
            return
        self.written.append(path)

    def _write_problem(self, path: Path) -> str | None:
        """Why nothing may be written to `path`: the page itself is a link, a folder or not a plain file.

        A folder that is a link to a folder is the user's own setup and is followed.
        """
        if path.is_symlink():
            return "the page is a symbolic link, left alone"
        if os.path.lexists(path) and not path.is_file():
            return "not a regular file, left alone"
        return None

    @staticmethod
    def _read(path: Path) -> str:
        return fsutil.read_text(path)

    def _sweep(self, folder: str) -> None:
        """Pages for services that no longer exist: archive if they hold notes, else delete.

        A page that cannot be read or is not UTF-8 is never deleted or archived: it is left as it is.
        """
        d = self.out / folder
        if not d.is_dir():
            return
        for f in sorted(d.glob("*.md")):
            if f in self.generated:
                continue
            try:
                kept = notes.extract(self._read(f))
            except UnicodeDecodeError:
                self._skip(f, "not UTF-8, left as it is")
                continue
            except OSError:
                self._skip(f, "could not be read, left as it is")
                continue
            if kept and kept.strip() != notes.PLACEHOLDER:
                dest = self.out / "archive" / folder
                try:
                    dest.mkdir(parents=True, exist_ok=True)
                    archived = _archive(f, dest)
                except OSError:  # no room, no permission, no folder: the page and its notes stay
                    self._skip(f, "could not be archived, left as it is")
                    continue
                if archived is None:  # saved while it was copied: left as it is, for the next run
                    continue
            else:
                try:
                    f.unlink()
                except OSError:
                    self._skip(f, "could not be removed, left as it is")
                    continue
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
            usable = [e for e in changelog or [] if store_entry_ok(e)]
            if len(usable) < len(changelog or []):
                bad = len(changelog or []) - len(usable)
                self.skipped.append(Skip("changelog.json", f"{bad} entry(ies) not usable, left in the file"))
            entries = [
                {"when": _when(datetime.fromisoformat(e["at"])), "changes": e["changes"]}
                for e in reversed(usable)
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
