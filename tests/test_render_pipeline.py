import errno
import hashlib
import os
import re
import stat
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from homelabscribe import fsutil, licensing, notes, pipeline
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


def _plex_archived(data: bytes) -> bytes:
    """The bytes of the demo's Plex page as archived with Plex gone: its two links point at the live pages."""
    for old, new in (
        (b"](../hosts/nova.md)", b"](../../hosts/nova.md)"),
        (b"](overseerr.md)", b"](../../services/overseerr.md)"),
    ):
        assert data.count(old) == 1
        data = data.replace(old, new)
    return data


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
    assert (tmp_path / "archive" / "services" / "plex.md").read_bytes() == _plex_archived(data)


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
    assert not copy.is_symlink() and copy.read_bytes() == _plex_archived(data)
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
    r = Renderer(tmp_path)
    r.render(snap)  # reported as skipped, not raised
    assert r.skipped == ["services/plex.md (could not be archived, left as it is)"]
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
    r = Renderer(tmp_path)
    r.render(snap)
    assert "services/plex.md (could not be archived, left as it is)" in r.skipped  # the page writes fail too
    assert (tmp_path / "services" / "plex.md").read_bytes() == data
    assert not list((tmp_path / "archive" / "services").iterdir())


def test_a_file_system_that_cannot_sync_still_archives(tmp_path, monkeypatch):
    snap, data = _ready_to_archive(tmp_path)

    def fsync(fd):
        raise OSError(errno.EINVAL, "Invalid argument")

    monkeypatch.setattr(os, "fsync", fsync)
    Renderer(tmp_path).render(snap)
    assert not (tmp_path / "services" / "plex.md").exists()
    assert (tmp_path / "archive" / "services" / "plex.md").read_bytes() == _plex_archived(data)


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
    assert (tmp_path / "archive" / "services" / "plex.md").read_bytes() == _plex_archived(newer.encode())


def test_the_clean_up_never_removes_a_file_that_took_the_copys_place(tmp_path, monkeypatch):
    snap, data = _ready_to_archive(tmp_path)
    name = tmp_path / "archive" / "services" / "plex.md"

    def swap_then_fail(fd, data):
        name.unlink()  # while the copy is still open, so the other file cannot get its inode number
        name.write_bytes(b"someone else's file\n")  # another file takes the name before the clean-up
        os.close(fd)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(renderer_module, "_fill", swap_then_fail)
    r = Renderer(tmp_path)
    r.render(snap)
    assert r.skipped == ["services/plex.md (could not be archived, left as it is)"]
    assert name.read_bytes() == b"someone else's file\n"
    assert (tmp_path / "services" / "plex.md").read_bytes() == data


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="root can write to a read-only folder")
@pytest.mark.parametrize("read_only", ["archive/services", "services"])
def test_a_read_only_folder_keeps_the_page_and_leaves_no_file(tmp_path, read_only):
    snap, data = _ready_to_archive(tmp_path)
    (tmp_path / "archive" / "services").mkdir(parents=True)
    (tmp_path / read_only).chmod(0o555)
    try:
        r = Renderer(tmp_path)
        r.render(snap)
        assert "services/plex.md (could not be archived, left as it is)" in r.skipped
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


# ---- archiving: the generated links of an archived page point at where their pages are ---------------


def _linked_lab() -> Snapshot:
    """A Proxmox node `pve` with a VM that is the Docker host `media`, and three services on `media`: Plex
    depends on "A [beta]" (a title with brackets, page `a-beta`) and Overseerr depends on Plex."""

    def on_media(name, title, deps=()):
        return Service(
            name=name,
            host="media",
            kind="container",
            display_name=title,
            compose_project="m",
            compose_service=name,
            depends_on=list(deps),
        )

    return Snapshot(
        hosts=[
            Host(name="pve", kind="proxmox-node"),
            Host(name="media", kind="docker", guest_of="pve/docker-vm"),
        ],
        services=[
            Service(
                name="docker-vm", host="pve", kind="vm", vmid=101, runs_host="media", display_name="Docker VM"
            ),
            on_media("plex", "Plex", ["sonarr"]),
            on_media("sonarr", "A [beta]"),
            on_media("overseerr", "Overseerr", ["plex"]),
        ],
    )


def _remove(out, noted, services=(), hosts=(), before=None, lab=None, renderer=None) -> dict[str, bytes]:
    """Render the lab (`_linked_lab` unless given), give the pages in `noted` notes, call `before`, then
    render the lab without the services and hosts named (with `renderer`, if given). Returns the bytes of
    each page in `noted` just before that second run."""
    lab = lab or _linked_lab()
    Renderer(out).render(lab)
    for rel in noted:
        _with_notes(out / rel, f"Notes on {rel}.")
    if before:
        before()
    data = {rel: (out / rel).read_bytes() for rel in noted}
    lab.services = [s for s in lab.services if s.name not in services]
    lab.hosts = [h for h in lab.hosts if h.name not in hosts]
    (renderer or Renderer(out)).render(lab)
    return data


def _swapped(data: bytes, *swaps: tuple[str, str]) -> bytes:
    """`data` with each text replaced by the other, each found exactly once."""
    for old, new in swaps:
        assert data.count(old.encode()) == 1, old
        data = data.replace(old.encode(), new.encode())
    return data


def _dead_links(out) -> list[str]:
    """Every relative Markdown link in an archived page that points at no file."""
    dead = []
    for page in sorted((out / "archive").rglob("*.md")):
        for target in re.findall(r"\]\(([^)\s]+\.md)\)", page.read_text()):
            if not (page.parent / target).is_file():
                dead.append(f"{page.relative_to(out).as_posix()} -> {target}")
    return dead


def test_an_archived_service_links_to_the_pages_that_stay(tmp_path):
    data = _remove(tmp_path, ["services/plex.md"], services=["plex"])
    assert (tmp_path / "archive" / "services" / "plex.md").read_bytes() == _swapped(
        data["services/plex.md"],
        ("| Runs on | [media](../hosts/media.md) |", "| Runs on | [media](../../hosts/media.md) |"),
        ("- Depends on [A [beta]](a-beta.md)", "- Depends on [A [beta]](../../services/a-beta.md)"),
        ("- Used by [Overseerr](overseerr.md)", "- Used by [Overseerr](../../services/overseerr.md)"),
    )
    assert not _dead_links(tmp_path)


