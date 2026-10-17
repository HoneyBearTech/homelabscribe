"""Optional notifications (Pro): tell the owner what changed, or what could not be read, after a scan.

Opt-in only. The one request goes to the ntfy or Discord address the owner put in their own config,
never anywhere else, and only after a scan that has something to say.
"""

from __future__ import annotations

import json
from pathlib import Path

from homelabscribe.config import NotifyConfig
from homelabscribe.diff import Diff

MAX_LINES = 15
STATE_FILE = "notify-state.json"


def build_message(
    diff: Diff | None, errors: list[str], recovered: bool, on: list[str]
) -> tuple[str, str] | None:
    """(title, body) for what is worth saying, or None."""
    lines: list[str] = []
    if "errors" in on:
        lines += [f"Could not read: {e}" for e in errors]
        if recovered:
            lines.append("Every source can be read again.")
    if "changes" in on and diff and not diff.empty:
        lines += [f"{c.service}: {c.detail}" for c in diff.changes]
    if not lines:
        return None
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES] + [f"...and {len(lines) - MAX_LINES} more"]
    return "HomelabScribe", "\n".join(lines)


def _post(target: NotifyConfig, title: str, body: str) -> None:
    import requests  # imported lazily so tests don't need the network

    if target.type == "discord":
        r = requests.post(target.url, json={"content": f"**{title}**\n{body}"[:1900]}, timeout=10)
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


def notify_after_scan(
    targets: list[NotifyConfig], state_dir: Path, diff: Diff | None, errors: list[str], post=_post
) -> list[str]:
    """Notify when changes appear or the set of unreadable sources changes. Returns delivery failures."""
    state_path = state_dir / STATE_FILE
    try:
        known = set(json.loads(state_path.read_text()).get("errors", []))
    except (OSError, ValueError):
        known = set()
    current = set(errors)
    failed: list[str] = []
    for t in targets:
        new_errors = sorted(current - known)
        msg = build_message(diff, new_errors, bool(known) and not current, t.on)
        if msg:
            failed += send([t], *msg, post=post)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"errors": sorted(current)}))
    return failed
