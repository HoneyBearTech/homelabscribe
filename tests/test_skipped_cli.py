"""Skipped files in the command line: one `skipped:` line each, exit 0, exit 1 with --strict."""

import json
import os

import pytest
from test_collectors import FakeDockerClient
from typer.testing import CliRunner

from homelabscribe import notes
from homelabscribe.cli import app
from homelabscribe.collectors import docker as docker_collector

runner = CliRunner()


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(docker_collector.DockerCollector, "client", property(lambda self: FakeDockerClient()))
    path = tmp_path / "homelabscribe.yaml"
    path.write_text(
        f"output_dir: {tmp_path / 'docs'}\n"
        f"state_dir: {tmp_path / 'state'}\n"
        f"html_dir: {tmp_path / 'html'}\n"
        "docker_hosts:\n  - name: nova\n    url: tcp://fake:2375\n"
    )
    return path


def _bad_page(config, tmp_path):
    assert runner.invoke(app, ["run", "-c", str(config)]).exit_code == 0
    page = tmp_path / "docs" / "services" / "sonarr.md"
    page.write_bytes(page.read_text().encode("utf-16"))
    (tmp_path / "docs" / "index.md").write_text("changed\n")
    return page


def test_run_reports_an_unreadable_page_and_goes_on(config, tmp_path):
    page = _bad_page(config, tmp_path)
    before = page.read_bytes()
    result = runner.invoke(app, ["run", "-c", str(config)])
    assert result.exit_code == 0, result.output
    assert result.output.count("skipped: services/sonarr.md (not UTF-8, left as it is)") == 1
    assert "Traceback" not in result.output and str(tmp_path) not in result.output.split("Updated")[0]
    assert page.read_bytes() == before
    assert "Updated 1 file(s)" in result.output  # index.md only: the skipped page is not counted
    assert (tmp_path / "html" / "index.html").exists()


def test_run_strict_exits_1_after_writing_the_docs(config, tmp_path):
    _bad_page(config, tmp_path)
    result = runner.invoke(app, ["run", "-c", str(config), "--strict"])
    assert result.exit_code == 1 and "skipped: services/sonarr.md" in result.output
    assert (tmp_path / "docs" / "index.md").read_text() != "changed\n"


def test_render_reports_the_skip_and_exits_0(config, tmp_path):
    _bad_page(config, tmp_path)
    result = runner.invoke(app, ["render", "-c", str(config)])
    assert result.exit_code == 0 and "skipped: services/sonarr.md" in result.output


@pytest.mark.skipif(os.name != "posix", reason="needs symbolic links")
def test_a_page_reported_by_the_render_is_not_reported_again_by_the_html_copy(config, tmp_path):
    assert runner.invoke(app, ["run", "-c", str(config)]).exit_code == 0
    page = tmp_path / "docs" / "services" / "sonarr.md"
    page.unlink()
    page.symlink_to(tmp_path / "nowhere.md")
    result = runner.invoke(app, ["render", "-c", str(config)])
    assert result.exit_code == 0
    assert result.output.count("skipped: services/sonarr.md") == 1


def _two_copies(tmp_path):
    """Two files HomelabScribe did not write, unreadable, whose names differ only after ` (`."""
    assert runner.invoke(app, ["run", "-c", str(tmp_path / "homelabscribe.yaml")]).exit_code == 0
    for n in (1, 2):
        (tmp_path / "docs" / "services" / f"plex ({n}).md").write_bytes(os.urandom(64) + b"\xff")


@pytest.mark.parametrize("command", ["render", "run"])
def test_two_unreadable_files_named_alike_each_get_a_line(config, tmp_path, command):
    _two_copies(tmp_path)
    result = runner.invoke(app, [command, "-c", str(config)])
    assert result.exit_code == 0, result.output
    for n in (1, 2):
        assert result.output.count(f"skipped: services/plex ({n}).md (not UTF-8, left as it is)") == 1


def _damage_newest_snapshot(tmp_path):
    bad = tmp_path / "state" / "snapshots" / "99990101T000000Z.json"
    bad.write_bytes(b'{"hosts": [')
    return bad


def test_scan_skips_a_damaged_snapshot_and_strict_fails(config, tmp_path):
    assert runner.invoke(app, ["scan", "-c", str(config)]).exit_code == 0
    bad = _damage_newest_snapshot(tmp_path)
    result = runner.invoke(app, ["scan", "-c", str(config)])
    assert result.exit_code == 0 and "skipped: snapshots/99990101T000000Z.json (damaged" in result.output
    assert bad.read_bytes() == b'{"hosts": ['
    _damage_newest_snapshot(tmp_path)
    assert runner.invoke(app, ["scan", "-c", str(config), "--strict"]).exit_code == 1


