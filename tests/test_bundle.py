"""`homelabscribe doctor --bundle`: the redacted archive for bug reports.

Each test carries the label of the behaviour it shows (B1 to B12). Sources are fakes: no network, no
Docker daemon.
"""

from __future__ import annotations

import base64
import io
import json
import re
import socket
import sys
import types
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal, Union, get_args, get_origin

import docker
import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import BaseModel
from test_collectors import _Endpoint
from typer.testing import CliRunner

from homelabscribe import __version__, bundle, licensing, notes
from homelabscribe.cli import app
from homelabscribe.collectors.npm import NpmCollector
from homelabscribe.collectors.proxmox import ProxmoxCollector
from homelabscribe.config import Config, DockerHostConfig, load_config
from homelabscribe.models import Host, Route, Service, Snapshot

runner = CliRunner()

MARK = "ZZ9LEAK7QX"  # a fixed prefix no real output contains; every seeded text starts with it
_VERIFY = licensing.verify  # the real check, before a test points it at its own key pair


# ---- fakes ------------------------------------------------------------------------------------


class Servers:
    """Stand-ins for Docker, Nginx Proxy Manager and Proxmox that count how often they are asked."""

    def __init__(self, monkeypatch, docker_info=None, npm=2, pve=None):
        self.calls: list[str] = []
        self.docker_info = docker_info or {"ServerVersion": "27.3.1", "Containers": 12}
        self.npm = npm
        self.pve = pve or {"version": "8.2.4"}
        servers = self

        class Client:
            def __init__(self, base_url=None, timeout=None):
                servers.calls.append("docker")

            @property
            def api(self):
                return self

            def info(self):
                return servers._answer(servers.docker_info)

        def collect(collector):
            servers.calls.append("npm")
            return [object()] * servers._answer(servers.npm)

        def api(collector):
            servers.calls.append("proxmox")
            return _Endpoint(None, {"version": _Endpoint(servers._answer(servers.pve))})

        monkeypatch.setattr(docker, "DockerClient", Client)
        monkeypatch.setattr(NpmCollector, "collect", collect)
        monkeypatch.setattr(ProxmoxCollector, "api", property(api))

    @staticmethod
    def _answer(value):
        if isinstance(value, Exception):
            raise value
        return value


def _license(monkeypatch, email="pro@example.com", expires=None) -> str:
    """A key signed with a throwaway key pair, which the app then checks against that pair."""
    private = Ed25519PrivateKey.generate()
    public = base64.urlsafe_b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    monkeypatch.setattr(licensing, "verify", lambda key, public_key_b64=None: _VERIFY(key, public.decode()))
    return licensing.sign(email, "pro", expires, private)


def _config(tmp_path: Path, extra: str = "", name: str = "homelabscribe.yaml") -> Path:
    path = tmp_path / name
    path.write_text(
        f"output_dir: {tmp_path / 'docs'}\n"
        f"state_dir: {tmp_path / 'state'}\n"
        "docker_hosts:\n  - name: nova\n    url: tcp://nova.lan:2375\n" + extra
    )
    return path


def _bundle(tmp_path: Path, config: Path, *args: str):
    """Run `doctor --bundle` and return (result, the opened archive)."""
    out = tmp_path / "bundle.zip"
    result = runner.invoke(app, ["doctor", "-c", str(config), "--bundle", "-o", str(out), *args])
    assert result.exit_code == 0, result.output
    return result, zipfile.ZipFile(out)


def _text(archive: zipfile.ZipFile, name: str) -> str:
    return archive.read(name).decode()


def _save(state_dir: Path, snapshot: Snapshot | str, name: str) -> Path:
    folder = state_dir / "snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(snapshot if isinstance(snapshot, str) else snapshot.model_dump_json())
    return path


# ---- B1: where the archive goes ---------------------------------------------------------------


def test_b1_bundle_is_written_to_the_current_folder_with_the_utc_time(tmp_path, monkeypatch):
    Servers(monkeypatch)
    monkeypatch.chdir(tmp_path)
    before = datetime.now(UTC).replace(microsecond=0)
    result = runner.invoke(app, ["doctor", "-c", str(_config(tmp_path)), "--bundle"])
    after = datetime.now(UTC)
    assert result.exit_code == 0, result.output
    (written,) = tmp_path.glob("*.zip")
    m = re.fullmatch(r"homelabscribe-bundle-(\d{8}T\d{6})Z\.zip", written.name)
    assert m and before <= datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC) <= after
    assert "Docker 'nova': OK (Docker 27.3.1, 12 containers)" in result.output  # the same checks ran


def test_b1_out_picks_the_file(tmp_path, monkeypatch):
    Servers(monkeypatch)
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "elsewhere" / "report.zip"
    target.parent.mkdir()
    result = runner.invoke(app, ["doctor", "-c", str(_config(tmp_path)), "--bundle", "--out", str(target)])
    assert result.exit_code == 0, result.output
    assert zipfile.is_zipfile(target) and not list(tmp_path.glob("*.zip"))


def test_b1_out_without_bundle_is_a_usage_error(tmp_path, monkeypatch):
    servers = Servers(monkeypatch)
    target = tmp_path / "report.zip"
    result = runner.invoke(app, ["doctor", "-c", str(_config(tmp_path)), "-o", str(target)])
    # A usage error is styled where colour is forced (as on a CI runner), which splits the option's name.
    message = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert result.exit_code == 2 and "--bundle" in message
    assert not target.exists() and servers.calls == []


