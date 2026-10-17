"""Compare two snapshots and describe what changed in plain language."""

from __future__ import annotations

from pydantic import BaseModel, Field

from homelabscribe.models import Service, Snapshot


class Change(BaseModel):
    kind: str  # added | removed | moved | changed
    service: str  # display title
    detail: str


class Diff(BaseModel):
    since: str
    until: str
    changes: list[Change] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.changes


def _ports(s: Service) -> str:
    return ", ".join(p.label() for p in s.ports) or "none"


def compare(old: Snapshot, new: Snapshot) -> Diff:
    diff = Diff(since=old.taken_at.isoformat(), until=new.taken_at.isoformat())
    before = {s.id: s for s in old.services}
    after = {s.id: s for s in new.services}

    removed = {k: v for k, v in before.items() if k not in after}
    added = {k: v for k, v in after.items() if k not in before}

    # Same name, different host = moved, not removed + added.
    for rid, rs in list(removed.items()):
        for aid, as_ in list(added.items()):
            if rs.name == as_.name and rs.kind == as_.kind:
                diff.changes.append(
                    Change(kind="moved", service=as_.title, detail=f"moved from {rs.host} to {as_.host}")
                )
                del removed[rid], added[aid]
                break

    for s in added.values():
        diff.changes.append(Change(kind="added", service=s.title, detail=f"added on {s.host}"))
    for s in removed.values():
        diff.changes.append(Change(kind="removed", service=s.title, detail=f"removed from {s.host}"))

    for sid in sorted(before.keys() & after.keys()):
        a, b = before[sid], after[sid]
        if a.image != b.image:
            diff.changes.append(
                Change(kind="changed", service=b.title, detail=f"image {a.image} → {b.image}")
            )
        if a.status != b.status:
            diff.changes.append(
                Change(kind="changed", service=b.title, detail=f"status {a.status} → {b.status}")
            )
        if _ports(a) != _ports(b):
            diff.changes.append(
                Change(kind="changed", service=b.title, detail=f"ports {_ports(a)} → {_ports(b)}")
            )
        if {m.destination for m in a.mounts} != {m.destination for m in b.mounts}:
            diff.changes.append(Change(kind="changed", service=b.title, detail="storage mounts changed"))
        if a.networks != b.networks:
            diff.changes.append(
                Change(
                    kind="changed",
                    service=b.title,
                    detail=f"networks {', '.join(a.networks) or 'none'} → {', '.join(b.networks) or 'none'}",
                )
            )

    order = {"added": 0, "moved": 1, "changed": 2, "removed": 3}
    diff.changes.sort(key=lambda c: (order[c.kind], c.service.lower()))
    return diff
