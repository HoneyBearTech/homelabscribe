"""State files: safe writes, and damaged files that are skipped instead of stopping the run."""

import errno
import json
import os

import pytest
from hypothesis import given
from hypothesis import strategies as st

from homelabscribe import fsutil, store
from homelabscribe.demo import demo_snapshots
from homelabscribe.enrich import enrich, load_catalog

ROOT = os.name != "posix" or os.geteuid() == 0


def _snapshots():
    old, new = demo_snapshots()
    enrich(old.services, load_catalog())
    enrich(new.services, load_catalog())
    return old, new


def _fail_replace(monkeypatch):
    def replace(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "replace", replace)


def test_atomic_write_keeps_the_permission_bits_and_leaves_no_temp_file(tmp_path):
    f = tmp_path / "a.json"
    f.write_text("old")
    f.chmod(0o640)
    fsutil.atomic_write(f, b"new")
    assert f.read_bytes() == b"new" and f.stat().st_mode & 0o777 == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["a.json"]


def test_atomic_write_that_fails_leaves_the_old_file_and_no_temp_file(tmp_path, monkeypatch):
    f = tmp_path / "a.json"
    f.write_text("old")
    _fail_replace(monkeypatch)
    with pytest.raises(OSError):
        fsutil.atomic_write(f, b"new")
    assert f.read_text() == "old" and [p.name for p in tmp_path.iterdir()] == ["a.json"]


def test_atomic_write_halfway_failure_leaves_the_old_file(tmp_path, monkeypatch):
    f = tmp_path / "a.json"
    f.write_text("old")
    real = fsutil.fill

    def half(fd, data):
        real(fd, data[:1])
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(fsutil, "fill", half)
    with pytest.raises(OSError):
        fsutil.atomic_write(f, b"new")
    assert f.read_text() == "old" and [p.name for p in tmp_path.iterdir()] == ["a.json"]


@pytest.mark.skipif(os.name != "posix", reason="needs symbolic links")
def test_atomic_write_never_writes_through_a_link_or_into_a_folder(tmp_path):
    outside = tmp_path / "outside.txt"
    link = tmp_path / "link.json"
    link.symlink_to(outside)
    with pytest.raises(OSError):
        fsutil.atomic_write(link, b"x")
    assert not outside.exists() and link.is_symlink()
    (tmp_path / "dir.json").mkdir()
    with pytest.raises(OSError):
        fsutil.atomic_write(tmp_path / "dir.json", b"x")


def test_a_failed_snapshot_write_keeps_the_older_snapshots(tmp_path, monkeypatch):
    old, new = _snapshots()
    store.save(old, tmp_path)
    _fail_replace(monkeypatch)
    with pytest.raises(OSError):
        store.save(new, tmp_path)
    files = list((tmp_path / "snapshots").iterdir())
    assert len(files) == 1 and store.recent(tmp_path, 1)[0].taken_at == old.taken_at


def test_a_failed_changelog_write_keeps_the_old_changelog(tmp_path, monkeypatch):
    store.save_changelog([{"at": "2026-01-01T00:00:00", "changes": []}], tmp_path)
    before = (tmp_path / "changelog.json").read_bytes()
    _fail_replace(monkeypatch)
    with pytest.raises(OSError):
        store.save_changelog([], tmp_path)
    assert (tmp_path / "changelog.json").read_bytes() == before


def test_leftover_temp_files_are_not_snapshots(tmp_path):
    old, _ = _snapshots()
    store.save(old, tmp_path)
    (tmp_path / "snapshots" / f".abc{fsutil.TEMP_SUFFIX}").write_text("{")
    skipped = []
    assert len(store.recent(tmp_path, 5, skipped)) == 1 and skipped == []


def _two(tmp_path):
    old, new = _snapshots()
    older = store.save(old, tmp_path)
    newer = tmp_path / "snapshots" / "99990101T000000Z.json"
    newer.write_text(new.model_dump_json())
    return older, newer


