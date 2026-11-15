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


def _around_block(text: str) -> tuple[str, str] | None:
    """The text before the first start marker and after the first end marker that follows it, or None."""
    head, start, rest = text.partition(START)
    _, end, tail = rest.partition(END)
    return (head, tail) if start and end else None


def merge(rendered: str, previous: str | None) -> str:
    """Put the previous file's notes into the freshly rendered page.

    A page rendered without a notes block (a start marker followed by an end marker) keeps the notes in
    a notes block added at its end.
    """
    if previous is None:
        return rendered
    kept = extract(previous)
    if kept is None or kept.strip() == PLACEHOLDER:
        return rendered
    around = _around_block(rendered)
    if around is None:
        if not kept.strip():
            return rendered
        blank = "\n" * max(0, 2 - (len(rendered) - len(rendered.rstrip("\n")))) if rendered else ""
        return f"{rendered}{blank}## Notes\n\n{START}\n{kept}\n{END}\n"
    head, tail = around
    return f"{head}{START}\n{kept}\n{END}{tail}"