def test_b1_an_existing_file_is_not_overwritten(tmp_path, monkeypatch):
    Servers(monkeypatch)
    target = tmp_path / "report.zip"
    target.write_text("mine")
    result = runner.invoke(app, ["doctor", "-c", str(_config(tmp_path)), "--bundle", "-o", str(target)])
    assert result.exit_code != 0
    assert "already exists" in result.output and "not overwritten" in result.output
    assert target.read_text() == "mine"


def test_b1_a_folder_that_is_missing_is_reported_without_a_traceback(tmp_path, monkeypatch):
    Servers(monkeypatch)
    target = tmp_path / "no-such-folder" / "report.zip"
    result = runner.invoke(app, ["doctor", "-c", str(_config(tmp_path)), "--bundle", "-o", str(target)])
    assert result.exit_code == 1 and "could not be written (FileNotFoundError)" in result.output


# ---- B2: what the archive holds ---------------------------------------------------------------


def test_b2_archive_holds_five_text_files_and_a_readme_that_says_read_first(tmp_path, monkeypatch):
    Servers(monkeypatch)
    _, archive = _bundle(tmp_path, _config(tmp_path))
    assert archive.namelist() == [
        "README.txt",
        "doctor.txt",
        "environment.json",
        "config.redacted.yaml",
        "snapshot-summary.json",
    ]
    for name in archive.namelist():
        text = _text(archive, name)  # decodes: text, not binary
        assert text.strip() and "\x00" not in text
    readme = _text(archive, "README.txt")
    assert "READ IT BEFORE YOU SHARE IT" in readme and "What was removed" in readme
    json.loads(_text(archive, "environment.json"))
    json.loads(_text(archive, "snapshot-summary.json"))
    yaml.safe_load(_text(archive, "config.redacted.yaml"))


# ---- B3: the redacted config ------------------------------------------------------------------

FULL_CONFIG = """\
docker_hosts:
  - name: nova
    url: tcp://nova.lan:2375
    link_host: nova.lan
  - name: nas
    url: unix:///var/run/docker.sock
  - name: odd
    url: gopher://nova.lan
    not_a_key: 1
proxmox:
  - name: homelab
    host: 192.0.2.10
    port: 8443
    user: scribe@pve
    token_name: docs
    token_value: pve-token-value
    verify_ssl: false
npm:
  - name: proxy
    url: https://npm.lan:81
    identity: viewer@example.com
    secret: npm-password
manual_services:
  - name: pihole
    host: pi
    url: http://192.0.2.2/admin
    description: Blocks ads
    restart: Unplug it
    dns: true
  - name: nas-box
    host: closet
notify:
  - type: discord
    url: https://discord.example/api/webhooks/1/abc
    "on": [errors]
  - type: ntfy
    url: ssh://ntfy.lan/topic
    token: ntfy-token
catalog_file: /config/catalog.yaml
license_key: LS1.not.real
breakglass:
  owner: Alex
  contacts: ["Sam 555-0142", "Provider 555-0199"]
  critical: [pihole, plex, vaultwarden]
  also_not_a_key: x
telemetry: please
"""


def test_b3_config_keeps_structure_and_masks_values(tmp_path, monkeypatch):
    Servers(monkeypatch)
    config = tmp_path / "homelabscribe.yaml"
    config.write_text(f"output_dir: {tmp_path / 'docs'}\nstate_dir: {tmp_path / 'state'}\n" + FULL_CONFIG)
    _, archive = _bundle(tmp_path, config)
    text = _text(archive, "config.redacted.yaml")
    r = "<redacted>"
    expected = {
        "output_dir": r,
        "state_dir": r,
        "docker_hosts": [
            {"name": "docker-host-1", "url": f"tcp://{r}", "link_host": r},
            {"name": "docker-host-2", "url": f"unix://{r}", "link_host": None},
            {"name": "docker-host-3", "url": r, "link_host": None},  # not one of the kept schemes
        ],
        "proxmox": [
            {
                "name": "proxmox-1",
                "host": r,
                "port": 8443,
                "user": r,
                "token_name": r,
                "token_value": r,
                "verify_ssl": False,
            }
        ],
        "npm": [{"name": "npm-1", "url": f"https://{r}", "identity": r, "secret": r, "verify_ssl": True}],
        "manual_services": [
            {
                "name": "manual-1",
                "host": r,
                "title": None,
                "description": r,
                "category": None,
                "url": f"http://{r}",
                "restart": r,
                "dns": True,
            },
            {
                "name": "manual-2",
                "host": r,
                "title": None,
                "description": None,
                "category": None,
                "url": None,
                "restart": None,
                "dns": False,
            },
        ],
        "notify": [
            {"type": "discord", "url": f"https://{r}", "token": None, "on": ["errors"]},
            {"type": "ntfy", "url": f"ssh://{r}", "token": r, "on": ["changes", "errors"]},
        ],
        "catalog_file": r,
        "license_key": "<set>",
        "breakglass": {"owner": r, "contacts": [r, r], "critical": [r, r, r]},  # lists keep their length
    }
    redacted = yaml.safe_load(text)
    assert {key: redacted[key] for key in expected} == expected
    # A key this test does not name (a config field of a later version) can only be masked or not set.
    assert all(redacted[key] in (r, None) for key in redacted.keys() - expected.keys())
    # Keys the config model does not know never reach the file; it gives only their number.
    assert "does not know (left out here): 3" in text
    for word in ("not_a_key", "also_not_a_key", "telemetry", "please"):
        assert word not in text


