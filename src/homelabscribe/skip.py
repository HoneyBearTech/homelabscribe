"""A file that was left alone: its name, kept apart from the reason, and the line printed for it."""

from __future__ import annotations


class Skip(str):
    """`<path> (<reason>)` as text, with `path` kept on its own.

    It is a `str`, so it prints, joins and compares as the plain line does. `path` is what a run compares to
    print each file once: file names can contain ` (`, so the line is never cut to find it.
    """

    path: str

    def __new__(cls, path: str, reason: str) -> Skip:
        line = super().__new__(cls, f"{path} ({reason})")
        line.path = path
        return line