def test_an_archived_host_and_its_services_link_to_each_other_and_to_the_pages_that_stay(tmp_path):
    noted = ["services/plex.md", "hosts/media.md"]  # A [beta] and Overseerr have no notes: deleted
    data = _remove(tmp_path, noted, services=["plex", "sonarr", "overseerr"], hosts=["media"])
    archive = tmp_path / "archive"
    assert (
        not (tmp_path / "services" / "a-beta.md").exists()
        and not (archive / "services" / "a-beta.md").exists()
    )
    assert (archive / "services" / "plex.md").read_bytes() == _swapped(
        data["services/plex.md"],
        ("| Runs on | [media](../hosts/media.md) |", "| Runs on | [media](../../archive/hosts/media.md) |"),
        ("- Depends on [A [beta]](a-beta.md)", "- Depends on A [beta]"),  # gone: its text, as written
        ("- Used by [Overseerr](overseerr.md)", "- Used by Overseerr"),
    )
    assert (archive / "hosts" / "media.md").read_bytes() == _swapped(
        data["hosts/media.md"],
        (
            "| Runs as | VM [Docker VM](../services/docker-vm.md) on [pve](../hosts/pve.md) |",
            "| Runs as | VM [Docker VM](../../services/docker-vm.md) on [pve](../../hosts/pve.md) |",
        ),
        ("| [A [beta]](../services/a-beta.md) | unknown |", "| A [beta] | unknown |"),
        ("| [Overseerr](../services/overseerr.md) | unknown |", "| Overseerr | unknown |"),
        ("| [Plex](../services/plex.md) | unknown |", "| [Plex](../../archive/services/plex.md) | unknown |"),
    )
    assert not _dead_links(tmp_path)


def test_two_services_that_depend_on_each_other_link_to_each_others_archived_copy(tmp_path):
    data = _remove(tmp_path, ["services/plex.md", "services/a-beta.md"], services=["plex", "sonarr"])
    archive = tmp_path / "archive" / "services"
    assert (archive / "plex.md").read_bytes() == _swapped(
        data["services/plex.md"],
        ("](../hosts/media.md)", "](../../hosts/media.md)"),
        ("- Depends on [A [beta]](a-beta.md)", "- Depends on [A [beta]](../../archive/services/a-beta.md)"),
        ("](overseerr.md)", "](../../services/overseerr.md)"),
    )
    assert (archive / "a-beta.md").read_bytes() == _swapped(
        data["services/a-beta.md"],
        ("](../hosts/media.md)", "](../../hosts/media.md)"),
        ("- Used by [Plex](plex.md)", "- Used by [Plex](../../archive/services/plex.md)"),
    )
    assert not _dead_links(tmp_path)


def test_the_docker_host_and_the_vm_it_runs_as_link_to_each_other(tmp_path):
    vm, media = "services/docker-vm.md", "hosts/media.md"
    data = _remove(
        tmp_path, [vm, media], services=["docker-vm", "plex", "sonarr", "overseerr"], hosts=["media"]
    )
    archive = tmp_path / "archive"
    assert (archive / vm).read_bytes() == _swapped(
        data[vm],
        ("| Runs on | [pve](../hosts/pve.md) |", "| Runs on | [pve](../../hosts/pve.md) |"),
        (
            "| Is the Docker host | [media](../hosts/media.md) |",
            "| Is the Docker host | [media](../../archive/hosts/media.md) |",
        ),
    )
    assert (archive / media).read_bytes() == _swapped(
        data[media],
        (
            "| Runs as | VM [Docker VM](../services/docker-vm.md) on [pve](../hosts/pve.md) |",
            "| Runs as | VM [Docker VM](../../archive/services/docker-vm.md) on [pve](../../hosts/pve.md) |",
        ),
        ("| [A [beta]](../services/a-beta.md) |", "| A [beta] |"),
        ("| [Overseerr](../services/overseerr.md) |", "| Overseerr |"),
        ("| [Plex](../services/plex.md) |", "| Plex |"),
    )
    assert not _dead_links(tmp_path)


def test_a_vm_archived_while_its_docker_host_stays_links_to_the_host(tmp_path):
    data = _remove(tmp_path, ["services/docker-vm.md"], services=["docker-vm"])
    assert (tmp_path / "archive" / "services" / "docker-vm.md").read_bytes() == _swapped(
        data["services/docker-vm.md"],
        ("](../hosts/pve.md)", "](../../hosts/pve.md)"),
        (
            "| Is the Docker host | [media](../hosts/media.md) |",
            "| Is the Docker host | [media](../../hosts/media.md) |",
        ),
    )


def test_a_page_archived_under_another_name_is_linked_by_that_name(tmp_path):
    archive = tmp_path / "archive"
    taken = {"services/a-beta.md": b"archived before\n", "hosts/media.md": b"put here by hand\n"}

    def take_the_names():
        for rel, text in taken.items():
            (archive / rel).parent.mkdir(parents=True, exist_ok=True)
            (archive / rel).write_bytes(text)

    noted = ["services/plex.md", "services/a-beta.md", "hosts/media.md"]
    data = _remove(tmp_path, noted, ["plex", "sonarr", "overseerr"], ["media"], before=take_the_names)
    assert {rel: (archive / rel).read_bytes() for rel in taken} == taken
    assert (archive / "services" / "plex.md").read_bytes() == _swapped(
        data["services/plex.md"],
        ("](../hosts/media.md)", "](../../archive/hosts/media-2.md)"),
        ("- Depends on [A [beta]](a-beta.md)", "- Depends on [A [beta]](../../archive/services/a-beta-2.md)"),
        ("- Used by [Overseerr](overseerr.md)", "- Used by Overseerr"),
    )
    assert (archive / "hosts" / "media-2.md").read_bytes() == _swapped(
        data["hosts/media.md"],
        (
            "](../services/docker-vm.md) on [pve](../hosts/pve.md)",
            "](../../services/docker-vm.md) on [pve](../../hosts/pve.md)",
        ),
        ("| [A [beta]](../services/a-beta.md) |", "| [A [beta]](../../archive/services/a-beta-2.md) |"),
        ("| [Overseerr](../services/overseerr.md) |", "| Overseerr |"),
        ("| [Plex](../services/plex.md) |", "| [Plex](../../archive/services/plex.md) |"),
    )
    assert (archive / "services" / "a-beta-2.md").read_bytes() == _swapped(
        data["services/a-beta.md"],
        ("](../hosts/media.md)", "](../../archive/hosts/media-2.md)"),
        ("](plex.md)", "](../../archive/services/plex.md)"),
    )


def test_a_link_to_a_page_that_is_not_there_becomes_its_text(tmp_path):
    def lose_pages():
        (tmp_path / "services" / "overseerr.md").unlink()  # removed by hand before the run
        (tmp_path / "hosts" / "media.md").unlink()
        (tmp_path / "hosts" / "media.md").mkdir()  # written in the run, but a folder is there: left alone

    data = _remove(tmp_path, ["services/plex.md"], services=["plex", "overseerr"], before=lose_pages)
    assert (tmp_path / "hosts" / "media.md").is_dir()
    assert (tmp_path / "archive" / "services" / "plex.md").read_bytes() == _swapped(
        data["services/plex.md"],
        ("| Runs on | [media](../hosts/media.md) |", "| Runs on | media |"),
        ("](a-beta.md)", "](../../services/a-beta.md)"),
        ("- Used by [Overseerr](overseerr.md)", "- Used by Overseerr"),
    )