def test_b3_placeholders_are_the_same_everywhere_in_the_archive(tmp_path, monkeypatch):
    Servers(monkeypatch)
    config = _config(tmp_path, "  - name: nas\n    url: tcp://nas.lan:2375\nnpm:\n" + NPM_ENTRY)
    _, archive = _bundle(tmp_path, config)
    redacted = yaml.safe_load(_text(archive, "config.redacted.yaml"))
    doctor = _text(archive, "doctor.txt")
    assert [h["name"] for h in redacted["docker_hosts"]] == ["docker-host-1", "docker-host-2"]
    assert [n["name"] for n in redacted["npm"]] == ["npm-1"]
    for name in ("docker-host-1", "docker-host-2", "npm-1"):
        assert f"{name} (" in doctor


def test_b3_without_a_license_key_and_without_a_config_file(tmp_path, monkeypatch):
    Servers(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _, archive = _bundle(tmp_path, tmp_path / "missing.yaml")
    text = _text(archive, "config.redacted.yaml")
    redacted = yaml.safe_load(text)
    assert redacted["license_key"] == "<not set>" and "No config file was found" in text
    assert redacted["docker_hosts"] == [
        {"name": "docker-host-1", "url": "unix://<redacted>", "link_host": None}
    ]
    assert "Config:   not found, using defaults" in _text(archive, "doctor.txt")


NPM_ENTRY = "  - name: proxy\n    url: http://npm.lan:81\n    identity: viewer@example.com\n    secret: pw\n"
PVE_ENTRY = "  - name: homelab\n    host: pve.lan\n    user: u@pve\n    token_name: t\n    token_value: v\n"


# ---- B4: a field the redaction does not know --------------------------------------------------


def test_b4_a_field_the_redaction_does_not_know_is_masked_whatever_its_type():
    class Inner(BaseModel):
        secret: str = f"{MARK}-inner"

    class NewHost(DockerHostConfig):
        region: str = f"{MARK}-region"

    class NewConfig(Config):
        api_base: str = f"{MARK}-text"
        retries: int = 31337
        enabled: bool = True
        ratio: float = 2.5
        tags: list[str] = [f"{MARK}-a", f"{MARK}-b"]
        extra: dict[str, str] = {f"{MARK}-key": f"{MARK}-value"}
        inner: Inner = Inner()
        unset: str | None = None
        docker_hosts: list[NewHost] = [NewHost(name=f"{MARK}-name")]

    cfg = NewConfig()
    out = bundle.redact(cfg.model_dump(), type(cfg))
    r = bundle.REDACTED
    assert out["api_base"] == r and out["retries"] == r and out["enabled"] == r and out["ratio"] == r
    assert out["tags"] == [r, r] and out["extra"] == r and out["inner"] == r and out["unset"] is None
    # A field added to a model the redaction knows is masked too, next to the ones it keeps.
    assert out["docker_hosts"] == [
        {"name": "docker-host-1", "url": f"unix://{r}", "link_host": None, "region": r}
    ]
    dumped = yaml.safe_dump(out)
    assert MARK not in dumped and "31337" not in dumped and "2.5" not in dumped
    assert MARK not in bundle.config_text(cfg, None)


def test_b4_a_kept_field_with_an_unexpected_value_is_masked():
    r = bundle.REDACTED
    data = {
        "docker_hosts": [{"name": "x", "url": ["tcp://a"]}, "not a mapping"],
        "proxmox": [{"port": "8006", "verify_ssl": "yes"}, {"port": 70000}, {"port": True}],
        "notify": [{"type": "carrier-pigeon", "on": ["changes", "whenever"]}, {"on": "always"}],
        "npm": "not a list",
        "breakglass": ["not", "a", "mapping"],
    }
    assert bundle.redact(data) == {
        "docker_hosts": [{"name": "docker-host-1", "url": r}, r],
        "proxmox": [{"port": r, "verify_ssl": r}, {"port": r}, {"port": r}],
        "npm": r,
        "notify": [{"type": r, "on": ["changes", r]}, {"on": r}],
        "breakglass": [r, r, r],
    }
    assert bundle.redact("just text") == r and bundle.redact(None) is None
    deep: object = "x"
    for _ in range(50):
        deep = [deep]
    assert MARK not in repr(bundle.mask(deep))  # deep nesting ends in a mask, not in an error
    assert (
        bundle.unknown_keys({"docker_hosts": [{"nope": 1}, 3], "breakglass": {"owner": "a", "x": 1}, 5: 6})
        == 3
    )
    assert bundle.unknown_keys(["not", "a", "mapping"]) == 0


# ---- B5: doctor.txt ---------------------------------------------------------------------------


def test_b5_doctor_txt_is_built_from_the_results_of_the_checks(tmp_path, monkeypatch):
    Servers(monkeypatch, npm=RuntimeError("401 for viewer@example.com"))
    config = _config(tmp_path, "npm:\n" + NPM_ENTRY + "proxmox:\n" + PVE_ENTRY)
    result, archive = _bundle(tmp_path, config)
    assert _text(archive, "doctor.txt") == (
        f"HomelabScribe {__version__}\n"
        "Config:   found\n"
        "License:  Free\n"
        "docker-host-1 (Docker host): OK (Docker 27.3.1, 12 containers)\n"
        "npm-1 (Nginx Proxy Manager): FAILED (RuntimeError)\n"
        "proxmox-1 (Proxmox): SKIPPED (needs Pro)\n"
    )
    # What doctor printed has the names, the config path and the output folder; doctor.txt has none of them.
    assert "Docker 'nova': OK" in result.output and str(config) in result.output
    for private in ("nova", "proxy", "homelab", str(tmp_path), "viewer@example.com"):
        assert private not in _text(archive, "doctor.txt")


def test_b5_values_a_server_sent_are_kept_only_in_a_fixed_form():
    assert bundle.version_number("27.3.1") == "27.3.1"
    assert bundle.version_number("8.2.4-rc1+build.7 (nova.lan)") == "8.2.4"
    assert bundle.version_number("9") == "9"
    for odd in ("v27.3.1", "nova 27", "", None, 27, ["27.3.1"], "1" * 40):
        assert bundle.version_number(odd) == "unknown"
    assert bundle.whole_number(12) == 12 and bundle.whole_number("12") == 12 and bundle.whole_number(0) == 0
    for odd in ("12 on nova", "", None, -1, 1.5, True, [12], "١٢", "9" * 13):
        assert bundle.whole_number(odd) == "unknown"
    assert bundle.type_name("ConnectionError") == "ConnectionError"
    assert bundle.type_name("no such host: nova.lan") == "Exception" and bundle.type_name(None) == "Exception"

    checks = [
        bundle.Check("docker", 0, "ok", version="27.3.1-nova", count="12"),
        bundle.Check("docker", 1, "ok", version={"x": 1}, count="many"),
        bundle.Check("proxmox", 0, "ok", version="8.2.4~pve1"),
        bundle.Check("proxmox", 1, "failed", error="nova.lan refused"),
        bundle.Check("something-new", 0, "ok"),
    ]
    assert bundle.doctor_text(checks, True, None).splitlines()[3:] == [
        "docker-host-1 (Docker host): OK (Docker 27.3.1, 12 containers)",
        "docker-host-2 (Docker host): OK (Docker unknown, unknown containers)",
        "proxmox-1 (Proxmox): OK (PVE 8.2.4)",
        "proxmox-2 (Proxmox): FAILED (Exception)",
    ]


def test_b5_license_line_is_one_of_five_fixed_texts_without_email_or_date(tmp_path, monkeypatch):
    Servers(monkeypatch)
    lines = {}
    lines["none"] = bundle.license_line(None)
    lines["garbage"] = bundle.license_line("LS1.not.real")
    lines["unsigned"] = bundle.license_line("nonsense")
    end = date.today() + timedelta(days=30)
    for label, expires in (("lifetime", None), ("dated", end), ("expired", date.today() - timedelta(days=1))):
        key = _license(monkeypatch, "owner@example.com", expires)
        lines[label] = bundle.license_line(key)
        config = _config(tmp_path, f"license_key: {key}\n", name=f"{label}.yaml")
        (tmp_path / "bundle.zip").unlink(missing_ok=True)
        result, archive = _bundle(tmp_path, config)
        doctor = _text(archive, "doctor.txt")
        assert f"License:  {lines[label]}\n" in doctor
        assert "owner@example.com" in result.output  # doctor's own output still names it (unchanged)
        for name in archive.namelist():
            assert "owner@example.com" not in _text(archive, name)
            assert str(end) not in _text(archive, name) and key not in _text(archive, name)
    assert lines == {
        "none": "Free",
        "garbage": "Free, key not valid",
        "unsigned": "Free, key not valid",
        "lifetime": "Pro, lifetime",
        "dated": "Pro, with an end date",
        "expired": "Free, Pro key expired",
    }
    assert set(lines.values()) == set(bundle.LICENSE_LINES)


@pytest.mark.parametrize("key", ["none", "not valid", "expired"])
def test_b5_proxmox_without_an_active_pro_key_is_skipped_and_never_connected_to(tmp_path, monkeypatch, key):
    servers = Servers(monkeypatch)
    extra, status, line = "", "Free edition", "Free"
    if key == "not valid":
        extra, line = "license_key: LS1.not.real\n", "Free, key not valid"
        status = "Free edition (License key signature is invalid.)"
    elif key == "expired":
        gone = date.today() - timedelta(days=3)
        extra = f"license_key: {_license(monkeypatch, 'old@example.com', gone)}\n"
        status, line = (
            f"Free edition (Pro license for old@example.com expired {gone})",
            "Free, Pro key expired",
        )
    config = _config(tmp_path, "proxmox:\n" + PVE_ENTRY + extra)

    plain = runner.invoke(app, ["doctor", "-c", str(config)])
    assert plain.output == (
        f"HomelabScribe {__version__}\n"
        f"Config:   {config} (found)\n"
        f"License:  {status}\n"
        f"Output:   {tmp_path / 'docs'}\n"
        "Docker 'nova': OK (Docker 27.3.1, 12 containers)\n"
        "Proxmox 'homelab': configured, needs Pro\n"
    )
    result, archive = _bundle(tmp_path, config)
    assert result.output.startswith(plain.output)  # doctor's own output is unchanged by --bundle
    doctor = _text(archive, "doctor.txt")
    assert f"License:  {line}\n" in doctor and "proxmox-1 (Proxmox): SKIPPED (needs Pro)\n" in doctor
    assert "proxmox" not in servers.calls  # no connection attempt, with or without --bundle


def test_b5_proxmox_with_an_active_pro_key_is_tested_and_reported(tmp_path, monkeypatch):
    servers = Servers(monkeypatch, pve={"version": "8.2.4"})
    config = _config(tmp_path, "proxmox:\n" + PVE_ENTRY + f"license_key: {_license(monkeypatch)}\n")
    result, archive = _bundle(tmp_path, config)
    assert "Proxmox 'homelab': OK (PVE 8.2.4)" in result.output
    assert "proxmox-1 (Proxmox): OK (PVE 8.2.4)\n" in _text(archive, "doctor.txt")
    assert servers.calls.count("proxmox") == 1

    servers.pve = TimeoutError("pve.lan timed out")
    (tmp_path / "bundle.zip").unlink()
    _, archive = _bundle(tmp_path, config)
    assert "proxmox-1 (Proxmox): FAILED (TimeoutError)\n" in _text(archive, "doctor.txt")


# ---- B6: environment.json ---------------------------------------------------------------------


def test_b6_environment_has_versions_and_platform_and_nothing_of_the_person(tmp_path, monkeypatch):
    Servers(monkeypatch)
    import platform

    monkeypatch.setattr(platform, "node", lambda: f"{MARK}-hostname")
    for var in ("USER", "USERNAME", "LOGNAME", "HOSTNAME", "HOME", "HLS_SECRET_TOKEN"):
        monkeypatch.setenv(var, f"{MARK}-{var}")
    _, archive = _bundle(tmp_path, _config(tmp_path))
    text = _text(archive, "environment.json")
    env = json.loads(text)
    assert list(env) == [
        "homelabscribe",
        "python",
        "python_implementation",
        "os",
        "machine",
        "container",
        "dependencies",
    ]
    assert env["homelabscribe"] == __version__
    assert env["python"] == platform.python_version() and env["os"] == platform.system()
    assert env["python_implementation"] == platform.python_implementation()
    assert env["machine"] == platform.machine() and isinstance(env["container"], bool)
    # The direct dependencies, each with the version that is installed.
    assert set(bundle.DIRECT_DEPENDENCIES) <= set(env["dependencies"])
    assert all(re.fullmatch(r"[0-9][0-9A-Za-z.+!_-]*", v) for v in env["dependencies"].values())
    assert "pytest" not in env["dependencies"] and "atheris" not in env["dependencies"]
    assert MARK not in text


def test_b6_dependency_list_matches_the_project_file_and_survives_a_missing_install(monkeypatch):
    import tomllib

    declared = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]
    names = {re.match(r"[A-Za-z0-9._-]+", d).group(0).lower() for d in declared["dependencies"]}
    assert set(bundle.DIRECT_DEPENDENCIES) <= names

    def missing(name):
        raise bundle.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(bundle.metadata, "requires", missing)  # as in the container image
    monkeypatch.setattr(bundle.metadata, "version", missing)
    assert bundle.environment()["dependencies"] == dict.fromkeys(
        sorted(bundle.DIRECT_DEPENDENCIES), "not installed"
    )
    monkeypatch.setattr(bundle.metadata, "version", lambda name: "1.0 (from /home/someone)")
    assert set(bundle.environment()["dependencies"].values()) == {"unknown"}


