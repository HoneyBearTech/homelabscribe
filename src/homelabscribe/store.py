"""Snapshots are plain JSON files, one per scan, so they're easy to inspect, diff and back up.

Every file is written to a temporary file and then swapped in, so a write that fails leaves the old file.
A file that cannot be read is skipped and reported (as `<path below the state folder> (<reason>)` in the
optional `skipped` list); it never stops the run.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import stat
from datetime import datetime
from pathlib import Path

from homelabscribe import fsutil
from homelabscribe.models import Snapshot

KEEP = 60  # roughly two months of daily scans


def _dir(state_dir: Path) -> Path:
    d = state_dir / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def writable(state_dir: Path) -> bool:
    """True when snapshots can be saved: the folder exists or can be made, and can be written to."""
    try:
        return os.access(_dir(state_dir), os.W_OK | os.X_OK)
    except OSError:
        return False


def save(snapshot: Snapshot, state_dir: Path) -> Path:
    """Save the snapshot. Raises OSError if that is not possible; an older snapshot is never changed."""
    folder = _dir(state_dir)
    path = folder / f"{snapshot.taken_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    fsutil.atomic_write(path, snapshot.model_dump_json(indent=2).encode("utf-8"))
    for old in sorted(folder.glob("*.json"))[:-KEEP]:
        with contextlib.suppress(OSError):
            old.unlink()
    return path


def recent(state_dir: Path, n: int = 2, skipped: list[str] | None = None) -> list[Snapshot]:
    """The newest `n` snapshots that can be read, newest first. A damaged one is skipped and left in place."""
    found: list[Snapshot] = []
    for f in sorted((state_dir / "snapshots").glob("*.json"), reverse=True):
        if len(found) >= n:
            break
        try:
            found.append(Snapshot.model_validate_json(fsutil.read_text(f)))
        except (OSError, ValueError) as e:
            if skipped is not None:
                why = "could not be read" if isinstance(e, OSError) else "damaged or not a snapshot"
                skipped.append(f"snapshots/{f.name} ({why})")
    return found


def entry_ok(entry: object) -> bool:
    """True for a changelog entry the pages can use: a dict with an ISO date `at` and a list of changes."""
    if not isinstance(entry, dict) or not isinstance(entry.get("at"), str):
        return False
    try:
        datetime.fromisoformat(entry["at"]).astimezone()  # the pages show local time
    except (ValueError, OverflowError, OSError):
        return False
    changes = entry.get("changes")
    return isinstance(changes, list) and all(
        isinstance(c, dict) and all(isinstance(c.get(k), str) for k in ("kind", "service", "detail"))
        for c in changes
    )


class ChangelogBlocked(Exception):
    """changelog.json cannot be used and could not be moved aside, so it must not be written over."""


def _name(path: Path, n: int) -> Path:
    return path.with_name(path.name + (".damaged" if n == 1 else f".damaged-{n}"))


def _copy_aside(path: Path) -> str | None:
    """Where hard links are not possible: copy the file to the next free `.damaged` name, then remove it.

    The copy is created new in one step (a taken name, even a link, is skipped for the next number), with
    the original's permission bits, written in full and synced. The original is removed only if it is
    still the same file with the same bytes. Any failure removes the copy and leaves the original as it is.
    """
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode):
            return None
        data = path.read_bytes()
    except OSError:
        return None
    for n in range(1, 1000):
        dest = _name(path, n)
        try:
            fd = os.open(dest, fsutil.NEW_FILE, st.st_mode & 0o777)
        except FileExistsError:
            continue
        except OSError:
            return None
        try:
            if hasattr(os, "fchmod"):
                try:
                    os.fchmod(fd, st.st_mode & 0o777)
                except BaseException:
                    os.close(fd)
                    raise
            fsutil.fill(fd, data)
            now = os.lstat(path)
            same = (now.st_ino, now.st_size, now.st_mtime_ns) == (st.st_ino, st.st_size, st.st_mtime_ns)
            if not same or path.read_bytes() != data:
                raise OSError(errno.EBUSY, "changed while it was copied")
            path.unlink()
        except (OSError, NotImplementedError):
            with contextlib.suppress(OSError):
                dest.unlink()  # our own partial copy, never a file that was there before
            return None
        return dest.name
    return None


def _move_aside(path: Path) -> str | None:
    """Move a changelog that cannot be used to `changelog.json.damaged` (then `-2`, `-3`; never replacing).

    A hard link makes the new name and fails if the name is taken; only then is the old name removed (and
    if that fails, the new link is removed again). Where hard links are not possible the file is copied
    instead. Returns the new name, or None if it could not be moved: the file is then as it was.
    """
    for n in range(1, 1000):
        dest = _name(path, n)
        try:
            os.link(path, dest, follow_symlinks=False)
        except FileExistsError:
            continue
        except (OSError, NotImplementedError):
            return _copy_aside(path)
        try:
            path.unlink()
        except OSError:
            with contextlib.suppress(OSError):
                dest.unlink()  # so a copy is not added on every run
            return None
        return dest.name
    return None


def load_changelog(state_dir: Path, skipped: list[str] | None = None) -> list[dict]:
    """The entries of changelog.json. A file that cannot be used is moved aside and an empty list returned.

    Raises ChangelogBlocked when it cannot be used and cannot be moved aside either: it stays as it is.
    """
    path = state_dir / "changelog.json"
    if not os.path.lexists(path):
        return []
    try:
        data = json.loads(fsutil.read_text(path))
        if isinstance(data, list):
            return data
        why = "damaged or not a list of entries"
    except (OSError, ValueError):
        why = "damaged or could not be read"
    moved = _move_aside(path)
    if skipped is not None:
        note = f"moved to {moved}" if moved else "could not be moved aside, left as it is"
        skipped.append(f"changelog.json ({why}; {note})")
    if moved is None or os.path.lexists(path):
        raise ChangelogBlocked
    return []


def save_changelog(entries: list[dict], state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    fsutil.atomic_write(state_dir / "changelog.json", json.dumps(entries[-500:], indent=2).encode("utf-8"))
