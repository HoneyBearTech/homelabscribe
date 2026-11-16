"""Property-based (fuzz) tests for the parsers that handle untrusted-ish input.

Hypothesis feeds random and malformed data to the pure parse functions. They must never leak
secret values and must either return a result or fail with a narrow, expected error.
"""

from __future__ import annotations

import base64
import re

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from homelabscribe import licensing, notes
from homelabscribe.bundle import CONFIG_MODELS, redact, unknown_keys
from homelabscribe.collectors.docker import parse_container, parse_depends_on, parse_ports
from homelabscribe.collectors.proxmox import parse_guest
from homelabscribe.htmlsite import page_html
from homelabscribe.redact import clean_labels, env_names
from homelabscribe.relink import relink
from homelabscribe.renderer import tag_text, yaml_text

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


BUNDLE_MARKER = "LEAK-SENTINEL-"
marked_text = st.builds(
    lambda scheme, rest: f"{scheme}{BUNDLE_MARKER}{rest}", st.sampled_from(["", "tcp://", "https://"]), text
)
marked_value = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | marked_text,
    lambda c: st.lists(c, max_size=4) | st.dictionaries(marked_text, c, max_size=4),
    max_leaves=12,
)
config_keys = st.sampled_from(sorted({name for model in CONFIG_MODELS for name in model.model_fields}))


@PROFILE
@given(marked_value, st.lists(st.tuples(config_keys, st.booleans()), max_size=3))
def test_bundle_redaction_never_returns_a_marker(value, path):
    """No text of the config survives the redaction of a bug-report bundle, as a key or as a value.

    The value is tried as it is and placed under real config keys, where a rule might keep something.
    """
    placed = value
    for key, in_list in reversed(path):
        placed = {key: [placed] if in_list else placed}
    for data in (value, placed):
        assert BUNDLE_MARKER not in repr(redact(data))
        assert unknown_keys(data) >= 0


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


notes_text = st.lists(
    st.sampled_from([notes.START, notes.END, notes.PLACEHOLDER, "\n"]) | text, max_size=8
).map("".join)


@PROFILE
@given(notes_text, notes_text)
def test_notes_merge_never_fails_and_keeps_real_notes(rendered, previous):
    out = notes.merge(rendered, previous)
    kept = notes.extract(previous)
    if kept is not None and kept.strip() not in ("", notes.PLACEHOLDER):
        assert kept in out


def _block_as_the_templates_write_it(rendered: str) -> bool:
    """True when the page has no notes block, or one that holds the placeholder as every template writes it.

    A rendered block that holds anything else is filled in a different form when the page is merged with
    itself (see the test below), which no template does; that case is kept apart from the stray markers.
    """
    head, start, rest = rendered.partition(notes.START)
    body, end, _ = rest.partition(notes.END)
    return not (start and end) or body == f"\n{notes.PLACEHOLDER}\n"


@PROFILE
@given(
    (
        notes_text
        | st.tuples(notes_text, notes_text).map(
            lambda ab: f"{ab[0]}{notes.START}\n{notes.PLACEHOLDER}\n{notes.END}{ab[1]}"
        )
    ).filter(_block_as_the_templates_write_it),
    notes_text,
)
def test_notes_merge_twice_gives_the_same_page(rendered, previous):
    once = notes.merge(rendered, previous)
    assert notes.merge(rendered, once) == once


@pytest.mark.xfail(
    strict=True, reason="a rendered block that is not the template's form is filled differently"
)
def test_notes_merge_twice_with_a_block_the_templates_never_write():
    rendered = f"{notes.START}{notes.END}"  # no notes to keep, so the page is written as it is
    once = notes.merge(rendered, "")
    assert notes.merge(rendered, once) == once  # the second merge writes the block as START, "", END


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


license_keys = (
    st.text(max_size=300)
    | st.builds("LS1.{}.{}".format, st.text(max_size=60), st.text(max_size=60))
    | st.builds("LS1.{}.{}".format, st.binary(max_size=120).map(_b64), st.binary(max_size=80).map(_b64))
)