# ---- B7: snapshot-summary.json ----------------------------------------------------------------


def _lab() -> Snapshot:
    def svc(name, kind, status, category, host="nova"):
        return Service(name=name, host=host, kind=kind, status=status, category=category)

    return Snapshot(
        taken_at=datetime(2026, 11, 3, 7, 30, tzinfo=UTC),
        hosts=[
            Host(name="nova", kind="docker"),
            Host(name="nas", kind="docker"),
            Host(name="pve1", kind="proxmox-node"),
            Host(name="pi", kind="manual"),
        ],
        services=[
            svc("plex", "container", "running", "Media"),
            svc("sonarr", "container", "running", "Media"),
            svc("grafana", "container", "exited", "Monitoring"),
            svc("custom", "container", "restarting", "My own category"),
            svc("ha", "vm", "running", "Virtual machines", "pve1"),
            svc("pbs", "lxc", "stopped", "Containers (LXC)", "pve1"),
            svc("pihole", "manual", "not scanned", "Network", "pi"),
            svc("odd", "container", "Up 3 hours (healthy) on nova", "Other"),
        ],
        routes=[
            Route(proxy="npm", domains=["a.example"], forward_host="nova", forward_port=80),
            Route(proxy="npm", domains=["b.example"], forward_host="nova", forward_port=81),
        ],
    )


