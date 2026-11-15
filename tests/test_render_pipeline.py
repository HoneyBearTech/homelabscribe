import errno
import os
import re
import stat
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from homelabscribe import licensing, notes, pipeline
from homelabscribe import renderer as renderer_module
from homelabscribe.config import Config, DockerHostConfig, ProxmoxConfig
from homelabscribe.demo import demo_snapshots
from homelabscribe.enrich import enrich, load_catalog
from homelabscribe.models import Host, Service, Snapshot
from homelabscribe.renderer import Renderer, slug, tag_text, yaml_text


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


# ---- archiving: a page already in `archive/` is never overwritten ------------------------------


def _with_notes(page, text):
    page.write_text(page.read_text().replace(notes.PLACEHOLDER, text))


def _archive_page(out, snap, folder, name, text):
    """Render the lab (the page comes back with an empty notes block), give the page notes, remove it."""
    Renderer(out).render(snap)
    _with_notes(out / folder / f"{name}.md", text)
    gone = snap.model_copy(deep=True)
    if folder == "services":
        gone.services = [s for s in gone.services if s.name != name]
    else:
        gone.hosts = [h for h in gone.hosts if h.name != name]
    Renderer(out).render(gone)
    assert not (out / folder / f"{name}.md").exists()


@pytest.mark.parametrize(("folder", "name"), [("services", "plex"), ("hosts", "pve1")])
def test_a_page_archived_again_does_not_replace_the_first(tmp_path, folder, name):
    snap = _snap()
    archive = tmp_path / "archive" / folder
    _archive_page(tmp_path, snap, folder, name, "First notes.")
    first = (archive / f"{name}.md").read_bytes()
    _archive_page(tmp_path, snap, folder, name, "Second notes.")
    _archive_page(tmp_path, snap, folder, name, "Third notes.")
    assert (archive / f"{name}.md").read_bytes() == first
    assert "First notes." in first.decode()
    assert "Second notes." in (archive / f"{name}-2.md").read_text()
    assert "Third notes." in (archive / f"{name}-3.md").read_text()
    assert sorted(p.name for p in archive.iterdir()) == [f"{name}-2.md", f"{name}-3.md", f"{name}.md"]


def test_a_name_taken_by_hand_is_skipped(tmp_path):
    archive = tmp_path / "archive" / "services"
    archive.mkdir(parents=True)
    (archive / "plex.md").write_bytes(b"archived before\n")
    (archive / "plex-2.md").write_bytes(b"put here by hand\n")
    _archive_page(tmp_path, _snap(), "services", "plex", "Plex pass renews in May.")
    assert (archive / "plex.md").read_bytes() == b"archived before\n"
    assert (archive / "plex-2.md").read_bytes() == b"put here by hand\n"
    assert "Plex pass renews in May." in (archive / "plex-3.md").read_text()


def test_a_folder_at_the_name_is_left_alone(tmp_path):
    snap = _snap()
    archive = tmp_path / "archive" / "services"
    (archive / "plex.md").mkdir(parents=True)
    _archive_page(tmp_path, snap, "services", "plex", "First notes.")
    assert (archive / "plex.md").is_dir() and not list((archive / "plex.md").iterdir())
    assert "First notes." in (archive / "plex-2.md").read_text()
    _archive_page(tmp_path, snap, "services", "plex", "Second notes.")
    assert (archive / "plex.md").is_dir() and not list((archive / "plex.md").iterdir())
    assert "Second notes." in (archive / "plex-3.md").read_text()


def _outside(tmp_path, out):
    return {
        (p.relative_to(tmp_path).as_posix(), p.read_bytes() if p.is_file() and not p.is_symlink() else None)
        for p in tmp_path.rglob("*")
        if not p.is_relative_to(out)
    }


@pytest.mark.parametrize("kind", ["folder", "file", "nowhere"])
def test_a_link_at_the_name_is_left_alone(tmp_path, kind):
    out = tmp_path / "docs"
    target = tmp_path / "elsewhere"
    if kind == "folder":
        target.mkdir()
    elif kind == "file":
        target.write_bytes(b"someone else's file\n")
    archive = out / "archive" / "services"
    archive.mkdir(parents=True)
    (archive / "plex.md").symlink_to(target, target_is_directory=kind == "folder")
    before = _outside(tmp_path, out)
    _archive_page(out, _snap(), "services", "plex", "Plex pass renews in May.")
    assert (archive / "plex.md").is_symlink() and os.readlink(archive / "plex.md") == str(target)
    assert _outside(tmp_path, out) == before  # the target is unchanged and nothing new is outside `docs`
    if kind == "folder":
        assert not list(target.iterdir())
    elif kind == "file":
        assert target.read_bytes() == b"someone else's file\n"
    else:
        assert not target.exists() and not target.is_symlink()
    copy = archive / "plex-2.md"
    assert not copy.is_symlink() and "Plex pass renews in May." in copy.read_text()


