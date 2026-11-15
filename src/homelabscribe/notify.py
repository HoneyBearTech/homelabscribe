"""Optional notifications (Pro): tell the owner what changed, or what could not be read, after a scan.

Opt-in only. The one request goes to the ntfy or Discord address the owner put in their own config,
never anywhere else, and only after a scan that has something to say.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Sequence
from pathlib import Path

from homelabscribe import fsutil
from homelabscribe.config import NotifyConfig
from homelabscribe.diff import Diff

MAX_LINES = 15
STATE_FILE = "notify-state.json"
# Discord takes 2000 characters for `**title**\nbody`; ntfy takes 4096 bytes. The body is cut at a whole line
# to stay under both: 1900 characters leaves room for the title, 3000 bytes is well under ntfy's limit.
MAX_CHARS = 1900
MAX_BYTES = 3000


_ESCAPED_CATEGORIES = {
    "Cc",
    "Cf",
    "Zl",
    "Zp",
}  # controls, format (bidi, zero-width) and line/paragraph separators
_DISCORD_MARKUP = set("\\*_~`|>[]()<")


def _path(skip: str) -> str:
    """The file a skip is about: `Skip.path`, or the whole text for a plain string."""
    return getattr(skip, "path", None) or str(skip)


def _encodable(text: str) -> str:
    """A name that is not UTF-8 holds lone surrogates; they show as `\\udcff` so the text can be sent."""
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _plain(text: str) -> str:
    """Always-encodable, one-line text: lone surrogates and every control, format and separator is escaped."""
    out = []
    for ch in _encodable(text):
        o = ord(ch)
        if ch in "\n\r\t":
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}[ch])
        elif unicodedata.category(ch) in _ESCAPED_CATEGORIES:
            out.append(f"\\x{o:02x}" if o < 0x100 else f"\\u{o:04x}")
        else:
            out.append(ch)
    return "".join(out)


def _discord(text: str) -> str:
    return "".join("\\" + ch if ch in _DISCORD_MARKUP else ch for ch in text)


def _skip_line(skip: str, discord: bool) -> str:
    """`Skipped: <path> (<reason>)`, the name and reason made safe as text (and for Discord, markup)."""
    path = _path(skip)
    rest = str(skip)[len(path) :] if str(skip).startswith(path) else ""  # ` (<reason>)`, or nothing
    name, reason = _plain(path), _plain(rest)
    if discord:
        name = _discord(name)
        if reason.startswith(" (") and reason.endswith(
            ")"
        ):  # the brackets around the reason stay as they are
            reason = " (" + _discord(reason[2:-1]) + ")"
        else:
            reason = _discord(reason)
    return f"Skipped: {name}{reason}"


def _fits(text: str) -> bool:
    return len(text) <= MAX_CHARS and len(text.encode("utf-8")) <= MAX_BYTES


def _trim(lines: list[str]) -> str:
    """At most MAX_LINES whole lines that fit the size budget, then `...and N more` for all left out."""
    lines = [_encodable(ln) for ln in lines]  # whatever the source of a line, it can be encoded
    for keep in range(min(len(lines), MAX_LINES), -1, -1):
        shown = lines[:keep]
        if keep < len(lines):
            shown = shown + [f"...and {len(lines) - keep} more"]
        body = "\n".join(shown)
        if _fits(body):
            return body
    return f"...and {len(lines)} more"  # unreachable in practice: this line alone always fits


def build_message(
    diff: Diff | None,
    errors: list[str],
    recovered: bool,
    on: list[str],
    skips: Sequence[str] = (),
    skips_recovered: bool = False,
    kind: str = "ntfy",
) -> tuple[str, str] | None:
    """(title, body) for what is worth saying, or None. The title is fixed; file names go in the body."""
    lines: list[str] = []
    if "errors" in on:
        lines += [f"Could not read: {e}" for e in errors]
        if recovered:
            lines.append("Every source can be read again.")
        lines += [_skip_line(s, kind == "discord") for s in skips]
        if skips_recovered:
            lines.append("Every skipped file can be read again.")
    if "changes" in on and diff and not diff.empty:
        lines += [f"{c.service}: {c.detail}" for c in diff.changes]
    if not lines:
        return None
    return "HomelabScribe", _trim(lines)


def _post(target: NotifyConfig, title: str, body: str) -> None:
    import requests  # imported lazily so tests don't need the network

    if target.type == "discord":
        r = requests.post(
            target.url,
            json={"content": f"**{title}**\n{body}", "allowed_mentions": {"parse": []}},
            timeout=10,
        )
    else:
        headers = {"Title": title}
        if target.token:
            headers["Authorization"] = f"Bearer {target.token}"
        r = requests.post(target.url, data=body.encode(), headers=headers, timeout=10)
    r.raise_for_status()


def send(targets: list[NotifyConfig], title: str, body: str, post=_post) -> list[str]:
    """Send to every target; returns what failed (type only, so no address or token is echoed)."""
    failed = []
    for t in targets:
        try:
            post(t, title, body)
        except Exception as e:
            failed.append(f"{t.type}: {type(e).__name__}")
    return failed


def _known(state_path: Path, key: str) -> set[str]:
    """What the last run reported under `key`. A file that cannot be read or has another shape is empty."""
    try:
        data = json.loads(fsutil.read_text(state_path))
        items = data.get(key, []) if isinstance(data, dict) else []
        return {e for e in items if isinstance(e, str)} if isinstance(items, list) else set()
    except (OSError, ValueError):
        return set()


def notify_after_scan(
    targets: list[NotifyConfig],
    state_dir: Path,
    diff: Diff | None,
    errors: list[str],
    skips: Sequence[str] = (),
    post=_post,
    render_complete: bool = True,
) -> list[str]:
    """Notify when changes appear or the set of unreadable sources or skipped files changes.

    `skips` are the files left alone in this round (`Skip` texts). Each is sent once, until it goes away.
    A round whose render did not complete (`render_complete=False`) has only some of its skips: the new
    ones are sent, but none is dropped from the state and no recovery is announced. Returns delivery failures.
    """
    state_path = state_dir / STATE_FILE
    known = _known(state_path, "errors")
    known_skips = _known(state_path, "skips")
    current = set(errors)
    by_path: dict[str, str] = {}
    for s in skips:
        by_path.setdefault(_path(s), s)
    current_skips = set(by_path)
    if not render_complete:
        current_skips |= known_skips
    new_skips = [by_path[p] for p in sorted(set(by_path) - known_skips)]
    failed: list[str] = []
    for t in targets:
        new_errors = sorted(current - known)
        msg = build_message(
            diff,
            new_errors,
            bool(known) and not current,
            t.on,
            new_skips,
            render_complete and bool(known_skips) and not current_skips,
            t.type,
        )
        if msg:
            failed += send([t], *msg, post=post)
    state_dir.mkdir(parents=True, exist_ok=True)
    state = {"errors": sorted(current), "skips": sorted(current_skips)}
    try:
        fsutil.atomic_write(state_path, json.dumps(state).encode("utf-8"))
    except OSError:
        failed.append("state file: could not be written")
    return failed
