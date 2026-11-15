"""`homelabscribe doctor --bundle`: a redacted archive to attach to a bug report.

The archive is meant for a public issue tracker, so it masks more rather than less. Nothing is copied as
text from the config, a snapshot or a server: names become numbered placeholders, other values become
`<redacted>`, and what a server or a snapshot said is kept only as a count, a version number or one of a
fixed list of words. A config field is masked until a rule below says it is safe to keep.
"""

from __future__ import annotations

import io
import json
import platform
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata, resources
from pathlib import Path
from typing import Literal, get_args, get_origin

import yaml
from pydantic import BaseModel

from homelabscribe import __version__, licensing
from homelabscribe.config import (
    BreakGlassConfig,
    Config,
    DockerHostConfig,
    ManualServiceConfig,
    NotifyConfig,
    NpmConfig,
    ProxmoxConfig,
)
from homelabscribe.models import Host, Service, Snapshot

REDACTED = "<redacted>"
UNKNOWN = "unknown"
MEMBERS = ("README.txt", "doctor.txt", "environment.json", "config.redacted.yaml", "snapshot-summary.json")

# ---- config.redacted.yaml ---------------------------------------------------------------------

# What may be kept of a config field. A field without a rule is masked, whatever its type.
_NESTED = "nested"  # a config model, or a list of them: look inside
_NAME = "name"  # a numbered placeholder by kind
_URL = "url"  # the scheme, when it is a known one
_BOOL = "bool"
_PORT = "port"
_WORDS = "words"  # the fixed words the config model checks (a Literal)
_SET = "set"  # only whether there is a value

_RULES: dict[type[BaseModel], dict[str, str]] = {
    Config: {
        "docker_hosts": _NESTED,
        "proxmox": _NESTED,
        "npm": _NESTED,
        "manual_services": _NESTED,
        "notify": _NESTED,
        "breakglass": _NESTED,
        "license_key": _SET,
    },
    DockerHostConfig: {"name": _NAME, "url": _URL},
    ProxmoxConfig: {"name": _NAME, "port": _PORT, "verify_ssl": _BOOL},
    NpmConfig: {"name": _NAME, "url": _URL, "verify_ssl": _BOOL},
    NotifyConfig: {"type": _WORDS, "url": _URL, "on": _WORDS},
    ManualServiceConfig: {"name": _NAME, "url": _URL, "dns": _BOOL},
    BreakGlassConfig: {},
}
CONFIG_MODELS: tuple[type[BaseModel], ...] = tuple(_RULES)  # the config models the rules above cover
_PLACEHOLDERS: dict[type[BaseModel], str] = {
    DockerHostConfig: "docker-host",
    ProxmoxConfig: "proxmox",
    NpmConfig: "npm",
    ManualServiceConfig: "manual",
}
_URL_SCHEMES = frozenset({"unix", "tcp", "http", "https", "ssh"})
_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*)://")
_MAX_DEPTH = 8


def placeholder(kind: type[BaseModel], position: int) -> str:
    """The name a configured source goes by in the archive: its kind and its place in the config."""
    return f"{_lookup(_PLACEHOLDERS, kind)}-{position + 1}"


def _lookup(table: dict, model: type) -> object:
    """A table entry for a model or the config model it extends."""
    for klass in getattr(model, "__mro__", ()):
        if klass in table:
            return table[klass]
    return None


def mask(value: object, depth: int = 0) -> object:
    """Hide a value. A list keeps its length and what is not set stays not set; nothing else is kept."""
    if value is None:
        return None
    if isinstance(value, list | tuple) and depth < _MAX_DEPTH:
        return [mask(item, depth + 1) for item in value]
    return REDACTED