@PROFILE
@given(license_keys)
def test_license_verify_rejects_garbage_cleanly(key):
    with pytest.raises(licensing.LicenseError):
        licensing.verify(key)


page_pieces = st.sampled_from(
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        '<a href="javascript:alert(1)" onclick="x">',
        "<style>*{display:none}</style><base href=//evil.test><meta http-equiv=refresh content=0>",
        "<iframe src=//evil.test></iframe><object data=x></object><embed src=x><form action=x><link rel=x>",
        "[a](javascript:alert(1))",
        "[a](data:text/html;base64,PHNjcmlwdD4=)",
        "[a](//evil.test/x)",
        "[a](ftp://evil.test)",
        "[a](<java\tscript:alert(1)>)",
        "[a]( vbscript:x )",
        "[a](http://ok.test/x)",
        "[a](../services/plex.md#top)",
        "[a]: javascript:alert(1)\n",
        "![i](//evil.test/pixel.png)",
        "![i](https://evil.test/pixel.png)",
        "![i](data:image/png;base64,AAAA)",
        "![i](pixel.png 'title')",
        "<javascript:alert(1)>",
        "<https://ok.test>",
        "---\n",
        "title: ",
        "<!-- homelabscribe:notes:start -->",
        "<!-- homelabscribe:notes:end -->",
        "```mermaid\n",
        "```\n",
        "| a | b |\n| :-: | --: |\n| 1 | 2 |\n",
        "# ",
        "> ",
        "\n",
        "\n\n",
        "&lt;",
        "&#x3C;script&#x3E;",
        "\\",
        '"',
        "'",
        "<",
        ">",
        "`",
        "(",
        ")",
        "[",
        "]",
        "!",
    ]
)


@PROFILE
@given(
    st.lists(page_pieces | st.text(max_size=20), max_size=12).map("".join),
    st.sampled_from(["index.md", "services/page.md", "archive/services/page.md"]),  # three depths
)
def test_html_page_carries_no_markup_from_the_page_text(source, rel):
    """Whatever a page holds, its HTML copy has no element or address that could run or fetch anything."""
    from html.parser import HTMLParser
    from urllib.parse import urlsplit

    def scheme_and_host(address: str) -> tuple[str, str]:
        plain = address.strip().replace("\\", "/")
        try:
            parts = urlsplit(plain)
            return parts.scheme.lower(), parts.netloc
        except ValueError:  # such as a host that is cut off: the scheme is still what a browser would see
            return plain.partition(":")[0].lower(), "?"

    found: list[tuple[str, dict]] = []

    class Elements(HTMLParser):
        def handle_starttag(self, tag, attrs):
            found.append((tag, dict(attrs)))

    Elements().feed(page_html(source, rel, frozenset({"services/plex.md", "index.md"})))
    tags = [tag for tag, _ in found]
    # The template's own head, then nothing but what the converter makes from Markdown.
    assert tags[:9] == ["html", "head", "meta", "meta", "meta", "meta", "title", "style", "body"]
    for tag, attrs in found[9:]:
        assert tag not in {"script", "style", "iframe", "object", "embed", "form", "base", "meta", "link"}
        assert not any(name.startswith("on") for name in attrs)
        assert "style" not in attrs  # no element made from the page text carries a style attribute
        if "href" in attrs:
            scheme, host = scheme_and_host(attrs["href"] or "")
            assert scheme in ("http", "https", "mailto") or (scheme == "" and host == "")
        if "src" in attrs:
            assert scheme_and_host(attrs["src"] or "") == ("", "")
            assert not (attrs["src"] or "").replace("\\", "/").startswith("/")


_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
LOADERS = [yaml.SafeLoader] + ([yaml.CSafeLoader] if yaml.__with_libyaml__ else [])
any_text = st.text(alphabet=st.characters(codec=None), max_size=40)


