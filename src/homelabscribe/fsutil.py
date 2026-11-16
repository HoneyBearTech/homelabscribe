"""Safe file writes: a file is replaced in one step, so a write that fails leaves the old file as it was."""

from __future__ import annotations

import contextlib
import errno
import os
import secrets
import stat
from pathlib import Path

TEMP_SUFFIX = ".hls-tmp"  # no glob for pages (`*.md`), snapshots (`*.json`) or HTML (`*.html`) matches it

# Create the file itself, never what a link in its place points at (and as bytes, where that is a choice).
NEW_FILE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)

# What a file system that cannot sync a file at all answers: the copy is then as safe as it allows.
_NO_SYNC = frozenset({errno.EINVAL, errno.ENOTSUP, errno.ENOSYS})


def fill(fd: int, data: bytes) -> None:
    """Write all of `data` to the new file open at `fd`, flush it to disk where that is possible, close it."""
    try:
        f = os.fdopen(fd, "wb")
    except BaseException:
        os.close(fd)
        raise
    with f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())  # on disk before it takes the old file's place
        except OSError as e:
            if e.errno not in _NO_SYNC:
                raise


def atomic_write(path: Path, data: bytes) -> None:
    """Replace `path` with `data`, or create it, in one step. Raises OSError and changes nothing on failure.

    The data goes to a temporary file in the same folder, is synced, and then takes the place of `path`.
    The new file keeps the permission bits of the one it replaces. A name held by a link, a folder or
    anything that is not a plain file is never written to, and neither is a file that is read-only.
    """
    mode = None
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EEXIST, "not a regular file")
        if not os.access(path, os.W_OK):
            raise PermissionError(errno.EACCES, "read-only")
        mode = st.st_mode & 0o777
    tmp = path.parent / f".{secrets.token_hex(8)}{TEMP_SUFFIX}"
    fd = os.open(tmp, NEW_FILE, 0o666)
    try:
        if mode is not None and hasattr(os, "fchmod"):
            try:
                os.fchmod(fd, mode)
            except BaseException:
                os.close(fd)
                raise
        fill(fd, data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def read_text(path: Path) -> str:
    """The text of a plain file, which must be UTF-8 and hold no NUL character.

    Raises OSError for a file that is not a plain file (a link is followed; a pipe or device is never
    opened, as that could block) and UnicodeDecodeError for any other encoding. UTF-16 without a byte-order
    mark would decode as UTF-8 with a NUL after every letter, so a NUL counts as another encoding.
    """
    return read_text_version(path)[0]


def read_text_version(path: Path) -> tuple[str, os.stat_result]:
    """`read_text`, and which file, in which version, the text was read from (`os.fstat` of the open file)."""
    if not stat.S_ISREG(os.stat(path).st_mode):
        raise OSError(errno.EINVAL, "not a regular file")
    with path.open("rb") as f:
        version = os.fstat(f.fileno())
        text = f.read().decode("utf-8")
    if "\0" in text:
        raise UnicodeDecodeError("utf-8", b"\0", 0, 1, "NUL character")
    return text, version