def test_links_in_the_notes_and_the_rest_of_the_page_are_kept_with_crlf_and_any_letters(tmp_path):
    mine = (
        "Café, Ærø, 日本語 — ✓\n- Depends on [A [beta]](a-beta.md)\n"
        "| Runs on | [media](../hosts/media.md) |\n"
        "See [the host](../hosts/media.md) and [Overseerr](overseerr.md)."
    )

    def notes_with_links_and_crlf():
        page = tmp_path / "services" / "plex.md"
        text = page.read_text().replace("Notes on services/plex.md.", mine)
        page.write_bytes(text.replace("\n", "\r\n").encode())

    data = _remove(tmp_path, ["services/plex.md"], services=["plex"], before=notes_with_links_and_crlf)
    copy = (tmp_path / "archive" / "services" / "plex.md").read_bytes()
    head, start, tail = data["services/plex.md"].partition(notes.START.encode())
    assert (
        copy
        == _swapped(
            head,
            (
                "| Runs on | [media](../hosts/media.md) |\r\n",
                "| Runs on | [media](../../hosts/media.md) |\r\n",
            ),
            (
                "- Depends on [A [beta]](a-beta.md)\r\n",
                "- Depends on [A [beta]](../../services/a-beta.md)\r\n",
            ),
            (
                "- Used by [Overseerr](overseerr.md)\r\n",
                "- Used by [Overseerr](../../services/overseerr.md)\r\n",
            ),
        )
        + start
        + tail
    )
    assert mine.replace("\n", "\r\n").encode() in tail  # the notes, links and all, byte for byte
    assert copy.count(b"\r\n") == data["services/plex.md"].count(b"\r\n") and b"\n" not in copy.replace(
        b"\r\n", b""
    )


def _titled(title: str) -> Snapshot:
    """The linked lab with another title for the service "A [beta]"."""
    lab = _linked_lab()
    next(s for s in lab.services if s.name == "sonarr").display_name = title
    return lab


def test_a_title_with_an_escaped_bracket_is_read_as_markdown_reads_it(tmp_path):
    title = "A \\](beta)"  # `A \](beta)`: the bracket is part of the text, as Markdown reads it; page a-beta
    data = _remove(
        tmp_path,
        ["services/plex.md", "hosts/media.md"],
        ["plex", "sonarr", "overseerr"],
        ["media"],
        lab=_titled(title),
    )
    archive = tmp_path / "archive"
    assert (archive / "services" / "plex.md").read_bytes() == _swapped(
        data["services/plex.md"],
        ("](../hosts/media.md)", "](../../archive/hosts/media.md)"),
        (f"- Depends on [{title}](a-beta.md)\n", f"- Depends on {title}\n"),
        ("- Used by [Overseerr](overseerr.md)", "- Used by Overseerr"),
    )
    assert (archive / "hosts" / "media.md").read_bytes() == _swapped(
        data["hosts/media.md"],
        (
            "](../services/docker-vm.md) on [pve](../hosts/pve.md)",
            "](../../services/docker-vm.md) on [pve](../../hosts/pve.md)",
        ),
        (f"| [{title}](../services/a-beta.md) |", f"| {title} |"),
        ("| [Overseerr](../services/overseerr.md) |", "| Overseerr |"),
        ("| [Plex](../services/plex.md) |", "| [Plex](../../archive/services/plex.md) |"),
    )


@pytest.mark.parametrize("title", ["A | b", "A\nb"])
def test_a_line_whose_link_text_is_not_one_piece_is_left_as_written(tmp_path, title):
    noted = ["services/plex.md", "hosts/media.md"]
    data = _remove(tmp_path, noted, ["plex", "sonarr", "overseerr"], ["media"], lab=_titled(title))
    plex = (tmp_path / "archive" / "services" / "plex.md").read_bytes()
    media = (tmp_path / "archive" / "hosts" / "media.md").read_bytes()
    assert b"](../../archive/hosts/media.md)" in plex and b"- Used by Overseerr\n" in plex
    assert b"| [Plex](../../archive/services/plex.md) |" in media and b"| Overseerr |" in media
    row = f"| [{title}](../services/a-b.md) |".encode()
    assert row in data["hosts/media.md"] and row in media  # a `|` or a line break in a table row: as written
    item = f"- Depends on [{title}](a-b.md)\n".encode()
    assert item in data["services/plex.md"]
    if "\n" in title:
        assert item in plex  # a link over two lines: as written
    else:
        assert f"- Depends on {title}\n".encode() in plex  # a `|` outside a table is just text


def test_generated_looking_lines_outside_their_place_or_in_a_code_block_are_left_alone(tmp_path):
    example = "```\n| | |\n| --- | --- |\n| Runs on | [media](../hosts/media.md) |\n```"
    proxmox_notes = (
        f"Set up like this:\n\n{example}\n\n"
        "| | |\n| --- | --- |\n| Runs on | [media](../hosts/media.md) |\n\n"
        "## Connections\n\n- Depends on [Plex](plex.md)"
    )
    lab = _linked_lab()
    vm = next(s for s in lab.services if s.name == "docker-vm")
    vm.description, vm.notes, vm.depends_on = f"A fenced example:\n{example}", proxmox_notes, []
    data = _remove(tmp_path, ["services/docker-vm.md"], ["docker-vm"], lab=lab)
    before = data["services/docker-vm.md"]
    assert before.count(b"| Runs on | [media](../hosts/media.md) |") == 3
    assert (tmp_path / "archive" / "services" / "docker-vm.md").read_bytes() == _swapped(
        before,
        ("| Runs on | [pve](../hosts/pve.md) |", "| Runs on | [pve](../../hosts/pve.md) |"),
        (
            "| Is the Docker host | [media](../hosts/media.md) |",
            "| Is the Docker host | [media](../../hosts/media.md) |",
        ),
    )


@pytest.mark.parametrize("problem", ["not UTF-8", "unreadable", "read-only and changed"])
def test_a_link_to_a_page_skipped_in_the_run_becomes_its_text(tmp_path, problem):
    if problem != "not UTF-8" and (os.name != "posix" or os.geteuid() == 0):
        pytest.skip("root can read and write any file")
    a_beta = tmp_path / "services" / "a-beta.md"
    lab = _linked_lab()

    def spoil_a_beta():
        if problem == "not UTF-8":
            a_beta.write_bytes(a_beta.read_text().replace("Runs on", "Läuft auf").encode("latin-1"))
        elif problem == "unreadable":
            a_beta.chmod(0o000)
        else:
            a_beta.chmod(0o444)
            next(s for s in lab.services if s.name == "sonarr").status = "running"  # its page has to change

    r = Renderer(tmp_path)
    try:
        data = _remove(tmp_path, ["services/plex.md"], ["plex"], before=spoil_a_beta, lab=lab, renderer=r)
    finally:
        a_beta.chmod(0o644)
    reason = {"not UTF-8": "not UTF-8", "unreadable": "could not be read"}.get(
        problem, "could not be written"
    )
    assert r.skipped == [f"services/a-beta.md ({reason}, left as it is)"]
    assert (tmp_path / "archive" / "services" / "plex.md").read_bytes() == _swapped(
        data["services/plex.md"],
        ("](../hosts/media.md)", "](../../hosts/media.md)"),
        (
            "- Depends on [A [beta]](a-beta.md)\n",
            "- Depends on A [beta]\n",
        ),  # skipped: not a page of this run
        ("](overseerr.md)", "](../../services/overseerr.md)"),
    )


