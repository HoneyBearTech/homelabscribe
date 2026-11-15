"""Pages that cannot be read or written are left alone and reported; the rest of the render goes on."""

import errno
import os

import pytest

from homelabscribe import fsutil, licensing, notes
from homelabscribe.demo import demo_snapshots
from homelabscribe.enrich import enrich, load_catalog
from homelabscribe.renderer import Renderer

ROOT = os.name != "posix" or os.geteuid() == 0
POSIX = pytest.mark.skipif(os.name != "posix", reason="needs POSIX permissions and links")
NOT_ROOT = pytest.mark.skipif(ROOT, reason="root can read and write anything")


def _snap():
    _, new = demo_snapshots()
    enrich(new.services, load_catalog())
    return new


def _lab(out, text="Plex pass renews in May."):
    """Render the lab once and give the Plex page notes. Returns the changed lab and the page."""
    snap = _snap()
    Renderer(out, licensing.PRO_FEATURES).render(snap, [], None)
    page = out / "services" / "plex.md"
    page.write_text(page.read_text().replace(notes.PLACEHOLDER, text))
    snap.services = [
        s.model_copy(update={"image": s.image + "x"}) if s.name == "plex" else s for s in snap.services
    ]
    snap.taken_at = snap.taken_at.replace(year=2030)  # the index page changes too
    return snap, page


def _run(out, snap):
    r = Renderer(out, licensing.PRO_FEATURES)
    r.render(snap, [], None)
    return r


def test_a_page_write_that_fails_leaves_the_page_and_its_notes(tmp_path, monkeypatch):
    snap, page = _lab(tmp_path)
    before = page.read_bytes()

    def boom(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "replace", boom)
    r = _run(tmp_path, snap)
    assert page.read_bytes() == before and b"Plex pass renews in May." in before
    assert any(line.startswith("services/plex.md (could not be written") for line in r.skipped)
    assert not [p for p in tmp_path.rglob("*") if p.name.endswith(fsutil.TEMP_SUFFIX)]