def test_b7_summary_counts_under_fixed_keys_and_copies_no_text(tmp_path, monkeypatch):
    Servers(monkeypatch)
    state = tmp_path / "state"
    _save(state, Snapshot(taken_at=datetime(2026, 11, 1, tzinfo=UTC)), "20261101T000000Z.json")
    _save(state, _lab(), "20261103T073000Z.json")
    _, archive = _bundle(tmp_path, _config(tmp_path))
    text = _text(archive, "snapshot-summary.json")
    summary = json.loads(text)
    assert summary["snapshots"] == 2
    newest = summary["newest"]
    assert newest["readable"] is True and newest["taken_at"] == "2026-11-03T07:30:00Z"
    assert newest["hosts_by_kind"] == {"docker": 2, "proxmox-node": 1, "manual": 1, "other": 0}
    assert newest["services_by_kind"] == {"container": 5, "vm": 1, "lxc": 1, "manual": 1, "other": 0}
    assert newest["services_by_status"] == {
        "created": 0,
        "running": 3,
        "paused": 0,
        "restarting": 1,
        "removing": 0,
        "exited": 1,
        "dead": 0,
        "stopped": 1,
        "other": 2,
    }
    categories = newest["services_by_category"]
    assert list(categories) == [*bundle.catalog_categories(), "other"]
    assert {"Media", "Monitoring", "Network", "Security", "Home"} <= set(categories)
    assert categories["Media"] == 2 and categories["Monitoring"] == 1 and categories["Network"] == 1
    assert categories["other"] == 4 and sum(categories.values()) == 8
    assert newest["routes"] == 2
    for copied in ("plex", "nova", "pve1", "a.example", "My own category", "healthy", "Virtual machines"):
        assert copied not in text


def test_b7_with_no_snapshot_it_says_so_and_creates_no_state_folder(tmp_path, monkeypatch):
    Servers(monkeypatch)
    _, archive = _bundle(tmp_path, _config(tmp_path))
    assert json.loads(_text(archive, "snapshot-summary.json")) == {
        "snapshots": 0,
        "newest": "there is no snapshot yet",
    }
    assert not (tmp_path / "state").exists() and not (tmp_path / "docs").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bundle.zip", "homelabscribe.yaml"]