def _plex_and_a_beta_with_notes(out) -> Snapshot:
    """The linked lab rendered, Plex's and A [beta]'s pages given notes; returns the lab without them."""
    lab = _linked_lab()
    Renderer(out).render(lab)
    for name in ("plex", "a-beta"):
        _with_notes(out / "services" / f"{name}.md", f"Notes on {name}.")
    lab.services = [s for s in lab.services if s.name not in ("plex", "sonarr")]
    return lab


def _on_fill(monkeypatch, when: bytes, then):
    """Make `_fill` call `then(fd, data)` instead for the copy whose bytes hold `when`."""
    real_fill = renderer_module._fill

    def fill(fd, data):
        if when in data:
            then(fd, data)
        else:
            real_fill(fd, data)

    monkeypatch.setattr(renderer_module, "_fill", fill)


def _no_space(fd, data):
    os.close(fd)
    raise OSError(28, "No space left on device")


@pytest.mark.parametrize(
    ("failing", "linked_from", "line"),
    [
        ("a-beta", "plex", "- Depends on A [beta]\n"),  # copied before the page that links to it
        ("plex", "a-beta", "- Used by Plex\n"),  # copied after it: that copy is written again
    ],
)
def test_a_page_whose_copy_fails_is_not_linked_to(tmp_path, monkeypatch, failing, linked_from, line):
    lab = _plex_and_a_beta_with_notes(tmp_path)
    archive = tmp_path / "archive" / "services"
    _on_fill(monkeypatch, f"Notes on {failing}.".encode(), _no_space)
    r = Renderer(tmp_path)
    r.render(lab)
    assert r.skipped == [f"services/{failing}.md (could not be archived, left as it is)"]
    assert (tmp_path / "services" / f"{failing}.md").exists() and not (archive / f"{failing}.md").exists()
    assert (
        line in (archive / f"{linked_from}.md").read_text()
    )  # its text: no link to a copy that is not there
    assert sorted(p.name for p in archive.iterdir()) == [f"{linked_from}.md"]  # no temporary file left
    assert not _dead_links(tmp_path)


def test_a_page_saved_while_it_is_copied_is_not_linked_to(tmp_path, monkeypatch):
    lab = _plex_and_a_beta_with_notes(tmp_path)
    plex = tmp_path / "services" / "plex.md"
    real_fill = renderer_module._fill

    def fill_then_save(fd, data):
        real_fill(fd, data)
        plex.write_text(plex.read_text().replace("Notes on plex.", "Newer notes on plex."))

    _on_fill(monkeypatch, b"Notes on plex.", fill_then_save)
    Renderer(tmp_path).render(lab)
    archive = tmp_path / "archive" / "services"
    assert "Newer notes on plex." in plex.read_text() and not (archive / "plex.md").exists()
    assert "- Used by Plex\n" in (archive / "a-beta.md").read_text()
    assert not _dead_links(tmp_path)


def test_a_page_that_gets_another_name_than_expected_is_linked_by_that_name(tmp_path, monkeypatch):
    lab = _plex_and_a_beta_with_notes(tmp_path)
    archive = tmp_path / "archive" / "services"
    real_fill = renderer_module._fill

    def fill_then_take_plexs_name(fd, data):
        real_fill(fd, data)
        (archive / "plex.md").write_bytes(b"put here by hand\n")  # while A [beta] is archived

    _on_fill(monkeypatch, b"Notes on a-beta.", fill_then_take_plexs_name)
    Renderer(tmp_path).render(lab)
    assert (archive / "plex.md").read_bytes() == b"put here by hand\n"
    assert "Notes on plex." in (archive / "plex-2.md").read_text()
    assert "- Used by [Plex](../../archive/services/plex-2.md)\n" in (archive / "a-beta.md").read_text()
    assert sorted(p.name for p in archive.iterdir()) == ["a-beta.md", "plex-2.md", "plex.md"]
    assert not _dead_links(tmp_path)


@pytest.mark.parametrize("change", ["edited", "replaced", "removed"])
def test_a_copy_changed_before_its_links_are_fixed_is_left_alone(tmp_path, monkeypatch, change):
    lab = _plex_and_a_beta_with_notes(tmp_path)
    copy = tmp_path / "archive" / "services" / "a-beta.md"
    mine = b"the user's own text\n"

    def change_the_copy_and_fail(fd, data):
        if change == "edited":
            copy.write_bytes(mine)  # the same file, other bytes
        elif change == "replaced":
            copy.unlink()
            copy.write_bytes(mine)  # another file under the name
        else:
            copy.unlink()
        _no_space(fd, data)

    _on_fill(monkeypatch, b"Notes on plex.", change_the_copy_and_fail)
    Renderer(tmp_path).render(lab)
    if change == "removed":
        assert not copy.exists()
    else:
        assert copy.read_bytes() == mine
    assert sorted(p.name for p in copy.parent.iterdir()) == ([] if change == "removed" else ["a-beta.md"])


def test_a_copy_changed_while_its_new_version_is_written_is_left_alone(tmp_path, monkeypatch):
    lab = _plex_and_a_beta_with_notes(tmp_path)
    copy = tmp_path / "archive" / "services" / "a-beta.md"
    _on_fill(monkeypatch, b"Notes on plex.", _no_space)
    checks = []
    real_is_own = renderer_module._is_own

    def is_own_until_written(c):
        checks.append(c.dest)
        return real_is_own(c) and len(checks) == 1  # changed after the first check, before the swap

    monkeypatch.setattr(renderer_module, "_is_own", is_own_until_written)
    before = None

    def remember(self, *args):
        nonlocal before
        before = copy.read_bytes()
        real_fix(self, *args)

    real_fix = Renderer._fix_copies
    monkeypatch.setattr(Renderer, "_fix_copies", remember)
    Renderer(tmp_path).render(lab)
    assert checks == [copy, copy]
    assert copy.read_bytes() == before  # not replaced
    assert sorted(p.name for p in copy.parent.iterdir()) == ["a-beta.md"]  # no temporary file left


