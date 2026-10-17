"""Human notes survive every regeneration.

Each generated page has a block between these markers. HomelabScribe rewrites everything outside it
and copies whatever is inside from the previous version of the file.
"""

from __future__ import annotations

START = "<!-- homelabscribe:notes:start -->"
END = "<!-- homelabscribe:notes:end -->"
PLACEHOLDER = "_Your notes go here. HomelabScribe never overwrites this section._"


def extract(text: str) -> str | None:
    if START not in text or END not in text:
        return None
    body = text.split(START, 1)[1].split(END, 1)[0]
    return body.strip("\n")


def merge(rendered: str, previous: str | None) -> str:
    """Put the previous file's notes into the freshly rendered page."""
    if previous is None:
        return rendered
    kept = extract(previous)
    if kept is None or kept.strip() == PLACEHOLDER:
        return rendered
    head, rest = rendered.split(START, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{START}\n{kept}\n{END}{tail}"