def test_the_archived_page_keeps_its_bytes(tmp_path):
    snap = _snap()
    Renderer(tmp_path).render(snap)
    page = tmp_path / "services" / "plex.md"
    text = page.read_text().replace(notes.PLACEHOLDER, "Café, Ærø, 日本語 — ✓\nSecond line.")
    data = text.replace("\n", "\r\n").encode("utf-8")
    page.write_bytes(data)
    snap.services = [s for s in snap.services if s.name != "plex"]
    Renderer(tmp_path).render(snap)
    assert (tmp_path / "archive" / "services" / "plex.md").read_bytes() == data


def test_a_page_that_is_a_link_is_archived_as_a_copy_and_its_target_is_kept(tmp_path):
    out, mine = tmp_path / "docs", tmp_path / "mine.md"
    snap = _snap()
    Renderer(out).render(snap)
    page = out / "services" / "plex.md"
    mine.write_bytes(page.read_text().replace(notes.PLACEHOLDER, "Plex pass renews in May.").encode())
    data = mine.read_bytes()
    page.unlink()
    page.symlink_to(mine)
    snap.services = [s for s in snap.services if s.name != "plex"]
    Renderer(out).render(snap)
    assert mine.read_bytes() == data  # the file the link pointed to is unchanged
    copy = out / "archive" / "services" / "plex.md"
    assert not copy.is_symlink() and copy.read_bytes() == data
    assert not page.exists() and not page.is_symlink()  # only the link itself is removed


def _ready_to_archive(out):
    """The lab rendered, the Plex page given notes; returns the lab without Plex and the page's bytes."""
    snap = _snap()
    Renderer(out).render(snap)
    page = out / "services" / "plex.md"
    _with_notes(page, "Plex pass renews in May.")
    snap.services = [s for s in snap.services if s.name != "plex"]
    return snap, page.read_bytes()


