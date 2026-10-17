"""Offline license keys. No phone-home, ever.

Key format:  LS1.<base64url(json payload)>.<base64url(ed25519 signature)>
Payload:     {"email": "...", "tier": "pro", "expires": "2027-10-17" | null}

You sign keys with your PRIVATE key (kept off GitHub; see tools/). This file only holds the
PUBLIC key, so publishing it is safe.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Public half of the license signing key (tools/make_keypair.py). Safe to publish.
PUBLIC_KEY_B64 = "RFI1KL1MFmdq-ON_sPJm6TVwIC5KXwI4ybhu-y2aG5s="

PRO_FEATURES = frozenset(
    {"multi_host", "proxmox", "changelog", "breakglass", "diagrams", "schedule", "notify"}
)


class LicenseError(ValueError):
    pass


@dataclass(frozen=True)
class License:
    email: str
    tier: str
    expires: date | None

    @property
    def active(self) -> bool:
        return self.expires is None or self.expires >= datetime.now(UTC).date()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def verify(key: str, public_key_b64: str = PUBLIC_KEY_B64) -> License:
    try:
        prefix, payload_b64, sig_b64 = key.strip().split(".")
    except ValueError as e:
        raise LicenseError("Malformed license key.") from e
    if prefix != "LS1":
        raise LicenseError("Unknown license key version.")
    try:
        pub = Ed25519PublicKey.from_public_bytes(_b64d(public_key_b64))
    except ValueError as e:
        raise LicenseError("This build has no license public key configured.") from e
    payload = _b64d(payload_b64)
    try:
        pub.verify(_b64d(sig_b64), payload)
    except InvalidSignature as e:
        raise LicenseError("License key signature is invalid.") from e
    data = json.loads(payload)
    exp = date.fromisoformat(data["expires"]) if data.get("expires") else None
    return License(email=data["email"], tier=data.get("tier", "pro"), expires=exp)


def sign(email: str, tier: str, expires: date | None, private_key) -> str:
    """Used by tools/issue_license.py and the payment webhook — never shipped keys."""
    payload = json.dumps(
        {"email": email, "tier": tier, "expires": expires.isoformat() if expires else None},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"LS1.{_b64e(payload)}.{_b64e(private_key.sign(payload))}"


def features(key: str | None, public_key_b64: str = PUBLIC_KEY_B64) -> tuple[frozenset[str], str]:
    """Return (enabled Pro features, human-readable status)."""
    if not key:
        return frozenset(), "Free edition"
    try:
        lic = verify(key, public_key_b64)
    except LicenseError as e:
        return frozenset(), f"Free edition ({e})"
    if not lic.active:
        return frozenset(), f"Free edition (Pro license for {lic.email} expired {lic.expires})"
    until = f" until {lic.expires}" if lic.expires else " (lifetime)"
    return PRO_FEATURES, f"Pro — licensed to {lic.email}{until}"
