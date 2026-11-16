"""The generated links of a page that is archived, pointed at where their pages are now.

A page in `services/` or `hosts/` links to other pages with paths relative to its folder: `../hosts/media.md`,
`../services/plex.md` and `sonarr.md`. Once the page is moved to `archive/services/` or `archive/hosts/`,
those paths point inside `archive/`. `relink` rewrites only the links the templates write, on the lines
they write them on, in the places they write those lines, and only before the notes block; everything else,
line endings included, stays as it is.
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


# The lines with generated links, by the place on the page they are in: how each line is written, and the
# folder of each link's page, in the order of the links.
_LINES: dict[str, list[tuple[re.Pattern[str], tuple[str, ...]]]] = {
    "service facts": [
        (re.compile(rf"\| Runs on \| {_link('a', 'hosts')} \|"), ("hosts",)),
        (re.compile(rf"\| Is the Docker host \| {_link('a', 'hosts')} \|"), ("hosts",)),
    ],
    "connections": [
        (re.compile(rf"- Depends on {_link('a', None, _TEXT)}"), ("services",)),
        (re.compile(rf"- Used by {_link('a', None, _TEXT)}"), ("services",)),
    ],
    "host facts": [
        (
            re.compile(rf"\| Runs as \| (?:VM|LXC) {_link('a', 'services')} on {_link('b', 'hosts')} \|"),
            ("services", "hosts"),
        ),
    ],
    "running here": [(re.compile(rf"\| {_link('a', 'services')} \|.*"), ("services",))],
}

# The lines that open and close the places, as the templates write them.
_FACTS = ["| | |", "| --- | --- |"]
_RUNNING = [
    "## Running here",
    "",
    "| Service | Status | Address | What it does |",
    "| --- | --- | --- | --- |",
]
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")

Where = Callable[[str, str], str | None]
"""Where the page `<folder>/<name>.md` is now, below the output folder (`hosts/media.md`,
`archive/services/plex-2.md`), or None when it is nowhere."""


def _fenced(lines: list[str]) -> list[bool]:
    """For each line, whether it is part of a fenced code block (its fences included), as Markdown reads it
    at the top level of the page: from a line of three or more backticks or tildes (up to three spaces
    before them; after backticks, no backtick) to a line of at least as many of the same, or the end."""
    inside: list[bool] = []
    fence = ""
    for line in lines:
        m = _FENCE.match(line)
        if not fence:
            if m and not (m.group(1)[0] == "`" and "`" in line[m.end() :]):
                fence = m.group(1)
            inside.append(bool(fence))
            continue
        inside.append(True)
        if (
            m
            and m.group(1)[0] == fence[0]
            and len(m.group(1)) >= len(fence)
            and not line[m.end() :].strip(" \t")
        ):
            fence = ""
    return inside


def _places(lines: list[str], folder: str) -> dict[int, str]:
    """The lines (by number) in the places the templates write generated links, and which place each is in.

    Every page starts with a table of facts (a `| | |` line, then `| --- | --- |`); its rows are the lines
    after it up to a blank line. On a service page, the "Depends on" and "Used by" lines are the list after
    the `## Connections` heading and a blank line, below that table and above the first `## How to restart`
    after it, up to a blank line. On a host page, the "Running here" table is the rows after the
    `## Running here` heading, a blank line and the table's two header lines, below the facts table, up to a
    blank line. Text from the lab (a description, a Proxmox note) can hold lines like these, so a place is
    never guessed: one whose opening lines are on the page more than once is none of these places, and when
    the facts table is not there exactly once, no line is. A line in a fenced code block is in none of these
    places and opens or ends none of them. (A title with a line break goes on over two lines; the lines after
    it stay in place.)
    """
    fenced = _fenced(lines)

    def free(i: int) -> bool:
        return 0 <= i < len(lines) and not fenced[i]

    def at(block: list[str], i: int) -> bool:
        return all(free(i + k) and lines[i + k] == line for k, line in enumerate(block))

    def only(block: list[str]) -> int | None:
        """Where the lines of `block` are on the page, outside fenced code, if they are there exactly once."""
        found = [i for i in range(len(lines)) if at(block, i)]
        return found[0] if len(found) == 1 else None

    def block(start: int) -> range:
        end = start
        while free(end) and lines[end].strip(" \t"):
            end += 1
        return range(start, end)

    places: dict[int, str] = {}
    top = only(_FACTS)
    if top is None:
        return places
    facts = block(top + len(_FACTS))
    places.update(dict.fromkeys(facts, "service facts" if folder == "services" else "host facts"))
    if folder == "services":
        stop = next((i for i in range(facts.stop, len(lines)) if at(["## How to restart"], i)), None)
        heading = only(["## Connections", ""])
        if stop is not None and heading is not None and facts.stop <= heading < stop:
            places.update((i, "connections") for i in block(heading + 2) if i < stop)
    elif folder == "hosts":
        heading = only(_RUNNING)
        if heading is not None and heading >= facts.stop:
            places.update(dict.fromkeys(block(heading + len(_RUNNING)), "running here"))
    return places


def _fix_line(line: str, place: str, where: Where) -> str:
    for pattern, folders in _LINES[place]:
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
    "Depends on", "Used by", "Runs as" and the rows of a host's table), in the place the template writes
    that line (`_places`, never in a fenced code block), and only when it names a page the way HomelabScribe
    names one. Such a link then points at `../../<where it is now>`, or, when its page is nowhere, becomes
    its text, exactly as written between the brackets. A line whose link text Markdown would not read as
    one piece (a title with a line break, or with a `|` in a table) is left as it is. Line endings (`\\n` or
    `\\r\\n`) stay as they were. A link changed once never matches again, so a second call changes nothing.
    """
    head, start, rest = text.partition(notes.START)
    if not start:
        return text
    lines = head.split("\n")
    bodies = [line.removesuffix("\r") for line in lines]
    for i, place in _places(bodies, folder).items():
        lines[i] = _fix_line(bodies[i], place, where) + lines[i][len(bodies[i]) :]
    return "\n".join(lines) + start + rest
