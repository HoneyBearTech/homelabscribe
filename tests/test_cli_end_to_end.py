"""End-to-end CLI runs: real config file, real scan/diff/render pipeline, fake Docker daemon only."""

import pytest
from test_collectors import FakeDockerClient
from typer.testing import CliRunner

from homelabscribe import __version__, notes
from homelabscribe.cli import app
from homelabscribe.collectors import docker as docker_collector

runner = CliRunner()


@pytest.fixture
def config(tmp_path, monkeypatch):
    """A config file pointing at tmp dirs, with the Docker client swapped for the fake."""
    monkeypatch.setattr(docker_collector.DockerCollector, "client", property(lambda self: FakeDockerClient()))
    path = tmp_path / "homelabscribe.yaml"
    path.write_text(
        f"output_dir: {tmp_path / 'docs'}\n"
        f"state_dir: {tmp_path / 'state'}\n"
        "docker_hosts:\n  - name: nova\n    url: tcp://fake:2375\n"
    )
    return path


def test_version_and_demo_commands(tmp_path):
    assert __version__ in runner.invoke(app, ["version"]).output
    out = tmp_path / "demo"
    result = runner.invoke(app, ["demo", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "index.md").exists() and (out / "topology.md").exists()


def test_run_scans_renders_and_keeps_notes_on_the_next_run(config, tmp_path):
    first = runner.invoke(app, ["run", "-c", str(config)])
    assert first.exit_code == 0, first.output
    assert "Scanned 1 host(s), 1 service(s)." in first.output
    page = tmp_path / "docs" / "services" / "sonarr.md"
    assert page.exists() and page.read_text().rstrip().endswith("<!-- homelabscribe:notes:end -->")
    assert list((tmp_path / "state" / "snapshots").glob("*.json"))

    page.write_text(page.read_text().replace(notes.PLACEHOLDER, "My own note."))
    second = runner.invoke(app, ["run", "-c", str(config)])
    assert second.exit_code == 0, second.output
    assert "My own note." in page.read_text()


def test_scan_then_render_as_separate_commands(config, tmp_path):
    assert runner.invoke(app, ["scan", "-c", str(config)]).exit_code == 0
    assert not (tmp_path / "docs" / "index.md").exists()
    rendered = runner.invoke(app, ["render", "-c", str(config)])
    assert rendered.exit_code == 0, rendered.output
    assert (tmp_path / "docs" / "index.md").exists()


def test_snapshots_never_hold_secret_values(config, tmp_path):
    runner.invoke(app, ["run", "-c", str(config)])
    stored = "".join(p.read_text() for p in (tmp_path / "state").rglob("*.json"))
    docs = "".join(p.read_text() for p in (tmp_path / "docs").rglob("*.md"))
    assert "supersecretvalue123" not in stored + docs


def test_strict_fails_when_a_host_cannot_be_read(config, monkeypatch):
    def down(self):
        raise ConnectionError("host is down")

    monkeypatch.setattr(docker_collector.DockerCollector, "client", property(down))
    assert runner.invoke(app, ["run", "-c", str(config)]).exit_code == 0
    assert runner.invoke(app, ["run", "-c", str(config), "--strict"]).exit_code == 1


def test_license_command_reports_free_without_a_key(config):
    result = runner.invoke(app, ["license", "-c", str(config)])
    assert result.exit_code == 0 and "free" in result.output.lower()