@pytest.mark.parametrize(
    "content,error",
    [
        ('{"hosts": [{"name": "nova-secret", "kind": "docker"', "ValidationError"),  # damaged: cut off
        (
            json.dumps({"services": [{"name": "plex", "host": "nova", "kind": "nova-secret"}]}),
            "ValidationError",
        ),
        (json.dumps({"taken_at": "nova-secret"}), "ValidationError"),
        (b"\xff\xfe nova-secret".decode("latin-1"), "ValidationError"),
    ],
)
def test_b7_a_newest_snapshot_that_cannot_be_read_is_reported_and_the_bundle_is_written(
    tmp_path, monkeypatch, content, error
):
    Servers(monkeypatch)
    state = tmp_path / "state"
    _save(state, _lab(), "20261103T073000Z.json")
    _save(state, content, "20261104T073000Z.json")
    _, archive = _bundle(tmp_path, _config(tmp_path))
    text = _text(archive, "snapshot-summary.json")
    assert json.loads(text) == {"snapshots": 2, "newest": {"readable": False, "error": error}}
    assert "nova-secret" not in text and archive.namelist() == list(bundle.MEMBERS)


def test_b7_a_snapshot_file_that_is_not_text_is_reported_by_the_type_of_the_error(tmp_path):
    path = _save(tmp_path, "", "20261104T073000Z.json")
    path.write_bytes(b"\xff\xfe\x00\x01")
    assert bundle.snapshot_summary(tmp_path) == {
        "snapshots": 1,
        "newest": {"readable": False, "error": "UnicodeDecodeError"},
    }


# ---- B8: nothing else is read -----------------------------------------------------------------

_opened: list[str] | None = None


def _audit(event, args):
    if _opened is not None and event == "open" and isinstance(args[0], str):
        _opened.append(args[0])


sys.addaudithook(_audit)  # cannot be removed again, so it only records while a test asks it to


def test_b8_no_generated_page_no_notes_and_no_changelog_entries_are_read(tmp_path, monkeypatch):
    global _opened
    Servers(monkeypatch)
    config = _config(tmp_path)
    docs, state = tmp_path / "docs", tmp_path / "state"
    (docs / "services").mkdir(parents=True)
    page = f"# Plex\n\n{notes.START}\n{MARK} the safe code is 1234\n{notes.END}\n"
    (docs / "index.md").write_text(page)
    (docs / "services" / "plex.md").write_text(page)
    (docs / "CHANGELOG.md").write_text(f"- **Added** {MARK}-service\n")
    older = _save(state, _lab(), "20261103T073000Z.json")
    newest = _save(state, _lab(), "20261104T073000Z.json")
    (state / "changelog.json").write_text(json.dumps([{"at": "x", "changes": [{"service": f"{MARK}-plex"}]}]))
    (state / "notify-state.json").write_text(json.dumps({"errors": [f"{MARK}-error"]}))

    _opened = []
    try:
        _, archive = _bundle(tmp_path, config)
    finally:
        opened, _opened = _opened, None
    inside = {p for p in opened if Path(p).is_relative_to(tmp_path)}
    assert inside == {str(config), str(newest), str(tmp_path / "bundle.zip")}
    assert str(older) not in inside
    for name in archive.namelist():
        assert MARK not in _text(archive, name) and "1234" not in _text(archive, name)


# ---- B9 and B10: what the command prints ------------------------------------------------------


def test_b9_prints_the_path_asks_to_look_inside_and_exits_like_doctor(tmp_path, monkeypatch):
    Servers(monkeypatch, docker_info=ConnectionError("refused"))
    config = _config(tmp_path)
    plain = runner.invoke(app, ["doctor", "-c", str(config)])
    result, _ = _bundle(tmp_path, config)
    assert "Docker 'nova': FAILED (ConnectionError)" in plain.output
    assert result.exit_code == plain.exit_code == 0  # a failed check does not change doctor's exit status
    assert result.output == plain.output + (
        f"Wrote {tmp_path / 'bundle.zip'}\n"
        "Look inside before you share it: it is plain text, and you are the last check.\n"
    )


def test_b10_doctor_without_bundle_prints_exactly_what_it_printed_before(tmp_path, monkeypatch):
    Servers(monkeypatch, npm=RuntimeError("401"))
    extra = "  - name: nas\n    url: tcp://nas.lan:2375\nnpm:\n" + NPM_ENTRY + "proxmox:\n" + PVE_ENTRY
    config = _config(tmp_path, extra + f"license_key: {_license(monkeypatch, 'pro@example.com')}\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor", "-c", str(config)])
    assert result.exit_code == 0
    assert result.output == (
        f"HomelabScribe {__version__}\n"
        f"Config:   {config} (found)\n"
        "License:  Pro — licensed to pro@example.com (lifetime)\n"
        f"Output:   {tmp_path / 'docs'}\n"
        "Docker 'nova': OK (Docker 27.3.1, 12 containers)\n"
        "Docker 'nas': OK (Docker 27.3.1, 12 containers)\n"
        "Nginx Proxy Manager 'proxy': FAILED (RuntimeError)\n"
        "Proxmox 'homelab': OK (PVE 8.2.4)\n"
    )
    assert not list(tmp_path.glob("*.zip"))  # and it writes nothing

    missing = runner.invoke(app, ["doctor", "-c", str(tmp_path / "nope.yaml")])
    assert missing.output == (
        f"HomelabScribe {__version__}\n"
        f"Config:   {tmp_path / 'nope.yaml'} (not found, using defaults)\n"
        "License:  Free edition\n"
        "Output:   homelabscribe-docs\n"
        "Docker 'docker': OK (Docker 27.3.1, 12 containers)\n"
    )