def _model_in(annotation: object) -> tuple[type[BaseModel] | None, bool]:
    """(the config model a field holds, whether it holds a list of them)."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation, False
    if get_origin(annotation) is list:
        inner = (get_args(annotation) or (None,))[0]
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return inner, True
    return None, False


def _words(annotation: object) -> frozenset[str]:
    """The fixed words a field accepts: the values of the Literal in its annotation."""
    if get_origin(annotation) is Literal:
        return frozenset(a for a in get_args(annotation) if isinstance(a, str))
    found: frozenset[str] = frozenset()
    for arg in get_args(annotation):
        found |= _words(arg)
    return found


def _url(value: object) -> str:
    m = _SCHEME.match(value) if isinstance(value, str) else None
    if m and m.group(1).lower() in _URL_SCHEMES:
        return f"{m.group(1).lower()}://{REDACTED}"
    return REDACTED


def _keep(rule: str | None, value: object, annotation: object, model: type, position: int | None) -> object:
    if rule == _SET:
        return "<set>" if value else "<not set>"
    if value is None:
        return None
    if rule == _BOOL and isinstance(value, bool):
        return value
    if rule == _PORT and isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 65535:
        return value
    if rule == _URL:
        return _url(value)
    if rule == _WORDS:
        allowed = _words(annotation)
        if isinstance(value, str) and value in allowed:
            return value
        if isinstance(value, list):
            return [v if isinstance(v, str) and v in allowed else REDACTED for v in value]
    if rule == _NAME and position is not None and _lookup(_PLACEHOLDERS, model):
        return placeholder(model, position)
    if rule == _NESTED:
        inner, many = _model_in(annotation)
        if inner is not None and many and isinstance(value, list):
            return [redact(item, inner, i) for i, item in enumerate(value)]
        if inner is not None and not many:
            return redact(value, inner)
    return mask(value)


def redact(data: object, model: type[BaseModel] = Config, position: int | None = None) -> object:
    """Config data with every value masked unless a rule says it is safe to keep.

    `data` is the validated config as plain data (`model_dump()`). Only keys the model defines are looked
    at, so a key it does not know never reaches the result.
    """
    if not isinstance(data, dict):
        return mask(data)
    rules = _lookup(_RULES, model) or {}
    out: dict[str, object] = {}
    for name, field in model.model_fields.items():
        if name in data:
            out[name] = _keep(rules.get(name), data[name], field.annotation, model, position)
    return out


def unknown_keys(raw: object, model: type[BaseModel] = Config) -> int:
    """How many keys of a config file the config model does not know (they are ignored when it loads)."""
    if not isinstance(raw, dict):
        return 0
    count = 0
    for key, value in raw.items():
        field = model.model_fields.get(key) if isinstance(key, str) else None
        if field is None:
            count += 1
            continue
        inner, many = _model_in(field.annotation)
        if inner is not None and many and isinstance(value, list):
            count += sum(unknown_keys(item, inner) for item in value)
        elif inner is not None and not many:
            count += unknown_keys(value, inner)
    return count


def config_text(cfg: Config, config_path: Path | None) -> str:
    """config.redacted.yaml: the structure of the validated config, with its values masked."""
    lines = ["# The configuration as HomelabScribe loaded it. Values are masked; the structure is kept."]
    if config_path is not None and config_path.exists():
        try:
            unknown: object = unknown_keys(yaml.safe_load(config_path.read_text()))
        except Exception:
            unknown = "not counted"
        lines.append(f"# Keys in the config file that HomelabScribe does not know (left out here): {unknown}")
    else:
        lines.append("# No config file was found, so these are the defaults.")
    body = yaml.safe_dump(redact(cfg.model_dump(), type(cfg)), sort_keys=False, default_flow_style=False)
    return "\n".join(lines) + "\n" + body


# ---- doctor.txt -------------------------------------------------------------------------------

_SOURCES: dict[str, tuple[type[BaseModel], str]] = {
    "docker": (DockerHostConfig, "Docker host"),
    "npm": (NpmConfig, "Nginx Proxy Manager"),
    "proxmox": (ProxmoxConfig, "Proxmox"),
}
_VERSION = re.compile(r"\d{1,4}(?:\.\d{1,4}){0,2}(?!\.?\d)")
_MAX_COUNT = 999_999
_TYPE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}")
# A public version (PEP 440 shape): an optional epoch, a dotted number, then short pre, post or dev markers.
_INSTALLED = re.compile(
    r"(?:\d{1,4}!)?\d{1,6}(?:\.\d{1,6}){0,5}(?:[._-]?(?:a|b|c|rc|alpha|beta|pre|preview|post|rev|r|dev)[._-]?\d{0,6}){0,3}"
)
_PLAIN = re.compile(r"[A-Za-z0-9_.-]{1,40}")

LICENSE_LINES = (
    "Free",
    "Free, key not valid",
    "Free, Pro key expired",
    "Pro, lifetime",
    "Pro, with an end date",
)


@dataclass(frozen=True)
class Check:
    """What one of doctor's connection tests found. `version` and `count` are as the server sent them."""

    kind: str  # "docker", "npm" or "proxmox"
    position: int  # the source's place in its list in the config, from 0
    outcome: str  # "ok", "failed" or "skipped"
    version: object = None
    count: object = None
    error: str | None = None  # the type name of the exception, for a failure


