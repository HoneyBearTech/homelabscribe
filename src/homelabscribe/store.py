"""Snapshots are plain JSON files, one per scan, so they're easy to inspect, diff and back up."""

from __future__ import annotations

import json
from pathlib import Path

from homelabscribe.models import Snapshot

KEEP = 60  # roughly two months of daily scans


def _dir(state_dir: Path) -> Path:
    d = state_dir / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(snapshot: Snapshot, state_dir: Path) -> Path:
    path = _dir(state_dir) / f"{snapshot.taken_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(snapshot.model_dump_json(indent=2))
    for old in sorted(_dir(state_dir).glob("*.json"))[:-KEEP]:
        old.unlink()
    return path


def recent(state_dir: Path, n: int = 2) -> list[Snapshot]:
    """Newest first."""
    files = sorted(_dir(state_dir).glob("*.json"), reverse=True)[:n]
    return [Snapshot.model_validate_json(f.read_text()) for f in files]


def load_changelog(state_dir: Path) -> list[dict]:
    path = state_dir / "changelog.json"
    return json.loads(path.read_text()) if path.exists() else []


def save_changelog(entries: list[dict], state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "changelog.json").write_text(json.dumps(entries[-500:], indent=2))