# ---- B11: the leak test -----------------------------------------------------------------------


class Marks:
    """Hands out a fresh marked text for every place the leak test seeds."""

    def __init__(self, tmp_path: Path):
        self.tmp_path, self.count = tmp_path, 0

    def text(self, where: str) -> str:
        self.count += 1
        return f"{MARK}-{where}-{self.count}"


def _fill(model: type[BaseModel], marks: Marks, urls: bool) -> dict:
    """A value for every field of a model, found by walking it: no hand-kept list of fields."""
    return {name: _value(field.annotation, name, marks, urls) for name, field in model.model_fields.items()}


def _value(annotation, name: str, marks: Marks, urls: bool):
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):  # `X | None`: seed the X
        return _value(next(a for a in get_args(annotation) if a is not type(None)), name, marks, urls)
    if origin is Literal:
        return get_args(annotation)[0]  # a fixed word the model checks: left as it is
    if origin is list:
        (inner,) = get_args(annotation)
        if get_origin(inner) is Literal:
            return list(get_args(inner))
        return [_value(inner, name, marks, urls), _value(inner, name, marks, urls)]
    if origin is dict:
        return {marks.text(f"{name}-key"): marks.text(f"{name}-value")}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _fill(annotation, marks, urls)
    if annotation is bool:
        return True
    if annotation is int:
        return 8006
    if annotation is float:
        return 1.5
    if annotation is datetime:
        return datetime.now(UTC).isoformat()
    if annotation is str:
        text = marks.text(name)
        return f"https://{text}.invalid:8443/{text}?token={text}" if urls and name == "url" else text
    if annotation is Path:
        return str(marks.tmp_path / marks.text(name))
    raise AssertionError(f"the leak test does not know how to seed a {annotation!r} field ({name})")


def _through_env(value, monkeypatch, counter=None):
    """Replace every seeded text by `${VAR}` and put the text into the environment instead."""
    counter = counter if counter is not None else [0]
    if isinstance(value, dict):
        return {k: _through_env(v, monkeypatch, counter) for k, v in value.items()}
    if isinstance(value, list):
        return [_through_env(v, monkeypatch, counter) for v in value]
    if isinstance(value, str) and MARK in value:
        counter[0] += 1
        monkeypatch.setenv(f"HLS_LEAK_TEST_{counter[0]}", value)
        return f"${{HLS_LEAK_TEST_{counter[0]}}}"
    return value


def _assert_no_marker(path: Path) -> zipfile.ZipFile:
    raw = path.read_bytes()
    assert MARK.encode() not in raw and MARK.lower().encode() not in raw.lower()
    archive = zipfile.ZipFile(io.BytesIO(raw))
    assert archive.comment == b""
    for info in archive.infolist():
        content = archive.read(info).decode()
        assert MARK.lower() not in content.lower(), info.filename
        assert MARK.lower() not in info.filename.lower()
        assert info.comment == b"" and info.extra == b""
    return archive


