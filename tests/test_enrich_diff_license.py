from datetime import date, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from homelabscribe import licensing
from homelabscribe.demo import demo_snapshots
from homelabscribe.diff import compare
from homelabscribe.enrich import enrich, image_base, image_owner, load_catalog
from homelabscribe.models import Service


@pytest.mark.parametrize(
    "image,base,owner",
    [
        ("lscr.io/linuxserver/sonarr:latest", "sonarr", "linuxserver"),
        ("postgres:16@sha256:abc", "postgres", ""),
        ("vaultwarden/server:1.32.0", "server", "vaultwarden"),
        ("ghcr.io/immich-app/immich-server:release", "immich-server", "immich-app"),
    ],
)
def test_image_names(image, base, owner):
    assert image_base(image) == base and image_owner(image) == owner


def test_enrich_catalog_labels_and_traefik():
    cat = load_catalog()
    s = Service(
        name="vw",
        host="a",
        kind="container",
        image="vaultwarden/server:1.32.0",
        labels={"traefik.http.routers.vw.rule": "Host(`vault.example`) && PathPrefix(`/`)"},
    )
    u = Service(
        name="x",
        host="a",
        kind="container",
        image="me/custom",
        labels={
            "homelabscribe.name": "Family Photos",
            "homelabscribe.category": "Photos",
            "homelabscribe.hidden": "true",
        },
    )
    enrich([s, u], cat)
    assert s.title == "Vaultwarden" and s.category == "Security" and s.url == "https://vault.example"
    assert u.title == "Family Photos" and u.category == "Photos" and u.hidden


def test_diff_detects_added_changed_moved_removed():
    old, new = demo_snapshots()
    kinds = {(c.kind, c.service) for c in compare(old, new).changes}
    assert ("added", "overseerr") in kinds
    assert any(k == "changed" and s == "grafana" for k, s in kinds)

    moved = new.model_copy(deep=True)
    next(s for s in moved.services if s.name == "plex").host = "pve1"
    moved.services = [s for s in moved.services if s.name != "pbs"]
    changes = compare(new, moved).changes
    assert [c.kind for c in changes] == ["moved", "removed"]


def _keys():
    priv = Ed25519PrivateKey.generate()
    import base64

    pub = base64.urlsafe_b64encode(priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    return priv, pub


def test_license_roundtrip_and_tamper():
    priv, pub = _keys()
    key = licensing.sign("a@example.com", "pro", None, priv)
    lic = licensing.verify(key, pub)
    assert lic.email == "a@example.com" and lic.active
    feats, status = licensing.features(key, pub)
    assert "changelog" in feats and "lifetime" in status

    head, payload, sig = key.split(".")
    forged = ".".join([head, payload[:-2] + "AA", sig])
    with pytest.raises(licensing.LicenseError):
        licensing.verify(forged, pub)


def test_expired_and_missing_license_fall_back_to_free():
    priv, pub = _keys()
    key = licensing.sign("a@example.com", "pro", date.today() - timedelta(days=1), priv)
    feats, status = licensing.features(key, pub)
    assert feats == frozenset() and "expired" in status
    assert licensing.features(None, pub)[0] == frozenset()
    assert licensing.features("garbage", pub)[0] == frozenset()


@pytest.mark.parametrize(
    "key",
    [
        "LS1.x.y",  # not base64
        "LS1.\u00e9.\u00e9",  # not ASCII
        "LS1.eyJhIjox.\u00e9",  # the signature does not decode
        "LS1.\u00e9.AAAA",  # the payload does not decode
        "LS1.AAAAA.AAAA",  # five characters: one more than a multiple of four
        "LS1.\ud800.\ud800",  # a lone surrogate
        "LS1.eyJhbWFpbCI6.AAAAA",  # cut off in the signature
        "  LS1.x.y\n",
    ],
)
def test_undecodable_key_is_a_license_error(key):
    with pytest.raises(licensing.LicenseError, match="^Malformed license key\\.$"):
        licensing.verify(key)
    assert licensing.features(key) == (frozenset(), "Free edition (Malformed license key.)")


def _signed(payload: bytes, priv) -> str:
    return f"LS1.{licensing._b64e(payload)}.{licensing._b64e(priv.sign(payload))}"


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"\xff\xfe",
        b"[1, 2]",
        b'"a@example.com"',
        b"null",
        b"{}",
        b'{"tier": "pro", "expires": null}',
        b'{"email": 1, "expires": null}',
        b'{"email": "a@example.com", "tier": ["pro"]}',
        b'{"email": "a@example.com", "expires": 20271017}',
        b'{"email": "a@example.com", "expires": "next year"}',
        b'{"email": "a@example.com", "expires": "2027-13-40"}',
        b"[" * 100_000 + b"]" * 100_000,
    ],
)
def test_signed_payload_of_the_wrong_shape_is_a_license_error(payload):
    priv, pub = _keys()
    key = _signed(payload, priv)
    with pytest.raises(licensing.LicenseError, match="^Malformed license key\\.$"):
        licensing.verify(key, pub)
    assert licensing.features(key, pub) == (frozenset(), "Free edition (Malformed license key.)")


def test_valid_keys_read_as_before():
    priv, pub = _keys()
    assert licensing.features(licensing.sign("a@example.com", "pro", date(2999, 1, 2), priv), pub) == (
        licensing.PRO_FEATURES,
        "Pro \u2014 licensed to a@example.com until 2999-01-02",
    )
    lic = licensing.verify(_signed(b'{"email": "a@example.com"}', priv), pub)  # tier and expiry optional
    assert (lic.email, lic.tier, lic.expires) == ("a@example.com", "pro", None)


def test_same_catalog_name_on_one_host_gets_distinct_titles():
    svcs = [
        Service(name="sonarr", host="h", kind="container", image="lscr.io/linuxserver/sonarr:latest"),
        Service(name="sonarr4k", host="h", kind="container", image="lscr.io/linuxserver/sonarr:latest"),
        Service(name="sonarr", host="other", kind="container", image="lscr.io/linuxserver/sonarr:latest"),
    ]
    enrich(svcs, load_catalog())
    assert [s.title for s in svcs] == ["Sonarr", "Sonarr (sonarr4k)", "Sonarr"]