def version_number(value: object) -> str:
    """A version a server sent, as its leading number of up to three dotted parts (`27.3.1`).

    Anything else, such as an address or a longer dotted number, is `unknown`.
    """
    m = _VERSION.match(value) if isinstance(value, str) else None
    return m.group(0) if m else UNKNOWN


def whole_number(value: object) -> int | str:
    """A count a server sent, as a whole number from 0 to 999999; anything else is `unknown`."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value if 0 <= value <= _MAX_COUNT else UNKNOWN
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 12:
        return int(value) if int(value) <= _MAX_COUNT else UNKNOWN
    return UNKNOWN


def type_name(value: object) -> str:
    """An exception's type name, when it looks like one; never its text."""
    return value if isinstance(value, str) and _TYPE_NAME.fullmatch(value) else "Exception"


def license_line(key: str | None) -> str:
    """One of five fixed texts. The email a key is licensed to and its end date are left out."""
    if not key:
        return LICENSE_LINES[0]
    try:
        lic = licensing.verify(key)
    except Exception:
        return LICENSE_LINES[1]
    if not lic.active:
        return LICENSE_LINES[2]
    return LICENSE_LINES[3] if lic.expires is None else LICENSE_LINES[4]


def _check_line(check: Check) -> str:
    model, label = _SOURCES[check.kind]
    head = f"{placeholder(model, check.position)} ({label})"
    if check.outcome == "skipped":
        return f"{head}: SKIPPED (needs Pro)"
    if check.outcome != "ok":
        return f"{head}: FAILED ({type_name(check.error)})"
    if check.kind == "docker":
        detail = f"Docker {version_number(check.version)}, {whole_number(check.count)} containers"
    elif check.kind == "npm":
        detail = f"{whole_number(check.count)} proxy hosts"
    else:
        detail = f"PVE {version_number(check.version)}"
    return f"{head}: OK ({detail})"


def doctor_text(checks: Iterable[Check], config_found: bool, license_key: str | None) -> str:
    """doctor.txt, built from the results of the checks and not from what doctor printed."""
    lines = [
        f"HomelabScribe {__version__}",
        f"Config:   {'found' if config_found else 'not found, using defaults'}",
        f"License:  {license_line(license_key)}",
    ]
    lines += [_check_line(c) for c in checks if c.kind in _SOURCES]
    return "\n".join(lines) + "\n"


# ---- environment.json -------------------------------------------------------------------------

# The direct dependencies, used when the project's own metadata is not installed (the container image
# runs from its source folder).
DIRECT_DEPENDENCIES = (
    "docker",
    "proxmoxer",
    "requests",
    "pydantic",
    "typer",
    "jinja2",
    "markdown-it-py",
    "pyyaml",
    "cryptography",
)
_REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")


def _direct_dependencies() -> list[str]:
    try:
        requires = metadata.requires("homelabscribe") or []
    except metadata.PackageNotFoundError:
        requires = []
    names = []
    for line in requires:
        m = _REQUIREMENT_NAME.match(line)
        if m and "extra ==" not in line:  # the optional test, lint and fuzz tools are not dependencies
            names.append(m.group(0).lower())
    return sorted(set(names) or set(DIRECT_DEPENDENCIES))


def _installed(name: str) -> str:
    try:
        found = metadata.version(name)
    except Exception:
        return "not installed"
    if not isinstance(found, str):
        return UNKNOWN
    public = found.split("+", 1)[0]  # the local label (after the first `+`) can name a private host
    return public if len(public) <= 40 and _INSTALLED.fullmatch(public) else UNKNOWN


def _plain(value: str) -> str:
    return value if _PLAIN.fullmatch(value) else UNKNOWN


def environment() -> dict:
    """Versions and platform. No host name, no user name and no environment variables."""
    return {
        "homelabscribe": __version__,
        "python": _plain(platform.python_version()),
        "python_implementation": _plain(platform.python_implementation()),
        "os": _plain(platform.system()),
        "machine": _plain(platform.machine()),
        "container": Path("/.dockerenv").exists() or Path("/run/.containerenv").exists(),
        "dependencies": {name: _installed(name) for name in _direct_dependencies()},
    }


# ---- snapshot-summary.json --------------------------------------------------------------------

OTHER = "other"
HOST_KINDS: tuple[str, ...] = get_args(Host.model_fields["kind"].annotation)
SERVICE_KINDS: tuple[str, ...] = get_args(Service.model_fields["kind"].annotation)
# Container states Docker documents, then the guest states Proxmox documents.
STATUSES = ("created", "running", "paused", "restarting", "removing", "exited", "dead", "stopped")