def test_a_page_write_cut_short_leaves_the_page(tmp_path, monkeypatch):
    snap, page = _lab(tmp_path)
    before = page.read_bytes()
    real = fsutil.fill

    def half(fd, data):
        real(fd, data[: len(data) // 2])
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(fsutil, "fill", half)
    _run(tmp_path, snap)
    assert page.read_bytes() == before
    assert not [p for p in tmp_path.rglob("*") if p.name.endswith(fsutil.TEMP_SUFFIX)]


@POSIX
def test_a_rewritten_page_keeps_its_permission_bits(tmp_path):
    snap, page = _lab(tmp_path)
    page.chmod(0o640)
    before = page.read_bytes()
    _run(tmp_path, snap)
    assert page.read_bytes() != before  # it was rewritten
    assert page.stat().st_mode & 0o777 == 0o640


@POSIX
def test_a_link_in_the_place_of_a_page_is_never_written_through(tmp_path):
    snap = _snap()
    target = tmp_path / "elsewhere" / "created.txt"  # a broken link pointing outside the output folder
    target.parent.mkdir()
    out = tmp_path / "docs"
    (out / "services").mkdir(parents=True)
    (out / "services" / "plex.md").symlink_to(target)
    r = _run(out, snap)
    assert not target.exists() and (out / "services" / "plex.md").is_symlink()
    assert any(line.startswith("services/plex.md (the page is a symbolic link") for line in r.skipped)
    assert (out / "services" / "sonarr.md").exists()


@POSIX
def test_a_folder_that_links_to_a_folder_is_followed_as_before(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "gone.md").write_text("no notes")
    out = tmp_path / "docs"
    out.mkdir()
    (out / "services").symlink_to(elsewhere)
    r = _run(out, _snap())
    assert (elsewhere / "sonarr.md").exists() and r.skipped == []
    assert not (elsewhere / "gone.md").exists()  # swept like any page in the folder


@POSIX
@pytest.mark.parametrize("kind", ["dangling", "file"])
def test_a_folder_link_that_leads_nowhere_reports_its_pages(tmp_path, kind):
    out = tmp_path / "docs"
    out.mkdir()
    target = tmp_path / "target"
    if kind == "file":
        target.write_text("not a folder")
    (out / "services").symlink_to(target)
    r = _run(out, _snap())
    assert any(line.startswith("services/sonarr.md (could not be written") for line in r.skipped)
    assert (out / "index.md").exists() and (out / "hosts" / "nova.md").exists()
    assert not target.is_dir()


def test_a_folder_named_like_a_page_is_left_alone(tmp_path):
    snap, page = _lab(tmp_path)
    page.unlink()
    page.mkdir()
    (page / "keep.txt").write_text("x")
    r = _run(tmp_path, snap)
    assert (page / "keep.txt").read_text() == "x"
    assert "services/plex.md (not a regular file, left alone)" in r.skipped
    assert (tmp_path / "services" / "sonarr.md").exists()


def _damage(page, how):
    text = page.read_text()
    if how == "utf16":
        page.write_bytes(text.encode("utf-16"))
    elif how == "latin1":
        page.write_bytes(text.replace("Plex", "Plex é").encode("latin-1"))
    else:
        page.write_bytes(text.encode() + b"\xff\xfe\x80")


@pytest.mark.parametrize("how", ["utf16", "latin1", "tail"])
def test_a_page_that_is_not_utf8_is_left_byte_for_byte(tmp_path, how):
    snap, page = _lab(tmp_path)
    _damage(page, how)
    before = page.read_bytes()
    sonarr = tmp_path / "services" / "sonarr.md"
    sonarr.write_text(sonarr.read_text() + "x")
    r = _run(tmp_path, snap)
    assert page.read_bytes() == before
    assert "services/plex.md (not UTF-8, left as it is)" in r.skipped
    assert not sonarr.read_text().endswith("x")  # the other pages were still rewritten


@pytest.mark.parametrize("how", ["utf16", "tail"])
def test_an_index_that_is_not_utf8_is_left_alone(tmp_path, how):
    snap, _ = _lab(tmp_path)
    index = tmp_path / "index.md"
    _damage(index, how)
    before = index.read_bytes()
    r = _run(tmp_path, snap)
    assert index.read_bytes() == before and "index.md (not UTF-8, left as it is)" in r.skipped


@POSIX
@NOT_ROOT
@pytest.mark.parametrize("mode", [0o444, 0])
def test_a_page_without_write_or_read_permission_is_skipped(tmp_path, mode):
    snap, page = _lab(tmp_path)
    before = page.read_bytes()
    page.chmod(mode)
    try:
        r = _run(tmp_path, snap)
    finally:
        page.chmod(0o644)
    assert page.read_bytes() == before
    assert any(line.startswith("services/plex.md (could not be") for line in r.skipped)
    assert (tmp_path / "services" / "sonarr.md").exists()


@POSIX
@NOT_ROOT
def test_a_page_that_cannot_be_created_in_a_read_only_folder_is_reported(tmp_path):
    snap = _snap()
    Renderer(tmp_path).render(snap)
    (tmp_path / "services" / "plex.md").unlink()
    (tmp_path / "services").chmod(0o555)
    try:
        r = _run(tmp_path, snap)
    finally:
        (tmp_path / "services").chmod(0o755)
    assert "services/plex.md (could not be written, left as it is)" in r.skipped


def _gone(snap):
    snap.services = [s for s in snap.services if s.name != "plex"]
    return snap


@POSIX
def test_the_sweep_leaves_what_it_cannot_read_alone(tmp_path):
    snap, page = _lab(tmp_path)
    page.unlink()
    services = tmp_path / "services"
    (services / "folder.md").mkdir()
    (services / "dead.md").symlink_to(tmp_path / "nowhere.md")
    (services / "stray.md").write_bytes(os.urandom(64) + b"\xff")
    (services / "pic.png").write_bytes(b"\xff\xd8\xff")
    r = Renderer(tmp_path, licensing.PRO_FEATURES)
    r.render(_gone(snap), [], None)
    assert (services / "folder.md").is_dir() and (services / "dead.md").is_symlink()
    assert (services / "stray.md").exists() and (services / "pic.png").exists()
    assert not (tmp_path / "archive").exists()
    names = {line.split(" (")[0] for line in r.skipped}
    assert names == {"services/folder.md", "services/dead.md", "services/stray.md"}


def test_files_named_alike_after_a_bracket_are_skipped_one_by_one(tmp_path):
    snap, _ = _lab(tmp_path)
    for n in (1, 2):
        (tmp_path / "services" / f"plex ({n}).md").write_bytes(os.urandom(64) + b"\xff")
    r = _run(tmp_path, snap)
    assert [s.path for s in r.skipped] == ["services/plex (1).md", "services/plex (2).md"]
    assert r.skipped[1] == "services/plex (2).md (not UTF-8, left as it is)"


def test_a_stray_page_with_notes_that_is_not_utf8_is_neither_removed_nor_archived(tmp_path):
    snap, page = _lab(tmp_path)
    _damage(page, "latin1")
    before = page.read_bytes()
    r = Renderer(tmp_path, licensing.PRO_FEATURES)
    r.render(_gone(snap), [], None)
    assert page.read_bytes() == before and not (tmp_path / "archive").exists()
    assert "services/plex.md (not UTF-8, left as it is)" in r.skipped


@POSIX
def test_a_linked_page_with_notes_is_still_archived_as_a_copy(tmp_path):
    snap, page = _lab(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text(f"{notes.START}\nMy notes\n{notes.END}\n")
    page.unlink()
    page.symlink_to(outside)
    Renderer(tmp_path, licensing.PRO_FEATURES).render(_gone(snap), [], None)
    assert "My notes" in (tmp_path / "archive" / "services" / "plex.md").read_text()
    assert outside.read_text().startswith(notes.START) and not page.exists()


@POSIX
@NOT_ROOT
def test_a_stray_page_in_a_read_only_folder_stays(tmp_path):
    snap, page = _lab(tmp_path, text=notes.PLACEHOLDER)
    page.unlink()
    stray = tmp_path / "services" / "stray.md"
    stray.write_text("no notes here")
    (tmp_path / "services").chmod(0o555)
    try:
        r = Renderer(tmp_path)
        r.render(snap, [], None)
    finally:
        (tmp_path / "services").chmod(0o755)
    assert stray.exists() and "services/stray.md (could not be removed, left as it is)" in r.skipped


def test_an_archive_folder_that_is_a_file_is_reported_and_keeps_the_page(tmp_path):
    snap, page = _lab(tmp_path)
    before = page.read_bytes()
    (tmp_path / "archive").mkdir()
    (tmp_path / "archive" / "services").write_text("in the way")
    r = Renderer(tmp_path, licensing.PRO_FEATURES)
    r.render(_gone(snap), [], None)
    assert page.read_bytes() == before
    assert "services/plex.md (could not be archived, left as it is)" in r.skipped
    assert (tmp_path / "archive" / "services").read_text() == "in the way"


def test_leftover_temp_files_are_not_swept_or_exported(tmp_path):
    snap, _ = _lab(tmp_path)
    leftover = tmp_path / "services" / f".abc{fsutil.TEMP_SUFFIX}"
    leftover.write_text("half a page")
    r = _run(tmp_path, snap)
    assert leftover.exists() and r.skipped == []


def test_unusable_changelog_entries_are_skipped_and_reported(tmp_path):
    snap = _snap()
    good = {
        "at": "2026-01-01T10:00:00+00:00",
        "changes": [{"kind": "added", "service": "Plex", "detail": "d"}],
    }
    bad = [{"changes": []}, {"at": "never", "changes": []}, 5, {"at": "2026-01-01", "changes": [1]}]
    r = Renderer(tmp_path, licensing.PRO_FEATURES)
    r.render(snap, [*bad, good], None)
    text = (tmp_path / "CHANGELOG.md").read_text()
    assert "**Added** Plex: d" in text
    assert r.skipped == ["changelog.json (4 entry(ies) not usable, left in the file)"]


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be"])
def test_utf16_without_a_byte_order_mark_is_left_alone_with_its_notes(tmp_path, codec):
    snap, page = _lab(tmp_path)
    page.write_bytes(page.read_text().encode(codec))
    before = page.read_bytes()
    r = _run(tmp_path, snap)
    assert page.read_bytes() == before
    assert "services/plex.md (not UTF-8, left as it is)" in r.skipped


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be"])
def test_an_obsolete_utf16_page_is_not_deleted_or_archived(tmp_path, codec):
    snap, page = _lab(tmp_path)
    page.write_bytes(page.read_text().encode(codec))
    before = page.read_bytes()
    r = Renderer(tmp_path, licensing.PRO_FEATURES)
    r.render(_gone(snap), [], None)
    assert page.read_bytes() == before and not (tmp_path / "archive").exists()
    assert "services/plex.md (not UTF-8, left as it is)" in r.skipped


@POSIX
def test_a_pipe_in_place_of_a_page_is_never_opened(tmp_path):
    snap, page = _lab(tmp_path)
    page.unlink()
    os.mkfifo(page)
    os.mkfifo(tmp_path / "services" / "stray.md")
    r = _run(tmp_path, snap)
    assert page.exists() and (tmp_path / "services" / "stray.md").exists()
    assert "services/plex.md (not a regular file, left alone)" in r.skipped
    assert "services/stray.md (could not be read, left as it is)" in r.skipped


def test_a_date_that_cannot_be_shown_in_local_time_is_skipped_not_fatal(tmp_path):
    entries = [
        {"at": "0001-01-01T00:00:00+14:00", "changes": []},
        {"at": "9999-12-31T23:59:59-14:00", "changes": []},
    ]
    r = Renderer(tmp_path, licensing.PRO_FEATURES)
    r.render(_snap(), entries, None)
    assert r.skipped == ["changelog.json (2 entry(ies) not usable, left in the file)"]