def test_a_copy_whose_links_cannot_be_fixed_is_kept_and_reported(tmp_path, monkeypatch):
    lab = _plex_and_a_beta_with_notes(tmp_path)
    copy = tmp_path / "archive" / "services" / "a-beta.md"
    _on_fill(monkeypatch, b"Notes on plex.", _no_space)
    real_fix = Renderer._fix_copies

    def fix_copies_on_a_full_disk(self, *args):
        before = copy.read_bytes()
        monkeypatch.setattr(os, "replace", lambda src, dst: _no_space(os.open(os.devnull, os.O_RDONLY), b""))
        real_fix(self, *args)
        monkeypatch.undo()
        assert copy.read_bytes() == before  # the copy, notes and all, as it was

    monkeypatch.setattr(Renderer, "_fix_copies", fix_copies_on_a_full_disk)
    r = Renderer(tmp_path)
    r.render(lab)
    assert r.skipped == [
        "services/plex.md (could not be archived, left as it is)",
        "archive/services/a-beta.md (its links could not be updated, left as it is)",
    ]
    assert "Notes on a-beta." in copy.read_text()
    assert sorted(p.name for p in copy.parent.iterdir()) == ["a-beta.md"]  # no temporary file left


def test_a_page_without_notes_given_notes_while_other_pages_are_read_is_kept(tmp_path, monkeypatch):
    lab = _plex_and_a_beta_with_notes(tmp_path)
    a_beta = tmp_path / "services" / "a-beta.md"
    a_beta.write_text(a_beta.read_text().replace("Notes on a-beta.", notes.PLACEHOLDER))  # no notes: deleted
    real_read = fsutil.read_text_version

    def read(path):
        if path.name == "plex.md":  # read after A [beta]: the user saves notes in A [beta] meanwhile
            _with_notes(a_beta, "Saved just now.")
        return real_read(path)

    monkeypatch.setattr(fsutil, "read_text_version", read)
    r = Renderer(tmp_path)
    r.render(lab)
    assert "Saved just now." in a_beta.read_text()  # kept, for the next run
    assert a_beta not in r.written
    assert "- Depends on A [beta]\n" in (tmp_path / "archive" / "services" / "plex.md").read_text()
    monkeypatch.undo()
    Renderer(tmp_path).render(lab)
    assert (
        not a_beta.exists()
        and "Saved just now." in (tmp_path / "archive" / "services" / "a-beta.md").read_text()
    )


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="counts open files in /proc/self/fd")
def test_archiving_many_pages_keeps_few_files_open(tmp_path):
    import resource

    lab = Snapshot(
        hosts=[Host(name="h", kind="docker")],
        services=[Service(name=f"app{i:02}", host="h", kind="container") for i in range(80)],
    )
    Renderer(tmp_path).render(lab)
    for page in (tmp_path / "services").glob("*.md"):
        _with_notes(page, "Kept.")
    lab.services = []
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (len(os.listdir("/proc/self/fd")) + 16, hard))
    try:
        r = Renderer(tmp_path)
        r.render(lab)
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
    assert r.skipped == []
    assert len(list((tmp_path / "archive" / "services").glob("*.md"))) == 80
    assert not list((tmp_path / "services").glob("*.md"))


def test_an_error_that_ends_the_run_leaves_no_empty_copy(tmp_path, monkeypatch):
    def interrupted(fd, data):
        os.close(fd)
        raise KeyboardInterrupt

    lab = _linked_lab()
    Renderer(tmp_path).render(lab)
    for name in ("plex", "a-beta"):
        _with_notes(tmp_path / "services" / f"{name}.md", f"Notes on {name}.")
    lab.services = [s for s in lab.services if s.name not in ("plex", "sonarr")]
    monkeypatch.setattr(renderer_module, "_fill", interrupted)
    with pytest.raises(KeyboardInterrupt):
        Renderer(tmp_path).render(lab)
    assert not list((tmp_path / "archive" / "services").iterdir())  # neither the one copied nor the next
    assert (tmp_path / "services" / "plex.md").exists() and (tmp_path / "services" / "a-beta.md").exists()


def test_a_page_that_is_no_longer_utf8_when_it_is_copied_is_copied_as_it_is(tmp_path, monkeypatch):
    def save_as_latin1():  # as if saved in another encoding after the old-page clean-up read it as UTF-8
        page = tmp_path / "services" / "plex.md"
        page.write_bytes(page.read_text().replace("Notes on services/plex.md.", "Café").encode("latin-1"))
        monkeypatch.setattr(
            fsutil, "read_text_version", lambda path: (path.read_bytes().decode("latin-1"), os.stat(path))
        )

    data = _remove(tmp_path, ["services/plex.md"], services=["plex"], before=save_as_latin1)
    assert b"Caf\xe9" in data["services/plex.md"]
    assert (tmp_path / "archive" / "services" / "plex.md").read_bytes() == data["services/plex.md"]


def test_the_demo_lab_archived_has_no_dead_links(tmp_path):
    snap = _snap()
    Renderer(tmp_path).render(snap)
    pages = [*(tmp_path / "services").glob("*.md"), *(tmp_path / "hosts").glob("*.md")]
    for page in pages:
        _with_notes(page, "Kept.")
    gone = snap.model_copy(deep=True)
    gone.services = [s for s in gone.services if s.host != "nova"]  # every service on nova, and host pve1
    gone.hosts = [h for h in gone.hosts if h.name != "pve1"]
    Renderer(tmp_path).render(gone)
    archived = [p.read_bytes() for p in (tmp_path / "archive").rglob("*.md")]
    assert len(archived) > 5
    for kind in (b"](../../archive/services/", b"](../../hosts/", b"](../../services/"):
        assert any(kind in data for data in archived), kind
    assert not _dead_links(tmp_path)


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
    page = rendered.replace(notes.START, "").replace(notes.END, "")  # stray markers are removed
    assert out.startswith(page)
    assert notes.extract(out[len(page) :]) == "Ask Jess."
    assert out.endswith(f"## Notes\n\n{notes.START}\nAsk Jess.\n{notes.END}\n")
    assert out.count(notes.START) == out.count(notes.END) == 1
    assert not page or "\n\n## Notes" in out
    if page == rendered:
        assert "\n\n\n" not in out


STRAY = [
    f"# Page\n{notes.START}\nrest\n",  # a start marker only
    f"# Page\n{notes.END}\nrest\n",  # an end marker only
    f"# Page\n{notes.END}\nmiddle\n{notes.START}\nrest\n",  # the end before the start
]


@pytest.mark.parametrize("rendered", STRAY)
def test_a_page_with_a_stray_marker_does_not_grow_when_it_is_merged_again(rendered):
    previous = f"# Old\n\n## Notes\n\n{notes.START}\nAsk Jess.\n{notes.END}\n"
    once = notes.merge(rendered, previous)
    assert notes.merge(rendered, once) == once
    assert notes.extract(once) == "Ask Jess." and once.count("## Notes") == 1
    assert notes.merge(rendered, None) == notes.merge(rendered, notes.merge(rendered, None))


