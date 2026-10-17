import re

from homelabscribe import licensing, notes, pipeline
from homelabscribe.config import Config, DockerHostConfig, ProxmoxConfig
from homelabscribe.demo import demo_snapshots
from homelabscribe.enrich import enrich, load_catalog
from homelabscribe.renderer import Renderer


def _snap():
    _, new = demo_snapshots()
    enrich(new.services, load_catalog())
    return new


def test_free_render_writes_core_pages_only(tmp_path):
    Renderer(tmp_path).render(_snap())
    assert (tmp_path / "index.md").exists()
    assert (tmp_path / "services" / "sonarr.md").exists()
    assert (tmp_path / "hosts" / "nova.md").exists()
    assert not (tmp_path / "CHANGELOG.md").exists()
    assert not (tmp_path / "break-glass.md").exists()


def test_pro_render_writes_pro_pages(tmp_path):
    Renderer(tmp_path, licensing.PRO_FEATURES).render(_snap(), [], None)
    for name in ("CHANGELOG.md", "break-glass.md", "topology.md"):
        assert (tmp_path / name).exists()
    assert "```mermaid" in (tmp_path / "topology.md").read_text()


def test_notes_survive_rerender(tmp_path):
    snap = _snap()
    Renderer(tmp_path).render(snap)
    page = tmp_path / "services" / "sonarr.md"
    text = page.read_text().replace(notes.PLACEHOLDER, "Config lives in /opt/sonarr. Ask Jess.")
    page.write_text(text)

    snap.services[0].status = "exited"  # force a content change elsewhere
    Renderer(tmp_path).render(snap)
    assert "Config lives in /opt/sonarr. Ask Jess." in page.read_text()


def test_unchanged_render_writes_nothing(tmp_path):
    snap = _snap()
    Renderer(tmp_path).render(snap)
    assert Renderer(tmp_path).render(snap) == []


def test_removed_service_with_notes_is_archived(tmp_path):
    snap = _snap()
    Renderer(tmp_path).render(snap)
    page = tmp_path / "services" / "plex.md"
    page.write_text(page.read_text().replace(notes.PLACEHOLDER, "Plex pass renews in May."))
    snap.services = [s for s in snap.services if s.name not in ("plex", "prowlarr")]
    Renderer(tmp_path).render(snap)
    assert not page.exists()
    assert (tmp_path / "archive" / "services" / "plex.md").exists()
    assert not (tmp_path / "services" / "prowlarr.md").exists()  # no notes: deleted


class FakeCollector:
    def __init__(self, snap):
        self.snap = snap

    def collect(self):
        return self.snap.hosts, [s.model_copy(deep=True) for s in self.snap.services]


def test_pipeline_scan_twice_builds_changelog(tmp_path):
    old, new = demo_snapshots()
    cfg = Config(output_dir=tmp_path / "docs", state_dir=tmp_path / "state")
    pipeline.scan(cfg, licensing.PRO_FEATURES, collectors=[("old", FakeCollector(old))])
    result = pipeline.scan(cfg, licensing.PRO_FEATURES, collectors=[("new", FakeCollector(new))])
    assert result.diff and not result.diff.empty
    pipeline.render(cfg, licensing.PRO_FEATURES)
    assert "Overseerr" in (tmp_path / "docs" / "CHANGELOG.md").read_text()


def test_free_tier_limits_sources(tmp_path):
    cfg = Config(
        state_dir=tmp_path,
        docker_hosts=[DockerHostConfig(name="a", url="tcp://127.0.0.1:1"), DockerHostConfig(name="b")],
        proxmox=[ProxmoxConfig(host="pve", user="u@pve", token_name="t", token_value="v")],
    )
    result = pipeline.scan(cfg, frozenset())
    assert any("multiple hosts need Pro" in s for s in result.skipped)
    assert any("Proxmox needs Pro" in s for s in result.skipped)
    assert len(result.errors) == 1  # host "a" unreachable, reported but not fatal


def test_duplicate_catalog_names_on_one_host_get_separate_pages(tmp_path):
    """Regression: sonarr and sonarr4k used to share one page, and the second overwrote the first."""
    from homelabscribe.enrich import enrich, load_catalog
    from homelabscribe.models import Host, Service, Snapshot

    image = "lscr.io/linuxserver/radarr:latest"
    svcs = [
        Service(name="radarr", host="h", kind="container", image=image),
        Service(name="radarr4k", host="h", kind="container", image=image),
    ]
    enrich(svcs, load_catalog())
    Renderer(tmp_path).render(Snapshot(hosts=[Host(name="h", kind="docker")], services=svcs))
    pages = sorted(p.name for p in (tmp_path / "services").glob("*.md"))
    assert len(pages) == 2
    index = (tmp_path / "index.md").read_text()
    linked = re.findall(r"\(services/([^)]+)\)", index)
    assert len(linked) == 2 and len(set(linked)) == 2


def test_link_host_is_used_in_service_addresses(tmp_path):
    from homelabscribe.models import Host, Port, Service, Snapshot

    svc = Service(name="sonarr", host="nova", kind="container", ports=[Port(container=8989, host=8989)])
    snap = Snapshot(hosts=[Host(name="nova", kind="docker", link_host="nova.lan")], services=[svc])
    Renderer(tmp_path).render(snap)
    assert "http://nova.lan:8989" in (tmp_path / "index.md").read_text()
    snap.hosts[0].link_host = None
    Renderer(tmp_path).render(snap)
    assert "http://nova:8989" in (tmp_path / "index.md").read_text()


def test_image_default_env_vars_are_hidden(tmp_path):
    from homelabscribe.models import Host, Service, Snapshot
    from homelabscribe.renderer import config_vars

    assert config_vars(["PATH", "S6_VERBOSITY", "XDG_CONFIG_HOME", "TZ", "PUID", "API_URL"]) == [
        "TZ",
        "PUID",
        "API_URL",
    ]
    svc = Service(name="x", host="h", kind="container", env_names=["PATH", "TZ"])
    Renderer(tmp_path).render(Snapshot(hosts=[Host(name="h", kind="docker")], services=[svc]))
    page = (tmp_path / "services" / "x.md").read_text()
    assert "\n`TZ`\n" in page  # the list holds only TZ; PATH is dropped from it
    assert "`TZ`, `PATH`" not in page and "Image defaults" in page


def test_break_glass_does_not_mistake_a_pihole_exporter_for_the_dns_server(tmp_path):
    from homelabscribe.config import BreakGlassConfig
    from homelabscribe.models import Host, Service, Snapshot

    hosts = [Host(name="a", kind="docker"), Host(name="b", kind="docker")]
    exporter = Service(name="pihole-exporter", host="a", kind="container", image="ekofr/pihole-exporter:v1")
    snap = Snapshot(hosts=hosts, services=[exporter])
    Renderer(tmp_path, licensing.PRO_FEATURES).render(snap, [], BreakGlassConfig())
    assert "look up website names" not in (tmp_path / "break-glass.md").read_text()

    real = Service(name="pihole", host="b", kind="container", image="pihole/pihole:latest")
    snap = Snapshot(hosts=hosts, services=[exporter, real])
    Renderer(tmp_path, licensing.PRO_FEATURES).render(snap, [], BreakGlassConfig())
    text = (tmp_path / "break-glass.md").read_text()
    assert "look up website names" in text and "**b**" in text
