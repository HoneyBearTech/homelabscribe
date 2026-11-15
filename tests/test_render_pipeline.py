import re

import pytest
import yaml

from homelabscribe import licensing, notes, pipeline
from homelabscribe.config import Config, DockerHostConfig, ProxmoxConfig
from homelabscribe.demo import demo_snapshots
from homelabscribe.enrich import enrich, load_catalog
from homelabscribe.models import Host, Service, Snapshot
from homelabscribe.renderer import Renderer, slug


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

    def addresses() -> list[str]:
        return re.findall(r"http://[^\s|]+", (tmp_path / "index.md").read_text())

    Renderer(tmp_path).render(snap)
    assert addresses() == ["http://nova.lan:8989"]
    snap.hosts[0].link_host = None
    Renderer(tmp_path).render(snap)
    assert addresses() == ["http://nova:8989"]


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


# ---- front matter is always valid YAML and always text ------------------------------------------

AWKWARD = [
    "Media: TV",
    "- dash",
    "@home",
    "123",
    "true",
    "null",
    "1.10",
    "#x",
    "[x]",
    "No",
    "~",
    "A [beta]",
    "Dev: tools",
    'say "hi" \\ now',
    "two\nlines",
    "tab\there",
    " padded ",
    "trailing:",
    "k: v # c",
    "Caf\u00e9 \u65e5\u672c\u8a9e",
    "nel\x85 ls\u2028 ps\u2029 bom\ufeff del\x7f",
    "{a: b}",
    "*star",
    "!tag",
    "'single'",
    "",
]


def _front_matter(text: str) -> dict:
    assert text.startswith("---\n")
    head = text.split("\n---\n", 1)[0][4:]
    return yaml.safe_load(head)


def _awkward_lab() -> Snapshot:
    hosts, services, slugs = [], [], set()
    for i, value in enumerate(AWKWARD):
        name = f"h{i}-{value}"
        title = value
        if slug(title) in slugs:  # two titles with the same page name would share one file
            title = f"{value} again"
        slugs.add(slug(title))
        hosts.append(Host(name=name, kind="docker"))
        services.append(
            Service(
                name=f"c{i}",
                display_name=title,
                host=name,
                kind="container",
                status=value or "unknown",
                image=value or None,
                category=value or "Other",
            )
        )
    return Snapshot(hosts=hosts, services=services)


def test_front_matter_of_awkward_values_is_valid_yaml_text(tmp_path):
    snap = _awkward_lab()
    renderer = Renderer(tmp_path)
    renderer.render(snap)
    seen = 0
    for s in snap.services:
        shared = sum(o.title.lower() == s.title.lower() for o in snap.services) > 1
        page = tmp_path / "services" / f"{slug(f'{s.title}-{s.host}' if shared else s.title)}.md"
        text = page.read_text()
        fm = _front_matter(text)
        want = ["title", "host", "category", "status"] + (["image"] if s.image else []) + ["tags"]
        assert list(fm) == want
        assert fm["title"] == s.title and type(fm["title"]) is str
        assert fm["host"] == s.host and fm["category"] == s.category and fm["status"] == s.status
        assert fm.get("image") == s.image
        assert fm["tags"][0] == "homelab" and len(fm["tags"]) == 2
        assert all(type(t) is str for t in fm["tags"])
        assert fm["tags"][1] == slug(s.category)
        assert text.count(notes.START) == 1 and text.rstrip().endswith(notes.END)
        assert text.split("\n---\n", 1)[1].startswith(f"# {s.title}\n")
        seen += 1
    assert seen == len(AWKWARD)
    for h in snap.hosts:
        page = next(p for p in renderer.generated if p.parent.name == "hosts" and p.stem == slug(h.name))
        text = page.read_text()
        fm = _front_matter(text)
        assert list(fm) == ["title", "kind", "tags"]
        assert fm["title"] == h.name and fm["kind"] == "docker" and fm["tags"] == ["homelab", "host"]
        assert text.count(notes.START) == 1 and text.rstrip().endswith(notes.END)


def test_ordinary_values_stay_plain(tmp_path):
    snap = Snapshot(
        hosts=[Host(name="nova", kind="docker")],
        services=[
            Service(
                name="plex",
                host="nova",
                kind="container",
                status="running",
                image="lscr.io/linuxserver/sonarr:latest",
                category="Virtual machines",
            )
        ],
    )
    Renderer(tmp_path).render(snap)
    head = (tmp_path / "services" / "plex.md").read_text().split("\n---\n", 1)[0]
    assert head == (
        "---\ntitle: plex\nhost: nova\ncategory: Virtual machines\nstatus: running\n"
        "image: lscr.io/linuxserver/sonarr:latest\ntags: [homelab, virtual-machines]"
    )


@pytest.mark.parametrize(
    ("category", "tag"),
    [
        ("Dashboards", "dashboards"),
        ("Databases", "databases"),
        ("Development", "development"),
        ("Downloads", "downloads"),
        ("Files", "files"),
        ("Games", "games"),
        ("Home", "home"),
        ("Infrastructure", "infrastructure"),
        ("Media", "media"),
        ("Monitoring", "monitoring"),
        ("Network", "network"),
        ("Photos", "photos"),
        ("Security", "security"),
        ("Other", "other"),
        ("Virtual machines", "virtual-machines"),
        ("Containers (LXC)", "containers-lxc"),
        ("123", '"123"'),
        ("No", '"no"'),
    ],
)
def test_category_tag(tmp_path, category, tag):
    snap = Snapshot(
        hosts=[Host(name="nova", kind="docker")],
        services=[Service(name="x", host="nova", kind="container", category=category)],
    )
    Renderer(tmp_path).render(snap)
    assert f"tags: [homelab, {tag}]\n" in (tmp_path / "services" / "x.md").read_text()


def test_the_allow_list_leaves_only_plain_text_unquoted():
    from homelabscribe.renderer import yaml_text

    samples = [*AWKWARD, "Plex", "Pi-hole", "a b", "x:1", "yes", "YES", "Off", "N", "y", "NULL", "e5", "1e3"]
    samples += [chr(c) for c in range(0x2000)] + [f"a{chr(c)}b" for c in range(0x300)]
    samples += [f"{w}{x}" for w in ("true", "no", "a") for x in ("", " ", ":", " #", ": b", "\n")]
    for text in samples:
        out = yaml_text(text)
        if out == text:
            assert text[0].isalpha()
            assert text.lower() not in {"true", "false", "yes", "no", "on", "off", "y", "n", "null", "~"}
            assert not text.endswith((" ", ":")) and ": " not in text and " #" not in text
            assert yaml.safe_load(f"[{text}]") == [text]
        else:
            assert out.startswith('"') and out.endswith('"') and "\n" not in out
