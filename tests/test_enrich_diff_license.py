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


def test_same_catalog_name_on_one_host_gets_distinct_titles():
    svcs = [
        Service(name="sonarr", host="h", kind="container", image="lscr.io/linuxserver/sonarr:latest"),
        Service(name="sonarr4k", host="h", kind="container", image="lscr.io/linuxserver/sonarr:latest"),
        Service(name="sonarr", host="other", kind="container", image="lscr.io/linuxserver/sonarr:latest"),
    ]
    enrich(svcs, load_catalog())
    assert [s.title for s in svcs] == ["Sonarr", "Sonarr (sonarr4k)", "Sonarr"]