def catalog_categories() -> tuple[str, ...]:
    """The categories of the built-in catalog (never the user's own catalog file)."""
    entries = yaml.safe_load(resources.files("homelabscribe").joinpath("catalog.yaml").read_text()) or []
    found = {e.get("category") for e in entries if isinstance(e, dict)}
    return tuple(sorted(c for c in found if isinstance(c, str)))


def _tally(values: Iterable[object], keys: Iterable[str]) -> dict[str, int]:
    """Counts under a fixed list of keys; a value that is not one of them counts under `other`."""
    counts = dict.fromkeys(keys, 0)
    counts.setdefault(OTHER, 0)
    for value in values:
        counts[value if isinstance(value, str) and value in counts else OTHER] += 1
    return counts


def snapshot_summary(state_dir: Path) -> dict:
    """Counts from the newest snapshot. No text from it is copied, and no folder is created."""
    folder = state_dir / "snapshots"
    files = sorted(p for p in folder.glob("*.json") if p.is_file()) if folder.is_dir() else []
    if not files:
        return {"snapshots": 0, "newest": "there is no snapshot yet"}
    summary: dict[str, object] = {"snapshots": len(files)}
    try:
        snap = Snapshot.model_validate_json(files[-1].read_text())
        summary["newest"] = {
            "readable": True,
            "taken_at": snap.taken_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hosts_by_kind": _tally((h.kind for h in snap.hosts), HOST_KINDS),
            "services_by_kind": _tally((s.kind for s in snap.services), SERVICE_KINDS),
            "services_by_status": _tally((s.status for s in snap.services), STATUSES),
            "services_by_category": _tally((s.category for s in snap.services), catalog_categories()),
            "routes": len(snap.routes),
        }
    except Exception as e:
        summary["newest"] = {"readable": False, "error": type_name(type(e).__name__)}
    return summary


# ---- the archive ------------------------------------------------------------------------------

README = """\
HomelabScribe diagnostic bundle
===============================

Written by `homelabscribe doctor --bundle` to attach to a bug report.

READ IT BEFORE YOU SHARE IT. Open each file and check that it says nothing you want to keep private.
The files are plain text.

What is in it
  doctor.txt             the result of each check: OK, FAILED (with the kind of error) or SKIPPED
  environment.json       versions of HomelabScribe, Python and its dependencies; operating system
                         family and machine type; whether it runs in a container
  config.redacted.yaml   which config keys are set, with the values masked
  snapshot-summary.json  how many snapshots there are, and counts from the newest one

What was removed
  Names of hosts, sources and services are replaced by numbered placeholders (docker-host-1,
  proxmox-1, npm-1, manual-1), the same one in every file. Addresses, users, tokens, passwords,
  emails, contacts, paths, descriptions and the license key are replaced by <redacted>, <set> or
  <not set>. Booleans, port numbers, the scheme of a URL (such as tcp or https) and a notification's
  `type` and `on` stay. From your servers only a version number and a count are kept, and from a
  snapshot only counts under fixed headings: no names, images, labels, addresses or notes.

What was not read
  Your generated pages, your notes and the changelog entries.
"""


def default_name(now: datetime) -> str:
    return f"homelabscribe-bundle-{now.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}.zip"


def build(cfg: Config, config_path: Path | None, checks: Iterable[Check], now: datetime) -> bytes:
    """The archive: text files only, each stamped with the time of the run and nothing of the machine."""
    found = config_path is not None and config_path.exists()
    texts = {
        "README.txt": README,
        "doctor.txt": doctor_text(checks, found, cfg.license_key),
        "environment.json": json.dumps(environment(), indent=2) + "\n",
        "config.redacted.yaml": config_text(cfg, config_path),
        "snapshot-summary.json": json.dumps(snapshot_summary(cfg.state_dir), indent=2) + "\n",
    }
    stamp = now.astimezone(UTC).timetuple()[:6]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in MEMBERS:
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # the same on every platform
            info.external_attr = 0o644 << 16
            archive.writestr(info, texts[name].encode())
    return buffer.getvalue()


def create(cfg: Config, config_path: Path | None, checks: Iterable[Check], out: Path | None = None) -> Path:
    """Write the archive and return its path. An existing file is never overwritten (`FileExistsError`)."""
    now = datetime.now(UTC)
    path = out if out is not None else Path(default_name(now))
    data = build(cfg, config_path, checks, now)
    f = path.open("xb")  # FileExistsError (or any error opening) comes before there is a file of ours
    try:
        with f:
            f.write(data)
    except OSError:  # writing, flushing or closing failed: do not leave a partial file behind
        path.unlink(missing_ok=True)
        raise
    return path
