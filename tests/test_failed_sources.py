"""A source that is down for one scan must not look like everything on it was removed."""

import pytest
from test_collectors import FakeDockerClient
from typer.testing import CliRunner

from homelabscribe import licensing, pipeline
from homelabscribe.cli import app
from homelabscribe.collectors.docker import DockerCollector
from homelabscribe.config import Config, DockerHostConfig


class DownClient:
    @property
    def api(self):
        raise ConnectionError("host is down")


def _cfg(tmp_path):
    return Config(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "out",
        docker_hosts=[DockerHostConfig(name="nova"), DockerHostConfig(name="other")],
    )


def _sources(client_for_nova, client_for_other):
    cfgs = [DockerHostConfig(name="nova"), DockerHostConfig(name="other")]
    return [
        ("Docker host 'nova'", DockerCollector(cfgs[0], client=client_for_nova)),
        ("Docker host 'other'", DockerCollector(cfgs[1], client=client_for_other)),
    ]


def test_a_failed_host_keeps_its_last_known_services_and_is_not_reported_removed(tmp_path):
    cfg = _cfg(tmp_path)
    first = pipeline.scan(cfg, licensing.PRO_FEATURES, _sources(FakeDockerClient(), FakeDockerClient()))
    assert not first.errors and len(first.snapshot.services) == 2

    second = pipeline.scan(cfg, licensing.PRO_FEATURES, _sources(FakeDockerClient(), DownClient()))
    assert len(second.errors) == 1 and "other" in second.errors[0]
    assert {h.name for h in second.snapshot.hosts} == {"nova", "other"}
    assert {s.host for s in second.snapshot.services} == {"nova", "other"}
    assert second.diff is not None and second.diff.empty  # nothing "removed"


def test_without_a_previous_scan_a_failed_host_is_simply_missing(tmp_path):
    result = pipeline.scan(_cfg(tmp_path), licensing.PRO_FEATURES, _sources(FakeDockerClient(), DownClient()))
    assert len(result.errors) == 1 and {h.name for h in result.snapshot.hosts} == {"nova"}


@pytest.mark.parametrize("command", ["run", "scan"])
def test_strict_exits_nonzero_when_a_source_fails(tmp_path, monkeypatch, command):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        f"state_dir: {tmp_path / 's'}\noutput_dir: {tmp_path / 'o'}\n"
        "docker_hosts:\n  - {name: nova, url: 'tcp://127.0.0.1:1'}\n"
    )

    def down(self):
        raise ConnectionError("down")

    monkeypatch.setattr(DockerCollector, "collect", down)
    runner = CliRunner()
    assert runner.invoke(app, [command, "-c", str(cfg)]).exit_code == 0  # default: errors are only reported
    strict = runner.invoke(app, [command, "-c", str(cfg), "--strict"])
    assert strict.exit_code == 1 and "error:" in strict.output


def test_unreadable_source_reason_is_short():
    from homelabscribe.pipeline import _reason

    long = "HTTPConnectionPool(host='x', port=2375): Max retries (Errno 61] Connection refused"
    assert _reason(Exception(long)) == "connection refused"
    assert _reason(Exception("boom\nsecond line")) == "boom"