def test_scan_with_only_damaged_snapshots_is_a_first_scan(config, tmp_path):
    (tmp_path / "state" / "snapshots").mkdir(parents=True)
    _damage_newest_snapshot(tmp_path)
    result = runner.invoke(app, ["scan", "-c", str(config)])
    assert result.exit_code == 0 and "Scanned 1 host(s)" in result.output
    assert len(list((tmp_path / "state" / "snapshots").glob("*.json"))) == 2


def test_render_with_no_readable_snapshot_says_why(config, tmp_path):
    (tmp_path / "state" / "snapshots").mkdir(parents=True)
    _damage_newest_snapshot(tmp_path)
    result = runner.invoke(app, ["render", "-c", str(config)])
    assert result.exit_code == 1 and "No snapshot yet" in result.output and "snapshots/9999" in result.output
    assert "Traceback" not in result.output


def test_an_unwritable_snapshot_folder_is_one_error_line(config, tmp_path):
    (tmp_path / "state").write_text("a file where the folder should be")
    for args in (["scan"], ["run"]):
        result = runner.invoke(app, [*args, "-c", str(config)])
        assert result.exit_code == 1 and result.output.count("error:") == 1
        assert isinstance(result.exception, SystemExit)  # no traceback
        assert str(tmp_path) not in result.output


def test_scan_moves_a_damaged_changelog_aside(config, tmp_path):
    assert runner.invoke(app, ["scan", "-c", str(config)]).exit_code == 0
    (tmp_path / "state" / "changelog.json").write_text("[{")
    # the saved lab loses its services, so the next scan has a change to record
    (snap,) = (tmp_path / "state" / "snapshots").glob("*.json")
    data = json.loads(snap.read_text())
    data["services"] = []
    snap.write_text(json.dumps(data))
    result = runner.invoke(app, ["scan", "-c", str(config), "--strict"])
    assert result.exit_code == 1 and "skipped: changelog.json (damaged" in result.output
    assert (tmp_path / "state" / "changelog.json.damaged").read_text() == "[{"
    assert (tmp_path / "state" / "changelog.json").read_text().startswith("[")


def test_a_page_that_cannot_be_archived_is_reported_and_the_run_goes_on(config, tmp_path):
    assert runner.invoke(app, ["run", "-c", str(config)]).exit_code == 0
    page = tmp_path / "docs" / "services" / "old.md"
    page.write_text(f"x\n{notes.START}\nMy notes\n{notes.END}\n")
    (tmp_path / "docs" / "archive").mkdir()
    (tmp_path / "docs" / "archive" / "services").write_text("in the way")
    (tmp_path / "docs" / "index.md").write_text("changed\n")
    result = runner.invoke(app, ["run", "-c", str(config)])
    assert result.exit_code == 0, result.output
    assert "skipped: services/old.md (could not be archived, left as it is)" in result.output
    assert "My notes" in page.read_text() and "Traceback" not in result.output
    assert "HTML file(s)" in result.output
    strict = runner.invoke(app, ["run", "-c", str(config), "--strict"])
    assert strict.exit_code == 1 and "HTML file(s)" in strict.output


def test_strict_fails_on_a_page_only_the_html_export_skipped(config, tmp_path):
    assert runner.invoke(app, ["run", "-c", str(config)]).exit_code == 0
    mine = tmp_path / "html" / "services" / "sonarr.html"
    mine.write_text("a page the user wrote")  # not one of ours, so the export leaves it
    plain = runner.invoke(app, ["run", "-c", str(config)])
    assert plain.exit_code == 0 and "skipped: services/sonarr.html" in plain.output
    strict = runner.invoke(app, ["run", "-c", str(config), "--strict"])
    assert strict.exit_code == 1 and mine.read_text() == "a page the user wrote"


def test_a_changelog_that_cannot_be_moved_aside_is_not_written_over(config, tmp_path):
    assert runner.invoke(app, ["scan", "-c", str(config)]).exit_code == 0
    state = tmp_path / "state"
    (state / "changelog.json").write_text("[{")
    for name in ["changelog.json.damaged", *(f"changelog.json.damaged-{n}" for n in range(2, 1000))]:
        (state / name).write_text("older")
    (snap,) = (state / "snapshots").glob("*.json")
    data = json.loads(snap.read_text())
    data["services"] = []
    snap.write_text(json.dumps(data))
    result = runner.invoke(app, ["scan", "-c", str(config)])
    assert result.exit_code == 0 and "skipped: changelog.json (damaged" in result.output
    assert (state / "changelog.json").read_text() == "[{"