def test_removing_markers_also_removes_one_that_a_removal_brings_together():
    nested = f"a<!-- homelabscribe:{notes.START}notes:start -->b"
    out = notes.merge(nested, f"{notes.START}\nmine\n{notes.END}")
    assert out.count(notes.START) == 1 and out.startswith("ab\n\n## Notes")


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


# ---- page names are never too long for a file name ----------------------------------------------


def _old_slug(text: str) -> str:
    """The rule before page names had a maximum length."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "item"


def _links_resolve(out: Path) -> int:
    """Every link from a page to another page points at a file. Returns how many links were checked."""
    count = 0
    for page in out.rglob("*.md"):
        for target in re.findall(r"\]\(([^)#:\s]+\.md)\)", page.read_text()):
            assert (page.parent / target).is_file(), (page, target)
            count += 1
    return count


@pytest.mark.parametrize("length", [253, 300, 3000])
def test_very_long_titles_and_host_names_render_and_links_point_at_the_files(tmp_path, length):
    title, host = ("Media server " * 300)[: length - 1] + "X", ("node-" * 700)[: length - 1] + "x"
    assert len(_old_slug(title)) == len(_old_slug(host)) == length  # too long for a file name before
    snap = Snapshot(
        hosts=[Host(name=host, kind="docker"), Host(name="nova", kind="docker")],
        services=[
            Service(name="a", host=host, kind="container", display_name=title),
            Service(name="b", host="nova", kind="container", display_name=title),  # same title, two hosts
            Service(name="c", host=host, kind="container"),
        ],
    )
    Renderer(tmp_path, licensing.PRO_FEATURES).render(snap, [], None)
    names = [p.name for p in tmp_path.rglob("*.md")]
    assert all(len(n) <= 203 for n in names)
    assert len(list((tmp_path / "services").glob("*.md"))) == 3
    assert (tmp_path / "hosts" / f"{slug(host)}.md").is_file()
    assert _links_resolve(tmp_path) >= 7  # index → 3 services and 2 hosts, each service → its host
    assert Renderer(tmp_path, licensing.PRO_FEATURES).render(snap, [], None) == []  # the same names again


def test_a_name_of_200_characters_is_kept_and_one_of_252_is_cut():
    assert slug("a" * 200) == "a" * 200
    cut = slug("b" * 252)
    assert cut == "b" * 191 + "-" + hashlib.sha256(("b" * 252).encode()).hexdigest()[:8]
    assert len(cut) == 200


def test_a_cut_name_does_not_end_the_cut_with_a_hyphen():
    name = "a" * 190 + " b" + "c" * 100  # the cut falls just after the hyphen
    assert slug(name).startswith("a" * 190 + "-") and "--" not in slug(name)
    assert len(slug(name)) == 199


def test_long_names_that_start_the_same_get_different_page_names():
    first, second = "x" * 191 + "-one" + "y" * 20, "x" * 191 + "-two" + "y" * 20
    assert slug(first) != slug(second)
    assert slug(first)[:191] == slug(second)[:191]


@settings(max_examples=200, deadline=None)
@given(
    st.text(max_size=600)
    | st.lists(st.sampled_from(["a", "Z", "9", "-", " ", ".", "\u00e9"]), max_size=500).map("".join)
    | st.text(alphabet="az09 -.", min_size=190, max_size=700)  # often longer than 200 as a page name
)
def test_slug_is_a_short_safe_page_name(text):
    s = slug(text)
    assert 0 < len(s) <= 200 and re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", s)
    assert slug(text) == s
    if len(_old_slug(text)) <= 200:
        assert s == _old_slug(text)


# ---- text that no file can hold (a lone surrogate) never stops a scan or a render -----------------

BAD = "bad\ud800text"
_TEXT_TYPES = (str, str | None, list[str], dict[str, str])


def _all_text(model_cls, **given):
    """A model with every text field (str, list of str, dict of str) holding a lone surrogate."""
    values = {}
    for name, field in model_cls.model_fields.items():
        if field.annotation in (str, str | None):
            values[name] = f"{name}-{BAD}"
        elif field.annotation == list[str]:
            values[name] = [f"{name}-{BAD}"]
        elif field.annotation == dict[str, str]:
            values[name] = {f"{name}-key-{BAD}": f"{name}-{BAD}"}
    return model_cls(**{**values, **given})


def _surrogate_lab() -> tuple[list[Host], list[Service], list]:
    from homelabscribe.models import Mount, Port, Route

    host = _all_text(Host, kind="docker")
    web = _all_text(
        Service,
        host=host.name,
        kind="container",
        ports=[_all_text(Port, container=80, host=8080)],
        mounts=[_all_text(Mount)],
        vmid=None,
    )
    # The VM that is the Docker host.
    guest = _all_text(Service, name=host.name, host=host.name, kind="vm", display_name=f"VM {BAD}")
    route = _all_text(Route, forward_host=host.name, forward_port=8080)
    return [host], [web, guest], [route]


def _texts(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [t for v in value for t in _texts(v)]
    if isinstance(value, dict):
        return [t for k, v in value.items() for t in [*_texts(k), *_texts(v)]]
    return []


_EXPECTED_FIELDS = {
    Host: {"name", "address", "link_host", "os", "version"},
    Service: {"name", "host", "status", "image", "networks", "env_names", "labels", "restart_policy"}
    | {"compose_project", "compose_service", "aliases", "depends_on", "tags", "notes", "restart_hint"}
    | {"display_name", "description", "category", "url"},
}  # `guest_of` and `runs_host` are set by the scan: the name and id of the Docker host and its VM


def test_lone_surrogates_in_every_text_of_a_scan_are_saved_compared_and_rendered(tmp_path, monkeypatch):
    from homelabscribe import store
    from homelabscribe.collectors.npm import NpmCollector
    from homelabscribe.config import NpmConfig

    hosts, services, routes = _surrogate_lab()
    for model, fields in _EXPECTED_FIELDS.items():  # the lab really reaches these fields
        assert fields <= {n for n, f in model.model_fields.items() if f.annotation in _TEXT_TYPES}
    monkeypatch.setattr(NpmCollector, "collect", lambda self: [r.model_copy(deep=True) for r in routes])
    cfg = Config(
        output_dir=tmp_path / "docs",
        state_dir=tmp_path / "state",
        npm=[NpmConfig(name=routes[0].proxy, url="http://npm.example.test:81", identity="i", secret="s")],
    )
    lab = Snapshot(hosts=hosts, services=services)
    first = pipeline.scan(cfg, licensing.PRO_FEATURES, collectors=[("lab", FakeCollector(lab))])
    second = pipeline.scan(cfg, licensing.PRO_FEATURES, collectors=[("lab", FakeCollector(lab))])
    assert second.diff is not None and second.diff.empty

    saved = store.recent(cfg.state_dir, 1)[0]
    assert saved == second.snapshot and first.snapshot.model_dump(exclude={"taken_at"}) == saved.model_dump(
        exclude={"taken_at"}
    )
    texts = _texts(saved.model_dump())
    assert not any(renderer_module._SURROGATES.search(t) for t in texts)
    for name in {n for fields in _EXPECTED_FIELDS.values() for n in fields} | {"protocol", "host_ip"}:
        assert any(f"{name}-bad\ufffdtext" in t for t in texts), name
    for name in ("source", "destination", "type", "proxy", "domains", "forward_scheme"):
        assert any(f"{name}-bad\ufffdtext" in t for t in texts), name
    assert saved.routes[0].service == saved.services[0].id
    assert saved.routes[0].forward_host == "name-bad\ufffdtext"
    assert saved.hosts[0].guest_of == saved.services[1].id and "\ufffd" in saved.hosts[0].guest_of
    assert saved.services[1].runs_host == saved.hosts[0].name == "name-bad\ufffdtext"

    renderer = Renderer(cfg.output_dir, licensing.PRO_FEATURES)
    renderer.render(saved, store.load_changelog(cfg.state_dir), cfg.breakglass)
    _pages_hold_no_surrogate(renderer)
    page = (tmp_path / "docs" / "services").glob("*.md")
    assert any("description-bad\ufffdtext" in p.read_text(encoding="utf-8") for p in page)


def _pages_hold_no_surrogate(renderer: Renderer) -> None:
    names = {p.relative_to(renderer.out).as_posix() for p in renderer.generated}
    assert {"index.md", "routes.md", "CHANGELOG.md", "break-glass.md", "topology.md"} <= names
    assert any(n.startswith("services/") for n in names) and any(n.startswith("hosts/") for n in names)
    assert len([n for n in names if n.startswith("services/")]) == 2  # the container and the VM
    for path in renderer.generated:
        text = path.read_text(encoding="utf-8")  # every page was written and reads back as UTF-8
        assert not renderer_module._SURROGATES.search(text)
        assert "bad\ufffdtext" in text or path.name == "CHANGELOG.md"  # no changes: no names on it


def test_a_renderer_handed_text_with_lone_surrogates_writes_every_page(tmp_path):
    hosts, services, routes = _surrogate_lab()
    renderer = Renderer(tmp_path, licensing.PRO_FEATURES)
    renderer.render(Snapshot(hosts=hosts, services=services, routes=routes), [], None)
    _pages_hold_no_surrogate(renderer)


_any_text = st.text(alphabet=st.characters(codec=None), max_size=20)


@settings(max_examples=100, deadline=None)
@given(
    st.lists(_any_text, min_size=12, max_size=12),
    st.dictionaries(_any_text, _any_text, max_size=3),
)
def test_a_cleaned_snapshot_with_any_text_is_saved_and_read_back(tmp_path_factory, t, labels):
    from homelabscribe import store
    from homelabscribe.models import Route

    snap = Snapshot(
        hosts=[Host(name=t[0], kind="docker", os=t[1])],
        services=[
            Service(name=t[2], host=t[0], kind="container", image=t[3], networks=[t[4]], labels=labels),
            Service(name=t[5], host=t[0], kind="manual", display_name=t[6], description=t[7], category=t[8]),
        ],
        routes=[Route(proxy=t[9], domains=[t[10]], forward_host=t[11], forward_port=1)],
    )
    cleaned = pipeline.snapshot_without_surrogates_or_markers(snap)
    state = tmp_path_factory.mktemp("state")
    store.save(cleaned, state)
    assert store.recent(state, 1) == [cleaned]
    texts = _texts(snap.model_dump())
    if not any(renderer_module._SURROGATES.search(x) or "homelabscribe:notes:" in x for x in texts):
        assert cleaned == snap


# ---- text from the lab is never taken for the notes block -----------------------------------------

PAIR = f"{notes.START} FAKE {notes.END}"
TOKEN = "homelabscribe:notes:"  # what every marker needs
assert TOKEN in notes.START and TOKEN in notes.END


def _marked_lab(image: str) -> Snapshot:
    return Snapshot(
        hosts=[Host(name="nova", kind="docker")],
        services=[
            Service(
                name="alpha",
                host="nova",
                kind="container",
                image=image,
                display_name=f"Alpha {PAIR}",
                description=f"Alpha does things {PAIR}",
                labels={"com.example.note": f"see {PAIR}"},
            ),
            Service(name="beta", host="nova", kind="container", description=f"Beta {notes.START} half"),
        ],
    )


def test_markers_in_lab_text_never_replace_the_notes(tmp_path):
    cfg = Config(output_dir=tmp_path / "docs", state_dir=tmp_path / "state")
    pro = licensing.PRO_FEATURES
    pipeline.scan(cfg, pro, collectors=[("lab", FakeCollector(_marked_lab(f"a:1 {PAIR}")))])
    pipeline.render(cfg, pro)
    service = tmp_path / "docs" / "services" / f"{slug(f'Alpha {PAIR}')}.md"
    changelog = tmp_path / "docs" / "CHANGELOG.md"
    _with_notes(service, "Alpha's notes.")
    _with_notes(changelog, "Changelog notes.")

    pipeline.scan(cfg, pro, collectors=[("lab", FakeCollector(_marked_lab(f"a:2 {PAIR}")))])
    pipeline.render(cfg, pro)
    assert "a:2" in changelog.read_text()  # an entry with text from the lab
    sizes = {p: p.stat().st_size for p in (tmp_path / "docs").rglob("*.md")}
    assert pipeline.render(cfg, pro) == []
    assert "Alpha's notes." in service.read_text() and "Changelog notes." in changelog.read_text()
    for page, size in sizes.items():
        text = page.read_text()
        assert text.count(notes.START) == text.count(notes.END) == 1, page
        assert page.stat().st_size <= size, page


_token_text = st.lists(
    st.sampled_from(
        [
            TOKEN,
            "homelabscribe",
            ":notes:",
            "homelabscribe-notes:",
            ":",
            "-",
            "\ud800",
            notes.START,
        ]
    )
    | st.text(alphabet=st.characters(codec=None), max_size=8),
    max_size=8,
).map("".join)


@settings(max_examples=300, deadline=None)
@given(_token_text)
def test_cleaned_text_never_holds_the_marker_token(text):
    cleaned = pipeline.without_surrogates_or_markers(text)
    assert TOKEN not in cleaned and not renderer_module._SURROGATES.search(cleaned)
    if TOKEN not in text and not renderer_module._SURROGATES.search(text):
        assert cleaned == text


@pytest.mark.parametrize("new_image", [f"img:1 {TOKEN}x", f"img:2 {TOKEN}x"])
def test_a_snapshot_saved_with_the_marker_token_is_cleaned_before_it_is_compared(tmp_path, new_image):
    from homelabscribe import store

    cfg = Config(output_dir=tmp_path / "docs", state_dir=tmp_path / "state")
    old = _marked_lab(f"img:1 {TOKEN}x")
    store.save(old, cfg.state_dir)  # as a version before the cleaning would have saved it
    assert TOKEN in (next((cfg.state_dir / "snapshots").glob("*.json"))).read_text()

    result = pipeline.scan(
        cfg, licensing.PRO_FEATURES, collectors=[("lab", FakeCollector(_marked_lab(new_image)))]
    )
    assert result.diff is not None
    if new_image == old.services[0].image:
        assert result.diff.empty  # the cleaning alone is not a change
    else:
        assert [c.kind for c in result.diff.changes] == ["changed"]
    assert TOKEN not in str(store.load_changelog(cfg.state_dir))


def test_the_cleaning_walks_every_kind_of_container():
    bad = f"x\ud800{TOKEN}y"
    good = "x\ufffdhomelabscribe-notes:y"
    data = {
        bad: [bad, (bad, 1), {bad}, frozenset({bad, 2.5})],
        "t": (bad, [bad], {bad: (bad,)}, frozenset({(bad, None)})),
        "s": {bad, (bad, bad)},
        "n": [None, True, 3],
    }
    out = pipeline._value_without_surrogates_or_markers(data)
    assert out == {
        good: [good, (good, 1), {good}, frozenset({good, 2.5})],
        "t": (good, [good], {good: (good,)}, frozenset({(good, None)})),
        "s": {good, (good, good)},
        "n": [None, True, 3],
    }
    assert type(out[good][1]) is tuple and type(out[good][2]) is set and type(out[good][3]) is frozenset
    assert type(out["t"]) is tuple and type(out["t"][1]) is list and type(out["s"]) is set


# ---- names from the config are matched in their cleaned form ---------------------------------------


@pytest.mark.parametrize("suffix", [f"{TOKEN}x", "\udcff"])  # "\udcff": a byte that is not UTF-8 in ${VAR}
def test_an_outage_keeps_what_was_last_known_of_a_source_whose_name_needs_cleaning(
    tmp_path, monkeypatch, suffix
):
    from test_collectors import FakeDockerClient

    from homelabscribe.collectors.docker import DockerCollector
    from homelabscribe.collectors.npm import NpmCollector
    from homelabscribe.config import load_config
    from homelabscribe.models import Route

    monkeypatch.setenv("LAB_SUFFIX", suffix)
    path = tmp_path / "homelabscribe.yaml"
    path.write_text(
        f"output_dir: {tmp_path / 'docs'}\nstate_dir: {tmp_path / 'state'}\n"
        "docker_hosts:\n  - {name: 'nova-${LAB_SUFFIX}', url: 'tcp://fake:2375'}\n"
        "npm:\n  - {name: 'proxy-${LAB_SUFFIX}', url: 'http://npm.example.test:81', identity: i, secret: s}\n"
        "manual_services:\n  - {name: printer, host: 'nova-${LAB_SUFFIX}'}\n"
    )
    cfg = load_config(path)
    host, proxy = cfg.docker_hosts[0].name, cfg.npm[0].name
    assert (host, proxy) == (f"nova-{suffix}", f"proxy-{suffix}")  # as written, never changed
    route = Route(proxy=proxy, domains=["tv.example.test"], forward_host=host, forward_port=8989)
    monkeypatch.setattr(DockerCollector, "client", property(lambda self: FakeDockerClient()))
    monkeypatch.setattr(NpmCollector, "collect", lambda self: [route.model_copy()])
    good = pipeline.scan(cfg, licensing.PRO_FEATURES).snapshot
    assert len(good.hosts) == 1 and len(good.services) == 2 and len(good.routes) == 1

    def down(self):
        raise ConnectionError("down")

    monkeypatch.setattr(DockerCollector, "client", property(down))
    monkeypatch.setattr(NpmCollector, "collect", down)
    result = pipeline.scan(cfg, licensing.PRO_FEATURES)
    assert len(result.errors) == 2 and f"'{host}'" in result.errors[0]  # messages show the name as written
    assert result.diff is not None and result.diff.empty
    now = result.snapshot
    assert now.model_dump(include={"hosts", "routes"}) == good.model_dump(include={"hosts", "routes"})
    # A hand-described service on a host that is kept is listed twice; so it is on the default branch too.
    assert {s.model_dump_json() for s in now.services} == {s.model_dump_json() for s in good.services}


def test_critical_services_named_in_the_config_are_found_by_their_cleaned_names(tmp_path):
    from homelabscribe.config import BreakGlassConfig

    title = f"Alpha {TOKEN}x"
    cfg = Config(
        output_dir=tmp_path / "docs",
        state_dir=tmp_path / "state",
        breakglass=BreakGlassConfig(critical=[title]),
    )
    lab = Snapshot(
        hosts=[Host(name="nova", kind="docker")],
        services=[
            Service(name="alpha", host="nova", kind="container", display_name=title),
            Service(name="beta", host="nova", kind="container", display_name="Beta"),
        ],
    )
    pipeline.render(cfg, licensing.PRO_FEATURES, pipeline.snapshot_without_surrogates_or_markers(lab))
    page = (tmp_path / "docs" / "break-glass.md").read_text()
    assert "Alpha homelabscribe-notes:x" in page and "Beta" not in page
    assert cfg.breakglass.critical == [title]  # the config itself is not changed


def test_a_host_another_source_returned_is_not_kept_twice_when_its_name_needs_cleaning():
    from homelabscribe.collectors.docker import DockerCollector

    name = f"nova-{TOKEN}x"
    previous = pipeline.snapshot_without_surrogates_or_markers(
        Snapshot(
            hosts=[Host(name=name, kind="docker")],
            services=[Service(name="web", host=name, kind="container")],
        )
    )
    scanned = Snapshot(hosts=[Host(name=name, kind="proxmox-node")])  # as the new scan returned it
    collector = DockerCollector(DockerHostConfig(name=name, url="tcp://fake:2375"))
    assert pipeline._carry_over(previous, collector, scanned) == ([], [])


def test_render_cleans_a_snapshot_saved_before_the_cleaning(tmp_path):
    from homelabscribe import store

    cfg = Config(output_dir=tmp_path / "docs", state_dir=tmp_path / "state")
    lab = Snapshot(
        hosts=[Host(name="nova", kind="docker")],
        services=[Service(name="alpha", host="nova", kind="container", description=f"Alpha {PAIR}")],
    )
    pipeline.render(cfg, licensing.PRO_FEATURES, pipeline.snapshot_without_surrogates_or_markers(lab))
    page = tmp_path / "docs" / "services" / "alpha.md"
    _with_notes(page, "Alpha's notes.")
    store.save(lab, cfg.state_dir)  # as a version before the cleaning saved it: the markers are in it
    assert notes.START in next((cfg.state_dir / "snapshots").glob("*.json")).read_text()

    pipeline.render(cfg, licensing.PRO_FEATURES)  # the newest snapshot from the store
    text = page.read_text()
    assert "Alpha's notes." in text
    assert text.count(notes.START) == text.count(notes.END) == 1