@pytest.mark.parametrize("content", [b'{"hosts": [', b"", b"\0" * 64, b"\xff\xfe{\x00", b"[]"])
def test_a_damaged_newest_snapshot_is_skipped_and_left_in_place(tmp_path, content):
    older, newer = _two(tmp_path)
    newer.write_bytes(content)
    skipped = []
    got = store.recent(tmp_path, 1, skipped)
    assert len(got) == 1 and got[0].taken_at == _snapshots()[0].taken_at
    assert skipped == [f"snapshots/{newer.name} (damaged or not a snapshot)"]
    assert newer.read_bytes() == content and older.exists()


def test_when_no_snapshot_can_be_read_there_is_none(tmp_path):
    _, newer = _two(tmp_path)
    for f in (tmp_path / "snapshots").glob("*.json"):
        f.write_bytes(b"{")
    skipped = []
    assert store.recent(tmp_path, 1, skipped) == [] and len(skipped) == 2


@pytest.mark.skipif(ROOT, reason="root can read any file")
def test_a_snapshot_without_permission_is_skipped(tmp_path):
    _, newer = _two(tmp_path)
    newer.chmod(0)
    try:
        skipped = []
        assert len(store.recent(tmp_path, 1, skipped)) == 1
        assert skipped == [f"snapshots/{newer.name} (could not be read)"]
    finally:
        newer.chmod(0o644)


def test_the_snapshot_folder_check(tmp_path):
    assert store.writable(tmp_path / "state")
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert not store.writable(blocker)


@pytest.mark.skipif(ROOT, reason="root can write anywhere")
def test_a_read_only_snapshot_folder_is_not_writable(tmp_path):
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots").chmod(0o555)
    try:
        assert not store.writable(tmp_path)
    finally:
        (tmp_path / "snapshots").chmod(0o755)


@pytest.mark.parametrize("content", [b'[{"at": ', b"", b"{}", b"null", b"\xff\xfe[\x00]", b"5"])
def test_a_damaged_changelog_is_moved_aside_and_a_new_one_started(tmp_path, content):
    (tmp_path / "changelog.json").write_bytes(content)
    skipped = []
    assert store.load_changelog(tmp_path, skipped) == []
    assert (tmp_path / "changelog.json.damaged").read_bytes() == content
    assert not (tmp_path / "changelog.json").exists()
    assert len(skipped) == 1 and skipped[0].startswith("changelog.json (") and ".damaged" in skipped[0]


def test_a_second_damaged_changelog_never_overwrites_the_first(tmp_path):
    for content in (b"{", b"}"):
        (tmp_path / "changelog.json").write_bytes(content)
        store.load_changelog(tmp_path, [])
    assert (tmp_path / "changelog.json.damaged").read_bytes() == b"{"
    assert (tmp_path / "changelog.json.damaged-2").read_bytes() == b"}"


def test_a_good_changelog_is_loaded_as_it_is(tmp_path):
    store.save_changelog([{"at": "x"}], tmp_path)
    skipped = []
    assert store.load_changelog(tmp_path, skipped) == [{"at": "x"}] and skipped == []
    assert store.load_changelog(tmp_path / "none") == []


@given(
    st.recursive(
        st.none() | st.booleans() | st.integers() | st.text(),
        lambda c: st.lists(c) | st.dictionaries(st.text(), c),
    )
)
def test_reading_a_changelog_entry_never_raises(value):
    assert store.entry_ok(value) in (True, False)


def test_entry_ok_wants_a_date_and_changes():
    change = {"kind": "added", "service": "Plex", "detail": "x"}
    assert store.entry_ok({"at": "2026-01-01T10:00:00+00:00", "changes": [change]})
    assert not store.entry_ok({"changes": []})
    assert not store.entry_ok({"at": "yesterday", "changes": []})
    assert not store.entry_ok({"at": "2026-01-01", "changes": [1]})
    assert not store.entry_ok({"at": "2026-01-01", "changes": [{"kind": "x"}]})
    assert not store.entry_ok(json.loads("[1]"))
