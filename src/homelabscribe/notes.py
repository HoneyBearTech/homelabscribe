"""Human notes survive every regeneration.

Each generated page has a block between these markers. HomelabScribe rewrites everything outside it
and copies whatever is inside from the previous version of the file.
"""

from __future__ import annotations

START = "<!-- homelabscribe:notes:start -->"
END = "<!-- homelabscribe:notes:end -->"
MARKER_TOKEN = "homelabscribe:notes:"  # part of both markers: a text without it cannot hold one
PLACEHOLDER = "_Your notes go here. HomelabScribe never overwrites this section._"


def extract(text: str) -> str | None:
    if START not in text or END not in text:
        return None
    body = text.split(START, 1)[1].split(END, 1)[0]
    return body.strip("\n")


def _around_block(text: str) -> tuple[str, str] | None:
    """The text before the first start marker and after the first end marker that follows it, or None."""
    head, start, rest = text.partition(START)
    _, end, tail = rest.partition(END)
    return (head, tail) if start and end else None


def _without_markers(text: str) -> str:
    """The text with every start and end marker removed, including one that a removal brings together."""
    while START in text or END in text:
        text = text.replace(START, "").replace(END, "")
    return text


def merge(rendered: str, previous: str | None) -> str:
    """Put the previous file's notes into the freshly rendered page.

    A page rendered without a notes block (a start marker followed by an end marker) keeps the notes in
    a notes block added at its end. Every start and end marker is first removed from that page, so the
    result holds exactly one notes block, or none when there are no notes to keep.
    """
    kept = extract(previous) if previous is not None else None
    has_notes = kept is not None and kept.strip() != PLACEHOLDER
    around = _around_block(rendered)
    if around is None:
        page = _without_markers(rendered)
        if not has_notes or not kept.strip():
            return page
        blank = "\n" * max(0, 2 - (len(page) - len(page.rstrip("\n")))) if page else ""
        return f"{page}{blank}## Notes\n\n{START}\n{kept}\n{END}\n"
    if not has_notes:
        return rendered
    head, tail = around
    return f"{head}{START}\n{kept}\n{END}{tail}"