@PROFILE
@given(any_text)
def test_front_matter_value_is_one_line_and_reads_back_as_the_same_text(value):
    """The text is written on one line and read back unchanged; a lone surrogate, which cannot be part of
    a text file, comes back as U+FFFD. PyYAML's pure-Python and libyaml loaders agree.
    """
    out = yaml_text(value)
    want = _LONE_SURROGATE.sub("\ufffd", value)
    assert len(out.splitlines()) == 1 and "\n" not in out and "\r" not in out
    out.encode("utf-8")  # writable to a file
    for loader in LOADERS:
        assert yaml.load(f"key: {out}", Loader=loader) == {"key": want}
        assert yaml.load(f"[a, {out}]", Loader=loader) == ["a", want]


@PROFILE
@given(any_text)
def test_front_matter_tag_reads_back_as_the_same_text_in_the_tags_list(category):
    tag = tag_text(category)
    assert tag and not _LONE_SURROGATE.search(tag)
    for loader in LOADERS:
        assert yaml.load(f"tags: [homelab, {yaml_text(tag)}]", Loader=loader) == {"tags": ["homelab", tag]}


link_pieces = st.sampled_from(
    [
        "| Runs on | ",
        "| Is the Docker host | ",
        "| Runs as | VM ",
        "| Runs as | LXC ",
        " on ",
        "- Depends on ",
        "- Used by ",
        "| ",
        " |",
        " | running |  | x |",
        "[media](../hosts/media.md)",
        "[A [beta]](../services/a-beta.md)",
        "[Plex](plex.md)",
        "[a](../../hosts/media.md)",
        "[",
        "]",
        "(",
        ")",
        "](",
        "../hosts/",
        "../services/",
        "../../",
        "media",
        "a-beta",
        "x-2",
        "Plex",
        "a" * 201,
        ".md",
        ".md)",
        "\\",
        "|",
        "\n",
        "\r\n",
        "\r",
        notes.START,
        notes.END,
        "\\]",
        "\\",
        "\\|",
    ]
)
link_folders = st.sampled_from(["services", "hosts"])
link_text = st.lists(link_pieces | text, max_size=4).map("".join)
link_names = st.sampled_from(["media", "a-beta", "plex", "x-2", "a" * 201, "Plex", "a--b"])
# The lines the templates write links on, with any text and name in them, each with a line ending.
link_lines = st.builds(
    lambda form, a, x, b, y, end: form.format(a=a, x=x, b=b, y=y) + end,
    st.sampled_from(
        [
            "| Runs on | [{a}](../hosts/{x}.md) |",
            "| Is the Docker host | [{a}](../hosts/{x}.md) |",
            "- Depends on [{a}]({x}.md)",
            "- Used by [{a}]({x}.md)",
            "| Runs as | VM [{a}](../services/{x}.md) on [{b}](../hosts/{y}.md) |",
            "| [{a}](../services/{x}.md) | {b} | {y} |",
        ]
    ),
    link_text,
    link_names,
    link_text,
    link_names,
    st.sampled_from(["\n", "\r\n"]),
)
link_targets = st.dictionaries(
    st.tuples(link_folders, link_names),
    st.sampled_from(
        ["hosts/media.md", "services/plex.md", "archive/services/a-beta-2.md", "archive/hosts/x.md"]
    ),
    max_size=6,
)


link_page = st.lists(link_lines | link_pieces | text, max_size=24).map("".join)


@PROFILE
@given(
    link_page | st.tuples(link_page, link_page).map(lambda ab: f"{ab[0]}{notes.START}\n{ab[1]}"),
    link_folders,
    link_targets,
)
def test_relink_never_fails_keeps_the_notes_and_changes_nothing_twice(page, folder, targets):
    """Whatever the page holds, fixing its links never fails, leaves everything from the notes start marker
    on and every line ending as it was, and finds nothing more to fix in its own result."""

    def where(target_folder, name):
        return targets.get((target_folder, name))

    out = relink(page, folder, where)
    head, start, rest = page.partition(notes.START)
    if not start:
        assert out == page
    assert out.endswith(start + rest)
    assert [line[-1:] == "\r" for line in out.split("\n")] == [line[-1:] == "\r" for line in page.split("\n")]
    assert out.count("\r") == page.count("\r")
    assert relink(out, folder, where) == out
    out.encode("utf-8")  # writable to a file
