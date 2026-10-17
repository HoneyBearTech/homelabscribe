"""Property-based (fuzz) tests for the parsers that handle untrusted-ish input.

Hypothesis feeds random and malformed data to the pure parse functions. They must never leak
secret values and must either return a result or fail with a narrow, expected error.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from homelabscribe import licensing, notes
from homelabscribe.collectors.docker import parse_container, parse_depends_on, parse_ports
from homelabscribe.collectors.proxmox import parse_guest
from homelabscribe.redact import clean_labels, env_names

text = st.text(max_size=40)
json_scalar = st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | text
json_value = st.recursive(
    json_scalar, lambda c: st.lists(c, max_size=4) | st.dictionaries(text, c, max_size=4), max_leaves=12
)

PROFILE = settings(max_examples=150, deadline=None)


@PROFILE
@given(st.lists(st.tuples(st.from_regex(r"[A-Z_]{1,12}", fullmatch=True), text), max_size=8))
def test_env_names_never_contains_values(pairs):
    marker = "VALUE-SENTINEL-"
    env = [f"{k}={marker}{v}" for k, v in pairs]
    assert all(marker not in n for n in env_names(env))


@PROFILE
@given(st.dictionaries(text, text, max_size=8))
def test_clean_labels_drops_htpasswd_hashes(labels):
    labels = {**labels, "traefik.http.middlewares.auth.basicauth.users": "admin:$apr1$abc$def"}
    cleaned = clean_labels(labels)
    assert all("$apr1$" not in v and "$2y$" not in v and "$2a$" not in v for v in cleaned.values())


@PROFILE
@given(st.text(max_size=80) | st.none())
def test_parse_depends_on_returns_sorted_unique(value):
    result = parse_depends_on(value)
    assert result == sorted(set(result))


@PROFILE
@given(
    st.dictionaries(
        st.from_regex(r"[0-9]{1,5}(/(tcp|udp))?", fullmatch=True),
        st.none()
        | st.lists(
            st.fixed_dictionaries(
                {}, optional={"HostIp": text, "HostPort": st.from_regex(r"[0-9]{1,5}", fullmatch=True)}
            ),
            max_size=3,
        ),
        max_size=5,
    )
)
def test_parse_ports_accepts_docker_shaped_input(raw):
    ports = parse_ports(raw)
    assert [(p.host or 0, p.container) for p in ports] == sorted((p.host or 0, p.container) for p in ports)


@PROFILE
@given(st.dictionaries(text, json_value, max_size=6))
def test_parse_container_only_fails_with_expected_errors(attrs):
    """Arbitrary junk may be rejected, but only through the errors a caller already handles."""
    try:
        parse_container(attrs, "host")
    except (ValueError, TypeError, AttributeError, KeyError):
        pass


@PROFILE
@given(st.lists(st.from_regex(r"[A-Z_]{1,8}=SECRET-[a-z0-9]{6,12}", fullmatch=True), max_size=5))
def test_parse_container_never_stores_env_values(env):
    svc = parse_container({"Name": "/x", "Config": {"Image": "i", "Env": env}}, "h")
    assert "SECRET-" not in svc.model_dump_json()


@PROFILE
@given(st.dictionaries(text, json_value, max_size=6), st.dictionaries(text, json_value, max_size=6))
def test_parse_guest_only_fails_with_expected_errors(item, cfg):
    try:
        parse_guest(item, cfg, "node", "vm")
    except (ValueError, TypeError, AttributeError, KeyError):
        pass


@PROFILE
@given(st.text(max_size=200), st.text(max_size=200))
def test_notes_merge_keeps_markers_and_notes(body, mine):
    rendered = f"head\n{notes.START}\n{notes.PLACEHOLDER}\n{notes.END}\ntail\n"
    previous = f"old\n{notes.START}\n{mine}\n{notes.END}\n"
    out = notes.merge(rendered, previous)
    assert notes.START in out and notes.END in out
    assert notes.extract(out) is not None


@PROFILE
@given(st.text(max_size=300))
def test_license_verify_rejects_garbage_cleanly(key):
    with pytest.raises(licensing.LicenseError):
        licensing.verify(key)