def test_a_failed_archiving_keeps_the_page_and_leaves_no_partial_file(tmp_path, monkeypatch):
    snap, data = _ready_to_archive(tmp_path)
    real_fill = renderer_module._fill

    def fill_part_way(fd, data):
        real_fill(fd, data[: len(data) // 2])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(renderer_module, "_fill", fill_part_way)
    with pytest.raises(OSError, match="No space left"):
        Renderer(tmp_path).render(snap)
    assert (tmp_path / "services" / "plex.md").read_bytes() == data
    assert not list((tmp_path / "archive" / "services").iterdir())


def test_the_copy_is_on_disk_before_the_page_is_removed(tmp_path, monkeypatch):
    snap, _ = _ready_to_archive(tmp_path)
    events = []
    real_fsync, real_unlink = os.fsync, Path.unlink

    def fsync(fd):
        events.append(("fsync", os.fstat(fd).st_ino))
        real_fsync(fd)

    def unlink(path, missing_ok=False):
        events.append(("unlink", path))
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(Path, "unlink", unlink)
    Renderer(tmp_path).render(snap)
    copy = (tmp_path / "archive" / "services" / "plex.md").stat().st_ino
    assert events.index(("fsync", copy)) < events.index(("unlink", tmp_path / "services" / "plex.md"))


def test_a_failed_flush_to_disk_keeps_the_page_and_leaves_no_file(tmp_path, monkeypatch):
    snap, data = _ready_to_archive(tmp_path)

    def fsync(fd):
        raise OSError(
            errno.EIO, "Input/output error"
        )  # a real failure, unlike a file system that cannot sync

    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(OSError, match="Input/output error"):
        Renderer(tmp_path).render(snap)
    assert (tmp_path / "services" / "plex.md").read_bytes() == data
    assert not list((tmp_path / "archive" / "services").iterdir())


def test_a_file_system_that_cannot_sync_still_archives(tmp_path, monkeypatch):
    snap, data = _ready_to_archive(tmp_path)

    def fsync(fd):
        raise OSError(errno.EINVAL, "Invalid argument")

    monkeypatch.setattr(os, "fsync", fsync)
    Renderer(tmp_path).render(snap)
    assert not (tmp_path / "services" / "plex.md").exists()
    assert (tmp_path / "archive" / "services" / "plex.md").read_bytes() == data


def test_a_page_saved_while_it_is_copied_is_kept_for_the_next_run(tmp_path, monkeypatch):
    snap, _ = _ready_to_archive(tmp_path)
    page = tmp_path / "services" / "plex.md"
    newer = page.read_text().replace("Plex pass renews in May.", "Plex pass renews in June, after the move.")
    real_fill = renderer_module._fill

    def fill_then_save_the_page(fd, data):
        real_fill(fd, data)
        page.write_text(newer)  # the user (or a sync tool) saves the page while it is being archived

    monkeypatch.setattr(renderer_module, "_fill", fill_then_save_the_page)
    renderer = Renderer(tmp_path)
    renderer.render(snap)
    assert page.read_text() == newer
    assert not list((tmp_path / "archive" / "services").iterdir())
    assert page not in renderer.written
    monkeypatch.undo()
    Renderer(tmp_path).render(snap)
    assert not page.exists()
    assert (tmp_path / "archive" / "services" / "plex.md").read_text() == newer


def test_the_clean_up_never_removes_a_file_that_took_the_copys_place(tmp_path, monkeypatch):
    snap, data = _ready_to_archive(tmp_path)
    name = tmp_path / "archive" / "services" / "plex.md"

    def swap_then_fail(fd, data):
        name.unlink()  # while the copy is still open, so the other file cannot get its inode number
        name.write_bytes(b"someone else's file\n")  # another file takes the name before the clean-up
        os.close(fd)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(renderer_module, "_fill", swap_then_fail)
    with pytest.raises(OSError, match="No space left"):
        Renderer(tmp_path).render(snap)
    assert name.read_bytes() == b"someone else's file\n"
    assert (tmp_path / "services" / "plex.md").read_bytes() == data


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="root can write to a read-only folder")
@pytest.mark.parametrize("read_only", ["archive/services", "services"])
def test_a_read_only_folder_keeps_the_page_and_leaves_no_file(tmp_path, read_only):
    snap, data = _ready_to_archive(tmp_path)
    (tmp_path / "archive" / "services").mkdir(parents=True)
    (tmp_path / read_only).chmod(0o555)
    try:
        with pytest.raises(PermissionError):
            Renderer(tmp_path).render(snap)
    finally:
        (tmp_path / read_only).chmod(0o755)
    assert (tmp_path / "services" / "plex.md").read_bytes() == data
    assert not list((tmp_path / "archive" / "services").iterdir())


def test_the_archived_copy_is_never_more_readable_than_the_page(tmp_path):
    snap = _snap()
    Renderer(tmp_path).render(snap)
    for name, text in (("plex", "Private notes."), ("sonarr", "Shared notes.")):
        _with_notes(tmp_path / "services" / f"{name}.md", text)
    (tmp_path / "services" / "plex.md").chmod(0o600)
    (tmp_path / "services" / "sonarr.md").chmod(0o644)
    snap.services = [s for s in snap.services if s.name not in ("plex", "sonarr")]
    old = os.umask(0o022)
    try:
        Renderer(tmp_path).render(snap)
    finally:
        os.umask(old)
    archive = tmp_path / "archive" / "services"
    assert stat.S_IMODE((archive / "plex.md").stat().st_mode) == 0o600
    assert stat.S_IMODE((archive / "sonarr.md").stat().st_mode) == 0o644


_ARCHIVED = ["plex.md", "plex-2.md", "plex-3.md", "nova.md", "nova-2.md"]


@settings(max_examples=40, deadline=None)
@given(
    st.dictionaries(st.sampled_from(_ARCHIVED), st.binary(max_size=24), max_size=4),
    st.lists(
        st.tuples(
            st.sampled_from(["plex", "plex-2", "nova"]),
            st.text(st.characters(categories=["L", "N"]), min_size=1, max_size=12),
        ),
        min_size=1,
        max_size=5,
    ),
)
def test_archiving_never_changes_a_file_already_in_the_archive(tmp_path_factory, there, steps):
    out = tmp_path_factory.mktemp("lab")
    archive = out / "archive" / "services"
    archive.mkdir(parents=True)
    for name, data in there.items():
        (archive / name).write_bytes(data)
    (out / "services").mkdir()
    for name, text in steps:
        before = {p.name: p.read_bytes() for p in archive.iterdir()}
        data = f"# {name}\n\n{notes.START}\n{text}\n{notes.END}\n".encode()
        (out / "services" / f"{name}.md").write_bytes(data)
        Renderer(out).render(Snapshot())
        after = {p.name: p.read_bytes() for p in archive.iterdir()}
        assert not (out / "services" / f"{name}.md").exists()
        assert {n: after.get(n) for n in before} == before
        new = set(after) - set(before)
        assert len(new) == 1 and after[new.pop()] == data


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
        assert fm["tags"][1] == tag_text(s.category)
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
        ("M\u00e9dias", "m\u00e9dias"),
        ("a/b", "a/b"),
        ("R_D", "r_d"),
        ("/a b/", "a-b"),
        ("  Home   lab ", "home-lab"),
        ("A, [beta] {x}: #y 'q' \"z\"", "a-beta-x-y-q-z"),
        ("&&&", "item"),
        ("", "item"),
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


@pytest.mark.parametrize(
    "category", [*AWKWARD, "a/b", "R_D", "M\u00e9dias", "---", "/", "ab\u0301c", "\u0663"]
)
def test_a_tag_reads_back_as_the_same_text_inside_the_tags_list(category):
    tag = tag_text(category)
    assert tag and tag == tag.strip("-/") and tag == tag.lower()
    assert yaml.safe_load(f"tags: [homelab, {yaml_text(tag)}]") == {"tags": ["homelab", tag]}


# ---- the Pro changelog page keeps notes like every other page -----------------------------------


def test_notes_on_the_changelog_page_survive_a_new_entry(tmp_path):
    old, new = demo_snapshots()
    cfg = Config(output_dir=tmp_path / "docs", state_dir=tmp_path / "state")
    pipeline.scan(cfg, licensing.PRO_FEATURES, collectors=[("old", FakeCollector(old))])
    pipeline.scan(cfg, licensing.PRO_FEATURES, collectors=[("new", FakeCollector(new))])
    pipeline.render(cfg, licensing.PRO_FEATURES)
    page = tmp_path / "docs" / "CHANGELOG.md"
    _with_notes(page, "The big move to nova was in March.")

    newer = new.model_copy(deep=True)
    newer.services = [s for s in newer.services if s.name != "plex"]
    pipeline.scan(cfg, licensing.PRO_FEATURES, collectors=[("newer", FakeCollector(newer))])
    pipeline.render(cfg, licensing.PRO_FEATURES)
    text = page.read_text()
    assert "The big move to nova was in March." in text
    assert "- **Removed** Plex" in text
    assert text.endswith(f"## Notes\n\n{notes.START}\nThe big move to nova was in March.\n{notes.END}\n")


def test_a_changelog_page_from_before_the_notes_block_is_rewritten_with_it(tmp_path):
    page = tmp_path / "CHANGELOG.md"
    page.write_text("# What changed\n\nNo changes recorded yet. Changes appear after the second scan.\n")
    Renderer(tmp_path, licensing.PRO_FEATURES).render(_snap(), [], None)
    assert page.read_text().endswith(f"## Notes\n\n{notes.START}\n{notes.PLACEHOLDER}\n{notes.END}\n")


@pytest.mark.parametrize(
    "rendered",
    [
        "# Page\n",
        "# Page",
        "",
        f"# Page\n{notes.START}\nrest\n",
        f"# Page\n{notes.END}\nrest\n",
        f"# Page\n{notes.END}\n{notes.START}\n",
    ],
)
def test_merge_keeps_the_notes_when_the_page_has_no_notes_block(rendered):
    previous = f"# Old\n\n## Notes\n\n{notes.START}\nAsk Jess.\n{notes.END}\n"
    out = notes.merge(rendered, previous)
    assert out.startswith(rendered)
    assert notes.extract(out[len(rendered) :]) == "Ask Jess."
    assert out.endswith(f"## Notes\n\n{notes.START}\nAsk Jess.\n{notes.END}\n")
    assert "\n\n\n" not in out and (not rendered or "\n\n## Notes" in out)


@pytest.mark.parametrize(
    "previous",
    [
        None,
        "# Old\n",
        f"{notes.START}\n{notes.PLACEHOLDER}\n{notes.END}\n",
        f"{notes.START}\n\n{notes.END}\n",
    ],
)
def test_merge_without_notes_to_keep_leaves_a_page_without_a_notes_block_as_it_is(previous):
    assert notes.merge("# Page\n", previous) == "# Page\n"


def test_merge_of_a_page_with_a_notes_block_is_unchanged():
    rendered = f"head\n{notes.START}\n{notes.PLACEHOLDER}\n{notes.END}\ntail\n"
    previous = f"old\n{notes.START}\nmine\n{notes.END}\n"
    assert notes.merge(rendered, previous) == f"head\n{notes.START}\nmine\n{notes.END}\ntail\n"
    assert (
        notes.merge(rendered, f"{notes.START}\n{notes.END}") == f"head\n{notes.START}\n\n{notes.END}\ntail\n"
    )