def test_a_blocked_changelog_is_printed_once_in_a_run(config, tmp_path):
    assert runner.invoke(app, ["run", "-c", str(config)]).exit_code == 0
    state = tmp_path / "state"
    (state / "changelog.json").write_text("[{")
    for name in ["changelog.json.damaged", *(f"changelog.json.damaged-{n}" for n in range(2, 1000))]:
        (state / name).write_text("older")
    (snap,) = (state / "snapshots").glob("*.json")
    data = json.loads(snap.read_text())
    data["services"] = []
    snap.write_text(json.dumps(data))
    result = runner.invoke(app, ["run", "-c", str(config)])
    assert result.exit_code == 0, result.output
    assert result.output.count("skipped: changelog.json") == 1


# --- notifications from `run` ---------------------------------------------------------------------------


@pytest.fixture
def posted(config, tmp_path, monkeypatch):
    """A Pro run with a Discord target and a fake `requests.post`: the messages sent, never a real request."""
    import requests

    from homelabscribe import licensing

    monkeypatch.setattr(licensing, "features", lambda key: (licensing.PRO_FEATURES, "Pro"))
    config.write_text(
        config.read_text() + "notify:\n  - type: discord\n    url: http://discord.invalid/hook\n"
    )
    sent: list[str] = []

    class _Response:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(
        requests, "post", lambda url, json=None, **kw: sent.append(json["content"]) or _Response()
    )
    return sent


def test_run_sends_one_message_with_the_skipped_page_after_the_render(config, tmp_path, posted):
    _bad_page(config, tmp_path)
    posted.clear()
    result = runner.invoke(app, ["run", "-c", str(config)])
    assert result.exit_code == 0, result.output
    assert posted == ["**HomelabScribe**\nSkipped: services/sonarr.md (not UTF-8, left as it is)"]
    assert str(tmp_path) not in posted[0]
    state = json.loads((tmp_path / "state" / "notify-state.json").read_text())
    assert state["skips"] == ["services/sonarr.md"]
    posted.clear()
    assert runner.invoke(app, ["run", "-c", str(config)]).exit_code == 0
    assert posted == []  # the same file is not sent again


def test_run_sends_a_page_the_html_copy_skipped(config, tmp_path, posted):
    assert runner.invoke(app, ["run", "-c", str(config)]).exit_code == 0
    (tmp_path / "html" / "services" / "sonarr.html").write_text("not made by HomelabScribe\n")
    (tmp_path / "docs" / "index.md").write_text("changed\n")
    posted.clear()
    result = runner.invoke(app, ["run", "-c", str(config)])
    assert result.exit_code == 0, result.output
    assert len(posted) == 1 and "Skipped: services/sonarr.html" in posted[0], result.output
    assert posted[0].count("sonarr") == 1


def test_run_sends_a_skipped_snapshot_found_by_the_scan(config, tmp_path, posted):
    assert runner.invoke(app, ["run", "-c", str(config)]).exit_code == 0
    posted.clear()
    (tmp_path / "state" / "changelog.json").write_text("{not json")
    result = runner.invoke(app, ["run", "-c", str(config)])
    assert result.exit_code == 0, result.output
    assert len(posted) == 1 and posted[0].startswith("**HomelabScribe**\nSkipped: changelog.json (")


def test_run_still_sends_what_the_scan_found_when_the_render_fails(config, tmp_path, posted, monkeypatch):
    from homelabscribe import pipeline

    def boom(cfg, feats, snapshot=None):
        raise RuntimeError("No snapshot yet")

    monkeypatch.setattr(pipeline, "render_pages", boom)
    monkeypatch.setattr(
        docker_collector.DockerCollector, "collect", lambda self: (_ for _ in ()).throw(OSError())
    )
    result = runner.invoke(app, ["run", "-c", str(config)])
    assert result.exit_code == 1
    assert len(posted) == 1 and "Could not read:" in posted[0]


def test_scan_and_render_alone_never_notify(config, tmp_path, posted):
    _bad_page(config, tmp_path)
    posted.clear()
    assert runner.invoke(app, ["scan", "-c", str(config)]).exit_code == 0
    assert runner.invoke(app, ["render", "-c", str(config)]).exit_code == 0
    assert posted == []
