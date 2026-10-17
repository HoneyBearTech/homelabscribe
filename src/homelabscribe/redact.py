"""Keep secrets out of snapshots. Environment values are never stored; risky labels are dropped."""

from __future__ import annotations

import re

SENSITIVE = re.compile(
    r"(pass|secret|token|key|private|credential|auth|cookie|session|salt|apikey|api_key)",
    re.IGNORECASE,
)

# Labels that are noise in docs.
NOISY_PREFIXES = ("org.opencontainers.", "maintainer", "build_version", "com.docker.compose.config")


def env_names(env: list[str] | None) -> list[str]:
    """Return variable NAMES only from ["KEY=value", ...]. Values are discarded."""
    names = {item.split("=", 1)[0] for item in env or [] if item}
    return sorted(names)


def is_sensitive(name: str) -> bool:
    return bool(SENSITIVE.search(name))


def clean_labels(labels: dict[str, str] | None) -> dict[str, str]:
    """Drop labels whose key or value looks like a secret (e.g. basic-auth hashes)."""
    out: dict[str, str] = {}
    for k, v in (labels or {}).items():
        if k.startswith(NOISY_PREFIXES):
            continue
        if is_sensitive(k.split(".")[-1]) and "homelabscribe." not in k:
            continue
        if "$apr1$" in v or "$2y$" in v or "$2a$" in v:  # htpasswd hashes
            continue
        out[k] = v
    return out