@pytest.mark.parametrize("license_kind", ["garbage", "valid", "expired"])
@pytest.mark.parametrize("via_env", [False, True], ids=["in-file", "through-env"])
@pytest.mark.parametrize("urls", [True, False], ids=["urls", "bare"])
def test_b11_no_seeded_marker_reaches_the_archive(tmp_path, monkeypatch, license_kind, via_env, urls):
    marks = Marks(tmp_path)

    # The config: a marker in every field that takes free text or a path, found by walking the models.
    data = _fill(Config, marks, urls)
    seeded = marks.count
    assert seeded >= 40  # every text field of every config model, two entries per list
    if license_kind != "garbage":
        expires = None if license_kind == "valid" else date.today() - timedelta(days=1)
        data["license_key"] = _license(monkeypatch, f"{MARK}-owner@example.com", expires)
    state_dir, output_dir = Path(data["state_dir"]), Path(data["output_dir"])
    config = tmp_path / f"{MARK}-config.yaml"
    config.write_text(yaml.safe_dump(_through_env(data, monkeypatch) if via_env else data))
    loaded = load_config(config)
    assert MARK in loaded.proxmox[0].token_value and MARK in loaded.npm[1].secret  # the seeds arrived
    assert MARK in loaded.breakglass.contacts[1] and MARK in str(loaded.catalog_file)

    # A valid snapshot with a marker in every text it holds (names, images, labels, variable names,
    # mounts, routes, statuses, categories); its kinds stay valid.
    snapshot = Snapshot.model_validate(_fill(Snapshot, marks, urls))
    assert marks.count - seeded >= 60
    service = snapshot.services[0]
    assert all(MARK in text for text in (service.name, service.image, service.status, service.category))
    assert (
        MARK in service.env_names[0]
        and MARK in service.mounts[0].source
        and MARK in next(iter(service.labels))
    )
    assert MARK in snapshot.routes[0].domains[0] and MARK in snapshot.hosts[0].name
    _save(state_dir, snapshot, "20261103T073000Z.json")
    (output_dir / "services").mkdir(parents=True)
    (output_dir / "services" / "plex.md").write_text(f"{notes.START}\n{MARK}-note\n{notes.END}\n")
    (state_dir / "changelog.json").write_text(json.dumps([{"at": f"{MARK}-at", "changes": []}]))

    # Servers that answer with a marker as version and count, then fail with a marker in the error.
    servers = Servers(monkeypatch)
    answers = {
        "docker": [
            {"ServerVersion": f"{MARK}-27.3.1", "Containers": f"{MARK}-12"},
            OSError(f"{MARK}-refused"),
        ],
        "npm": [3, RuntimeError(f"{MARK}-401")],
        "proxmox": [{"version": f"{MARK}-8.2.4"}, ConnectionError(f"{MARK}-unreachable")],
    }

    def answer(value):
        kind = servers.calls[-1]
        return Servers._answer(answers[kind][servers.calls.count(kind) - 1])

    monkeypatch.setattr(servers, "_answer", answer)

    started = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=2)  # a zip keeps even seconds
    out = tmp_path / "bundle.zip"
    result = runner.invoke(app, ["doctor", "-c", str(config), "--bundle", "-o", str(out)])
    finished = datetime.now(UTC)
    assert result.exit_code == 0, result.output
    assert f"{MARK}-27.3.1" in result.output  # doctor itself still prints what the server said
    archive = _assert_no_marker(out)

    # The members carry the time of the run and nothing else of the machine.
    for info in archive.infolist():
        assert started <= datetime(*info.date_time, tzinfo=UTC) <= finished
        assert info.create_system == 3 and info.external_attr == 0o644 << 16

    # The fixed words, booleans, port numbers and URL schemes are kept; the rest is masked.
    redacted = yaml.safe_load(_text(archive, "config.redacted.yaml"))
    first, *_ = get_args(Config.model_fields["notify"].annotation)[0].model_fields["type"].annotation.__args__
    assert [n["type"] for n in redacted["notify"]] == [first, first]
    assert [n["on"] for n in redacted["notify"]] == [["changes", "errors"]] * 2
    assert [n["url"] for n in redacted["notify"]] == ["https://<redacted>" if urls else "<redacted>"] * 2
    assert [p["port"] for p in redacted["proxmox"]] == [8006, 8006]
    assert [p["verify_ssl"] for p in redacted["proxmox"]] == [True, True]
    assert [h["name"] for h in redacted["docker_hosts"]] == ["docker-host-1", "docker-host-2"]
    assert [m["name"] for m in redacted["manual_services"]] == ["manual-1", "manual-2"]
    assert redacted["license_key"] == "<set>" and redacted["breakglass"]["contacts"] == ["<redacted>"] * 2

    pro = license_kind == "valid"
    license_line = {
        "garbage": "Free, key not valid",
        "valid": "Pro, lifetime",
        "expired": "Free, Pro key expired",
    }
    assert _text(archive, "doctor.txt").splitlines()[2:] == [
        f"License:  {license_line[license_kind]}",
        "docker-host-1 (Docker host): OK (Docker unknown, unknown containers)",
        "docker-host-2 (Docker host): FAILED (OSError)",
        "npm-1 (Nginx Proxy Manager): OK (3 proxy hosts)",
        "npm-2 (Nginx Proxy Manager): FAILED (RuntimeError)",
        "proxmox-1 (Proxmox): " + ("OK (PVE unknown)" if pro else "SKIPPED (needs Pro)"),
        "proxmox-2 (Proxmox): " + ("FAILED (ConnectionError)" if pro else "SKIPPED (needs Pro)"),
    ]
    assert servers.calls.count("proxmox") == (2 if pro else 0)

    # The snapshot's kinds land in their own counts; its statuses and categories count under "other".
    newest = json.loads(_text(archive, "snapshot-summary.json"))["newest"]
    assert newest["readable"] is True
    assert newest["hosts_by_kind"] == {"docker": 2, "proxmox-node": 0, "manual": 0, "other": 0}
    assert newest["services_by_kind"] == {"container": 2, "vm": 0, "lxc": 0, "manual": 0, "other": 0}
    assert newest["services_by_status"]["other"] == 2 and newest["services_by_category"]["other"] == 2
    assert newest["routes"] == 2

    # Second case: a newer snapshot file whose kind the models reject, with a marker as that kind.
    rejected = snapshot.model_dump(mode="json")
    rejected["services"][0]["kind"] = f"{MARK}-kind"
    _save(state_dir, json.dumps(rejected), "20261104T073000Z.json")
    out.unlink()
    assert runner.invoke(app, ["doctor", "-c", str(config), "--bundle", "-o", str(out)]).exit_code == 0
    archive = _assert_no_marker(out)
    assert json.loads(_text(archive, "snapshot-summary.json")) == {
        "snapshots": 2,
        "newest": {"readable": False, "error": "ValidationError"},
    }


# ---- B12: no network call of its own ----------------------------------------------------------


def test_b12_the_bundle_adds_no_network_call_to_doctors_connection_tests(tmp_path, monkeypatch):
    servers = Servers(monkeypatch)
    extra = "npm:\n" + NPM_ENTRY + "proxmox:\n" + PVE_ENTRY + f"license_key: {_license(monkeypatch)}\n"
    config = _config(tmp_path, extra)
    _save(tmp_path / "state", _lab(), "20261103T073000Z.json")

    def no_network(*args, **kwargs):
        raise AssertionError("the bundle opened a network connection")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)

    assert runner.invoke(app, ["doctor", "-c", str(config)]).exit_code == 0
    plain, servers.calls = servers.calls, []
    _bundle(tmp_path, config)
    assert servers.calls == plain == ["docker", "npm", "proxmox"]  # one test per source, bundle or not

    # Building the archive from finished checks asks no server anything.
    servers.calls = []
    checks = [bundle.Check("docker", 0, "ok", version="27.3.1", count=12)]
    data = bundle.build(load_config(config), config, checks, datetime.now(UTC))
    assert zipfile.ZipFile(io.BytesIO(data)).namelist() == list(bundle.MEMBERS) and servers.calls == []
