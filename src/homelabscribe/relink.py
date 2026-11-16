"""The generated links of a page that is archived, pointed at where their pages are now.

A page in `services/` or `hosts/` links to other pages with paths relative to its folder: `../hosts/media.md`,
`../services/plex.md` and `sonarr.md`. Once the page is moved to `archive/services/` or `archive/hosts/`,
those paths point inside `archive/`. `relink` rewrites only the links the templates write, on the lines
they write them on, and only before the notes block; everything else, line endings included, stays as it is.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from homelabscribe import notes

# A page name as `slug` and `coded_name` make it: lower-case letters and digits in runs joined by single
# hyphens, at most 200 characters (`renderer.SLUG_MAX`), followed by `.md)`.
_NAME = r"(?=[a-z0-9-]{1,200}\.md\))[a-z0-9]+(?:-[a-z0-9]+)*"
# The text of a link as the templates write it (titles and host names are not escaped), read the way
# Markdown reads it: a backslash escapes the character after it (`\]` is a bracket in the text), and the
# text ends at the first other `](`. On one line; in a table, no `|` either, other than an escaped `\|`.
_TEXT = r"(?:\\[^\r\n]|[^\\\]\r\n]|\](?!\())*"
_CELL = r"(?:\\[^\r\n]|[^\\\]|\r\n]|\](?!\())*"


def _link(group: str, folder: str | None, text: str = _CELL) -> str:
    """A link as the templates write it, its text in `group` and its page name in `<group>_name`: a page in
    `../<folder>/`, or in the page's own folder when `folder` is None."""
    where = "" if folder is None else rf"\.\./{folder}/"
    return rf"\[(?P<{group}>{text})\]\({where}(?P<{group}_name>{_NAME})\.md\)"


# The lines with generated links, by the folder the page is in: how each line is written, and the folder of
# each link's page, in the order of the links.
_LINES: dict[str, list[tuple[re.Pattern[str], tuple[str, ...]]]] = {
    "services": [
        (re.compile(rf"\| Runs on \| {_link('a', 'hosts')} \|"), ("hosts",)),
        (re.compile(rf"\| Is the Docker host \| {_link('a', 'hosts')} \|"), ("hosts",)),
        (re.compile(rf"- Depends on {_link('a', None, _TEXT)}"), ("services",)),
        (re.compile(rf"- Used by {_link('a', None, _TEXT)}"), ("services",)),
    ],
    "hosts": [
        (
            re.compile(rf"\| Runs as \| (?:VM|LXC) {_link('a', 'services')} on {_link('b', 'hosts')} \|"),
            ("services", "hosts"),
        ),
        (re.compile(rf"\| {_link('a', 'services')} \|.*"), ("services",)),
    ],
}

Where = Callable[[str, str], str | None]
"""Where the page `<folder>/<name>.md` is now, below the output folder (`hosts/media.md`,
`archive/services/plex-2.md`), or None when it is nowhere."""


def _fix_line(line: str, folder: str, where: Where) -> str:
    for pattern, folders in _LINES.get(folder, ()):
        m = pattern.fullmatch(line)
        if m is None:
            continue
        out, at = [], 0
        for group, target in zip("ab", folders, strict=False):
            now = where(target, m.group(f"{group}_name"))
            # The whole link, `[` to `)`: from just before the text to just after the name and `.md)`.
            start, end = m.start(group) - 1, m.end(f"{group}_name") + len(".md)")
            out += [line[at:start], m.group(group) if now is None else f"[{m.group(group)}](../../{now})"]
            at = end
        return "".join(out) + line[at:]
    return line


def relink(text: str, folder: str, where: Where) -> str:
    """The text of a page from `folder` (`services` or `hosts`), with its generated links pointed at the
    page each now names, for a copy of the page in `archive/<folder>/`.

    Only the part before the first notes start marker is looked at (the split `notes` makes); from the
    marker on, nothing changes, and a page without one is returned as it is. In that part, a link is
    changed only on a line written the way a template writes it ("Runs on", "Is the Docker host",
    "Depends on", "Used by", "Runs as" and the rows of a host's table) and only when it names a page
    the way HomelabScribe names one. Such a link then points at `../../<where it is now>`, or, when its
    page is nowhere, becomes its text, exactly as written between the brackets. Line endings (`\\n` or
    `\\r\\n`) stay as they were. A link changed once never matches again, so a second call changes nothing.
    """
    head, start, rest = text.partition(notes.START)
    if not start:
        return text
    lines = head.split("\n")
    for i, line in enumerate(lines):
        body, cr = (line[:-1], "\r") if line.endswith("\r") else (line, "")
        lines[i] = _fix_line(body, folder, where) + cr
    return "\n".join(lines) + start + rest
