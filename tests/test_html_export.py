"""The static HTML copy of the generated pages (config `html_dir`, `demo --html`).

Each test carries the label of the behaviour it shows (H1 to H13). The lab is the demo lab or a fake
Docker daemon: no network.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import socket
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest
from test_collectors import FakeDockerClient
from typer.testing import CliRunner

from homelabscribe import cli, htmlsite, licensing, notes, pipeline
from homelabscribe.cli import app
from homelabscribe.collectors import docker as docker_collector
from homelabscribe.config import Config
from homelabscribe.demo import demo_snapshots
from homelabscribe.enrich import enrich, load_catalog
from homelabscribe.models import Host, Route, Service, Snapshot
from homelabscribe.renderer import Renderer

runner = CliRunner()
VOID = {"meta", "img", "br", "hr"}


class Page(HTMLParser):
    """The elements of a page, in order, and whether its tags are balanced."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.open: list[str] = []
        self.balanced = True
        self.text: list[str] = []
        self.feed(html)
        self.close()
        self.balanced = self.balanced and not self.open

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag not in VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):  # `<hr />`: an element with no end tag
        self.tags.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if not self.open or self.open.pop() != tag:
            self.balanced = False

    def handle_data(self, data):
        self.text.append(data)

    def all(self, name: str) -> list[dict[str, str | None]]:
        return [attrs for tag, attrs in self.tags if tag == name]


def _body(html: str) -> str:
    return html.split("<main>", 1)[1].split("</main>", 1)[0]


def _convert(markdown: str, rel: str = "index.md", pages=()) -> str:
    return _body(htmlsite.page_html(markdown, rel, pages))


def _lab() -> Snapshot:
    _, new = demo_snapshots()
    enrich(new.services, load_catalog())
    new.routes = [Route(proxy="npm", domains=["plex.home.example"], forward_host="nova", forward_port=32400)]
    return new


def _render(out: Path, snapshot: Snapshot | None = None, features=licensing.PRO_FEATURES) -> Renderer:
    renderer = Renderer(out, features)
    renderer.render(snapshot or _lab(), [], None)
    return renderer


def _site(tmp_path: Path, snapshot: Snapshot | None = None, features=licensing.PRO_FEATURES):
    """Render the lab to `docs` and export it to `html`. Returns (docs, html, what the export did)."""
    out, html = tmp_path / "docs", tmp_path / "html"
    renderer = _render(out, snapshot, features)
    return out, html, htmlsite.export(out, html, renderer.generated)


def _files(folder: Path) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file() or p.is_symlink()}


@pytest.fixture
def config(tmp_path, monkeypatch):
    """Writes a config for a fake Docker host; call it with extra lines (such as `html_dir`)."""
    monkeypatch.setattr(docker_collector.DockerCollector, "client", property(lambda self: FakeDockerClient()))

    def write(extra: str = "") -> Path:
        path = tmp_path / "homelabscribe.yaml"
        path.write_text(
            f"output_dir: {tmp_path / 'docs'}\n"
            f"state_dir: {tmp_path / 'state'}\n"
            "docker_hosts:\n  - name: nova\n    url: tcp://fake:2375\n" + extra
        )
        return path

    return write


# ---- H1 and H2: turning it on -----------------------------------------------------------------


def test_h1_without_html_dir_nothing_changes_and_no_html_is_written(config, tmp_path):
    assert Config().html_dir is None  # optional, and off unless it is set
    result = runner.invoke(app, ["run", "-c", str(config())])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-1] == f"Updated 3 file(s) in {tmp_path / 'docs'}."
    assert "HTML" not in result.output
    rendered = runner.invoke(app, ["render", "-c", str(config())])
    assert rendered.output == f"Updated 0 file(s) in {tmp_path / 'docs'}.\n"
    assert not [p for p in tmp_path.rglob("*") if p.suffix in (".html", ".htm")]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["docs", "homelabscribe.yaml", "state"]


@pytest.fixture
def same_scan_time(monkeypatch):
    """Every scan gets the same time, so a second run shows no change the clock made (the break-glass page
    and the index show the scan time to the minute or the second)."""
    from datetime import UTC, datetime

    from homelabscribe import models

    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 15, 12, 0, 0, tzinfo=UTC)

    monkeypatch.setattr(models, "datetime", Fixed)


def test_h2_run_and_render_write_the_html_copy_after_the_markdown_and_report_it(
    config, tmp_path, same_scan_time
):
    path = config(f"html_dir: {tmp_path / 'html'}\n")
    first = runner.invoke(app, ["run", "-c", str(path)])
    assert first.exit_code == 0, first.output
    assert first.output.splitlines()[-2:] == [
        f"Updated 3 file(s) in {tmp_path / 'docs'}.",
        f"Updated 3 HTML file(s) in {tmp_path / 'html'}.",
    ]
    assert _files(tmp_path / "html") == {"index.html", "hosts/nova.html", "services/sonarr.html"}

    again = runner.invoke(app, ["run", "-c", str(path)])
    assert again.output.splitlines()[-1] == f"Updated 0 HTML file(s) in {tmp_path / 'html'}."

    page = tmp_path / "docs" / "services" / "sonarr.md"
    page.write_text(page.read_text().replace(notes.PLACEHOLDER, "Ask Jess about the config."))
    rendered = runner.invoke(app, ["render", "-c", str(path)])
    assert rendered.exit_code == 0, rendered.output
    assert rendered.output == (
        f"Updated 0 file(s) in {tmp_path / 'docs'}.\nUpdated 1 HTML file(s) in {tmp_path / 'html'}.\n"
    )
    assert "Ask Jess about the config." in (tmp_path / "html" / "services" / "sonarr.html").read_text()


def test_h2_run_with_every_writes_the_html_copy_on_each_round(config, tmp_path, monkeypatch):
    path = config(f"html_dir: {tmp_path / 'html'}\n")
    monkeypatch.setattr(licensing, "features", lambda key: (licensing.PRO_FEATURES, "Pro (test)"))
    rounds, index_there = [], []

    def sleep(seconds):
        rounds.append(seconds)
        index_there.append((tmp_path / "html" / "index.html").is_file())
        (tmp_path / "html" / "index.html").unlink()  # so the next round has something to write
        if len(rounds) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", sleep)
    result = runner.invoke(app, ["run", "-c", str(path), "--every", "1s"])
    html_lines = [line for line in result.output.splitlines() if "HTML file(s)" in line]
    assert rounds == [1, 1] and len(html_lines) == 2
    # Each round wrote the copy, and round 2 brought the deleted index.html back and counted it. How many
    # more files changed depends on the clock (a page that shows the scan time), so it is a lower bound.
    assert index_there == [True, True]
    m = re.fullmatch(rf"Updated (\d+) HTML file\(s\) in {re.escape(str(tmp_path / 'html'))}\.", html_lines[1])
    assert m and int(m.group(1)) >= 1


def test_h2_demo_html_writes_the_sample_lab_as_html(tmp_path):
    out, html = tmp_path / "demo", tmp_path / "demo-html"
    result = runner.invoke(app, ["demo", "-o", str(out), "--html", str(html)])
    assert result.exit_code == 0, result.output
    pages = {p.relative_to(out).with_suffix(".html").as_posix() for p in out.rglob("*.md")}
    assert len(pages) == 20 and _files(html) == pages  # an index.html and a page per Markdown page
    assert f"Wrote 20 HTML file(s) to {html}/." in result.output and (html / "index.html").is_file()

    plain = runner.invoke(app, ["demo", "-o", str(tmp_path / "plain")])
    assert plain.exit_code == 0 and "HTML" not in plain.output
    assert not list((tmp_path / "plain").rglob("*.html"))


# ---- H3: which pages are exported -------------------------------------------------------------


def test_h3_the_renderers_pages_and_archived_pages_are_exported_and_nothing_else(tmp_path):
    out, html = tmp_path / "docs", tmp_path / "html"
    lab = _lab()
    _render(out, lab)
    plex = out / "services" / "plex.md"
    plex.write_text(plex.read_text().replace(notes.PLACEHOLDER, "Plex pass renews in May."))
    lab.services = [s for s in lab.services if s.name != "plex"]

    # Files that are not pages, whatever their name: they are left alone and not exported.
    foreign = {
        "todo.md": "# My own list\n",
        "extra/index.md": f"# Mine\n{notes.START}\nx\n{notes.END}\n",
        "archive/services/no-markers.md": "# Not a generated page\n",
        "archive/services/deeper/old.md": f"# Deeper\n{notes.START}\nx\n{notes.END}\n",
        "archive/other/old.md": f"# Other\n{notes.START}\nx\n{notes.END}\n",
        "archive/services/half.md": f"# Half\n{notes.START}\nonly one marker\n",
    }
    for name, text in foreign.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_text(text)

    renderer = _render(out, lab)
    result = htmlsite.export(out, html, renderer.generated)
    exported = _files(html)
    assert (out / "archive" / "services" / "plex.md").is_file()
    assert {
        "index.html",
        "routes.html",  # the routes page
        "CHANGELOG.html",  # the changelog page, which has no notes markers
        "break-glass.html",
        "topology.html",  # the other Pro pages
        "hosts/nova.html",
        "services/sonarr.html",
        "archive/services/plex.html",  # an archived page
    } <= exported
    generated = {p.relative_to(out).with_suffix(".html").as_posix() for p in renderer.generated}
    assert exported == generated | {"archive/services/plex.html"}
    assert notes.START not in (out / "CHANGELOG.md").read_text()
    assert "Plex pass renews in May." in (html / "archive" / "services" / "plex.html").read_text()
    assert result.pages == len(exported) and not result.skipped
    for name, text in foreign.items():
        assert (out / name).read_text() == text  # untouched
    assert not (html / "todo.html").exists() and not (html / "extra").exists()


# ---- H4: notes --------------------------------------------------------------------------------


def test_h4_notes_are_in_the_html_and_the_marker_comments_are_not(tmp_path):
    out, html = tmp_path / "docs", tmp_path / "html"
    renderer = _render(out)
    page = out / "services" / "sonarr.md"
    page.write_text(
        page.read_text().replace(notes.PLACEHOLDER, "Config lives in `/opt/sonarr`.\n\n- Ask Jess")
    )
    htmlsite.export(out, html, renderer.generated)

    with_notes = (html / "services" / "sonarr.html").read_text()
    assert "<h2>Notes</h2>" in with_notes
    assert (
        "<p>Config lives in <code>/opt/sonarr</code>.</p>" in with_notes and "<li>Ask Jess</li>" in with_notes
    )
    for text in (p.read_text() for p in html.rglob("*.html")):
        assert "homelabscribe:notes" not in text and "&lt;!--" not in text and "<!--" not in text

    # A notes block that holds only the placeholder is left out together with its heading.
    without = (html / "services" / "plex.html").read_text()
    assert "Notes" not in without and "Your notes go here" not in without
    assert without.rstrip().endswith("</main>\n</body>\n</html>")
    family = (html / "break-glass.html").read_text()
    assert "Notes from" not in family and "Passwords and accounts" in family


def test_h4_marker_handling_keeps_the_text_around_the_notes():
    page = f"# T\n\n## Notes\n\n{notes.START}\n{notes.PLACEHOLDER}\n{notes.END}\n\nafter\n"
    assert _convert(page) == "\n<h1>T</h1>\n<p>after</p>\n"
    mine = f"# T\n\ntext\n\n{notes.START}\n{notes.PLACEHOLDER}\n{notes.END}\n"
    assert _convert(mine) == "\n<h1>T</h1>\n<p>text</p>\n"  # no heading above the block: only the block goes
    kept = f"# T\n\n## Notes\n\n{notes.START}\nmine\n{notes.END}\n{notes.START}\n"
    assert _convert(kept) == "\n<h1>T</h1>\n<h2>Notes</h2>\n<p>mine</p>\n"
    assert _convert(f"only {notes.END} one") == "\n<p>only  one</p>\n"


# ---- H5: front matter and titles --------------------------------------------------------------


@pytest.mark.parametrize(
    "markdown,title",
    [
        ("---\ntitle: Service: beta\ntags: [homelab]\n---\n# Heading\n", "Service: beta"),
        ("---\ntitle:   [not: {yaml\n\tkey: :\n---\n# Heading\n", "[not: {yaml"),
        ("---\r\ntitle: Windows\r\n---\r\n# Heading\r\n", "Windows"),
        ("---\ntitle: <script>alert(1)</script>\n---\n", "<script>alert(1)</script>"),
        ("---\ntags: [a]\n---\n# From the *heading*\n", "From the heading"),
        ("---\ntitle:\n---\n# Empty title line\n", "Empty title line"),
        ("---\ntitle: never closes\n\n# Heading wins\n", "Heading wins"),
        ("---\ntitle: never closes\nno heading at all\n", "plex"),
        ("no front matter and no heading\n", "plex"),
        ("", "plex"),
        ("---\n---\n", "plex"),
    ],
)
def test_h5_title_is_the_text_after_title_and_front_matter_never_stops_the_export(markdown, title):
    html = htmlsite.page_html(markdown, "services/plex.md")
    page = Page(html)
    assert page.balanced
    assert re.search(r"<title>(.*)</title>", html).group(1) == title.replace("<", "&lt;").replace(">", "&gt;")
    assert not page.all("script")


def test_h5_front_matter_is_not_shown():
    body = _convert("---\ntitle: Sonarr\nhost: nova\ntags: [homelab, media]\n---\n# Sonarr\n\ntext\n")
    assert body == "\n<h1>Sonarr</h1>\n<p>text</p>\n"
    unclosed = _convert("---\ntitle: x\n\n# Real page\n")
    assert "<h1>Real page</h1>" in unclosed  # front matter that does not close is page text


# ---- H6: links and images ---------------------------------------------------------------------

PAGES = frozenset({"index.md", "services/plex.md", "services/sonarr.md", "hosts/nova.md", "CHANGELOG.md"})


@pytest.mark.parametrize(
    "address",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://files.example/x",
        "ssh://nova.lan",
        "file:///etc/passwd",
        "vbscript:x",
        "smb://nas/share",
        "//example.test/page",
        "<java\tscript:alert(1)>",
        "odd:thing",
    ],
)
def test_h6_a_link_with_another_scheme_is_shown_as_its_text_and_is_not_a_link(address):
    page = Page(htmlsite.page_html(f"Go to [the *label*]({address}) now.\n", "services/plex.md", PAGES))
    assert [a.get("href") for a in page.all("a")] == ["../index.html"]  # only the link back to the overview
    assert "the label" in "".join(page.text)


@pytest.mark.parametrize(
    "address,href",
    [
        ("http://nova.lan:8989", "http://nova.lan:8989"),
        ("https://sonarr.home.example/x?y=1#z", "https://sonarr.home.example/x?y=1#z"),
        ("mailto:alex@example.com", "mailto:alex@example.com"),
        ("#how-to-restart", "#how-to-restart"),
        ("sonarr.md", "sonarr.html"),  # a page of the export: its HTML file
        ("sonarr.md#ports", "sonarr.html#ports"),  # with its fragment kept
        ("./sonarr.md?x=1#ports", "./sonarr.html?x=1#ports"),
        ("../hosts/nova.md", "../hosts/nova.html"),
        ("../index.md", "../index.html"),
        ("../CHANGELOG.md", "../CHANGELOG.html"),
        ("radarr.md", "radarr.md"),  # not a page of the export: unchanged
        ("../todo.md", "../todo.md"),
        ("/services/sonarr.md", "/services/sonarr.md"),
        ("photos/rack.png", "photos/rack.png"),
        ("../", "../"),
    ],
)
def test_h6_web_relative_and_fragment_links_stay_links(address, href):
    page = Page(htmlsite.page_html(f"[label]({address})\n", "services/plex.md", PAGES))
    assert [a.get("href") for a in page.all("a")] == ["../index.html", href]


def test_h6_relative_means_a_local_path_with_no_scheme_and_no_host():
    for address in ("page.html", "./a/b.md", "../x", "/from/the/root", "a%20b.md", "photos/rack.png?v=2#top"):
        assert htmlsite.address_kind(address) == "relative"
    assert htmlsite.address_kind("#top") == "fragment"
    for address in ("http://a.test", "HTTPS://a.test", "mailto:a@example.com"):
        assert htmlsite.address_kind(address) == "web"
    for address in (
        "//example.test/pixel.png",
        "\\\\example.test\\pixel.png",  # a browser reads a backslash as a slash
        "/\\example.test/pixel.png",
        "javascript:alert(1)",
        " javascript:alert(1)",
        "java\tscript:alert(1)",
        "java%09script:alert(1)",
        "data:text/html,x",
        "ftp://a.test",
        "ssh://a.test",
        "file:///etc/passwd",
        "weird:thing",
        "",
    ):
        assert htmlsite.address_kind(address) == "other", address
    # Markdown escapes a backslash, so in a page it ends up as part of a local path.
    page = Page(htmlsite.page_html("[a](\\\\example.test\\page) ![b](\\\\example.test\\pixel.png)\n"))
    assert page.all("a")[1] == {"href": "%5Cexample.test%5Cpage"}
    assert page.all("img") == [{"src": "%5Cexample.test%5Cpixel.png", "alt": "b"}]


def test_h6_autolinks_follow_the_same_rule():
    page = Page(htmlsite.page_html("<https://example.com/a> and <ftp://example.com/b>\n"))
    assert [a.get("href") for a in page.all("a")] == ["index.html", "https://example.com/a"]
    assert "ftp://example.com/b" in "".join(page.text)


@pytest.mark.parametrize(
    "address",
    [
        "https://example.test/pixel.png",  # a remote image
        "http://example.test/pixel.png",
        "data:image/png;base64,iVBORw0KGgo=",  # a data image
        "//example.test/pixel.png",  # no scheme, but a host
        "javascript:alert(1)",
        "#fragment",
    ],
)
def test_h6_an_image_that_is_not_relative_is_shown_as_its_alternative_text(address):
    page = Page(htmlsite.page_html(f"![the *rack* photo]({address})\n"))
    assert not page.all("img") and "the rack photo" in "".join(page.text)


@pytest.mark.parametrize("address", ["/any/path.png", "/", "/photos/rack.png?x=1", "/../x.png"])
def test_h6_an_image_that_starts_with_a_slash_is_shown_as_its_alternative_text(address):
    page = Page(htmlsite.page_html(f"![the rack]({address})\n"))
    assert not page.all("img") and "the rack" in "".join(page.text)


def test_h6_a_link_to_an_address_that_starts_with_a_slash_stays_a_link():
    assert {"href": "/x"} in Page(htmlsite.page_html("[x](/x)\n")).all("a")


def test_h6_a_relative_image_stays_an_image():
    page = Page(htmlsite.page_html('![rack](photos/rack.png "The rack")\n', "services/plex.md"))
    assert page.all("img") == [{"src": "photos/rack.png", "alt": "rack", "title": "The rack"}]


# ---- H7: raw HTML is text ---------------------------------------------------------------------


def test_h7_raw_html_in_a_label_a_description_a_proxmox_note_and_notes_is_shown_as_text(tmp_path):
    attack = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    lab = Snapshot(
        hosts=[Host(name="nova", kind="docker"), Host(name="pve1", kind="proxmox-node")],
        services=[
            Service(
                name="web",
                host="nova",
                kind="container",
                labels={
                    "homelabscribe.name": f"Web {attack}",
                    "homelabscribe.description": f"Label says {attack}",
                    "homelabscribe.url": "javascript:alert(3)",
                },
            ),
            Service(name="db", host="nova", kind="container", description=f"Description says {attack}"),
            Service(
                name="vm", host="pve1", kind="vm", vmid=101, notes=f"Proxmox note says {attack}\n<iframe>"
            ),
        ],
    )
    enrich(lab.services, load_catalog())
    out, html = tmp_path / "docs", tmp_path / "html"
    renderer = _render(out, lab)
    page = out / "services" / "db.md"
    mine = f'My note says {attack} <a href="javascript:alert(4)" onclick="alert(5)">x</a>'
    page.write_text(page.read_text().replace(notes.PLACEHOLDER, mine))
    htmlsite.export(out, html, renderer.generated)

    texts = {p.relative_to(html).as_posix(): p.read_text() for p in html.rglob("*.html")}
    assert len(texts) == 9
    escaped = "&lt;script&gt;alert(1)&lt;/script&gt;&lt;img src=x onerror=alert(2)&gt;"
    assert f"Label says {escaped}" in texts["index.html"]
    assert f"Description says {escaped}" in texts["services/db.html"]
    assert f"My note says {escaped}" in texts["services/db.html"]
    assert (
        f"Proxmox note says {escaped}" in texts["services/vm.html"]
        and "&lt;iframe&gt;" in texts["services/vm.html"]
    )
    assert sum(f"Web {escaped}" in text for text in texts.values()) >= 3  # also in the page title
    for text in texts.values():
        page = Page(text)
        assert page.balanced
        for banned in ("script", "img", "iframe", "object", "embed", "form", "base", "link"):
            assert not page.all(banned)
        assert not [name for _, attrs in page.tags for name in attrs if name.startswith("on")]
        assert all(htmlsite.address_kind(a["href"]) != "other" for a in page.all("a"))


def test_h7_the_page_template_escapes_what_it_inserts():
    html = htmlsite.page_html('---\ntitle: a "quoted" <b>title</b> & more\n---\ntext\n', 'x/"><script>.md')
    assert "<title>a &#34;quoted&#34; &lt;b&gt;title&lt;/b&gt; &amp; more</title>" in html
    assert not Page(html).all("script") and not Page(html).all("b")


# ---- H8 and H9: the page ----------------------------------------------------------------------


def test_h8_every_page_is_complete_html_with_inline_styles_a_csp_and_nothing_from_outside(tmp_path):
    _, html, result = _site(tmp_path)
    assert result.pages == 21
    for path in html.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        page = Page(text)
        assert text.startswith('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n')
        assert text.startswith(htmlsite.OWNED_START)  # the generator line that makes the file the export's
        assert text.endswith("</body>\n</html>\n") and page.balanced
        assert [tag for tag, _ in page.tags][:9] == [
            "html",
            "head",
            "meta",
            "meta",
            "meta",
            "meta",
            "title",
            "style",
            "body",
        ]
        metas = page.all("meta")
        assert metas[1] == {"name": "generator", "content": htmlsite.GENERATOR}
        assert metas[2] == {"name": "viewport", "content": "width=device-width, initial-scale=1"}

        # Its styles are inline, and the policy allows that stylesheet and same-origin images only.
        css = text.split("<style>", 1)[1].split("</style>", 1)[0]
        digest = base64.b64encode(hashlib.sha256(css.encode()).digest()).decode()
        assert metas[3] == {
            "http-equiv": "Content-Security-Policy",
            "content": f"default-src 'none'; style-src 'sha256-{digest}'; img-src 'self'; "
            "base-uri 'none'; form-action 'none'",
        }
        assert "prefers-color-scheme:dark" in css and "border-collapse" in css  # dark mode, readable tables
        assert "url(" not in css and "@import" not in css

        # No script, no inline style or event handler, and nothing a browser would fetch from elsewhere.
        assert len(page.all("style")) == 1 and len(metas) == 4
        for banned in ("script", "link", "iframe", "object", "embed", "form", "base", "img"):
            assert not page.all(banned)
        for _, attrs in page.tags:
            assert "style" not in attrs and "src" not in attrs
            assert not [name for name in attrs if name.startswith("on")]
        assert {tag for tag, attrs in page.tags if "href" in attrs} <= {"a"}

        # A link back to the overview, which is there.
        home = page.all("a")[0]["href"]
        assert (path.parent / home).resolve() == (html / "index.html").resolve()
        assert page.all("table") or path.name in ("CHANGELOG.html", "topology.html", "break-glass.html")


def test_h8_text_and_link_colors_have_enough_contrast_in_light_and_dark():
    def luminance(color: str) -> float:
        color = color.lstrip("#")
        color = "".join(c * 2 for c in color) if len(color) == 3 else color
        parts = [int(color[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    light, dark = htmlsite.CSS.split("@media (prefers-color-scheme:dark)")
    dark = dark.split("}\n}", 1)[0]
    for mode in (light, dark):
        colors = dict(re.findall(r"--([a-z]+):(#[0-9a-f]{3,6})", mode))
        assert set(colors) == {"bg", "fg", "muted", "line", "soft", "link"}
        for text in ("fg", "muted", "link"):
            for behind in ("bg", "soft"):
                lighter, darker = sorted((luminance(colors[text]), luminance(colors[behind])), reverse=True)
                assert (lighter + 0.05) / (darker + 0.05) >= 4.5, (text, behind)


def test_h8_table_alignment_is_a_class_not_an_inline_style():
    body = _convert("| a | b | c |\n| :-- | :-: | --: |\n| 1 | 2 | 3 |\n")
    assert '<th>a</th>\n<th class="center">b</th>\n<th class="right">c</th>' in body
    assert '<td class="right">3</td>' in body and "style=" not in body


def test_h9_a_mermaid_block_is_shown_as_text_with_one_line_on_how_to_see_it_drawn(tmp_path):
    _, html, _ = _site(tmp_path)
    topology = (html / "topology.html").read_text()
    assert topology.count(htmlsite.MERMAID_NOTE) == 1 and "To see it drawn" in htmlsite.MERMAID_NOTE
    assert "<pre><code>flowchart LR\n" in topology and "--&gt;" in topology
    assert not Page(topology).all("script") and "mermaid.min.js" not in topology
    assert _convert("```python\nprint('<b>')\n```\n") == "\n<pre><code>print('&lt;b&gt;')\n</code></pre>\n"


# ---- H10: whose files are whose ---------------------------------------------------------------


def test_h10_only_changed_files_are_written(tmp_path):
    out, html, first = _site(tmp_path)
    assert len(first.changed) == 21
    stamps = {p: p.stat().st_mtime_ns for p in html.rglob("*.html")}
    time.sleep(0.01)
    renderer = _render(out)
    page = out / "services" / "plex.md"
    page.write_text(page.read_text().replace(notes.PLACEHOLDER, "Renews in May."))
    second = htmlsite.export(out, html, renderer.generated)
    assert second.changed == [html / "services" / "plex.html"] and second.pages == 21
    changed = {p for p in stamps if p.stat().st_mtime_ns != stamps[p]}
    assert changed == {html / "services" / "plex.html"}
    assert htmlsite.export(out, html, renderer.generated).changed == []


def test_h10_a_foreign_file_is_never_overwritten_or_removed_and_an_owned_one_whose_page_is_gone_is(tmp_path):
    out, html = tmp_path / "docs", tmp_path / "html"
    lab = _lab()
    renderer = _render(out, lab)
    (html / "services").mkdir(parents=True)
    mine = {
        "index.html": "<html><head><title>My own start page</title></head></html>",  # at a generated path
        "services/sonarr.html": "not even HTML",  # at a generated path
        "services/mine.html": "<p>beside a generated page</p>",  # beside one
        "notes.txt": "my notes",
        "old/page.html": f"<html><head></head><body>{htmlsite.OWNED_LINE}</body></html>",  # not in the head
        "old/quoted.html": f"<html><head><title>x</title>\n{htmlsite.OWNED_LINE}\n</head></html>",
        "old/data.html.bak": htmlsite.OWNED_START,  # not an .html file
    }
    for name, text in mine.items():
        (html / name).parent.mkdir(parents=True, exist_ok=True)
        (html / name).write_text(text)

    first = htmlsite.export(out, html, renderer.generated)
    for name, text in mine.items():
        assert (html / name).read_text() == text
    assert first.skipped == [
        "index.html (a file that HomelabScribe did not write is in the way)",
        "services/sonarr.html (a file that HomelabScribe did not write is in the way)",
    ]
    assert first.pages == 19 and (html / "services" / "plex.html").is_file()  # it goes on with the others

    # A page that is gone: its HTML file is removed, because the export wrote it. Nothing else is.
    assert (html / "services" / "plex.html").is_file() and (html / "routes.html").is_file()
    lab.services = [s for s in lab.services if s.name != "plex"]
    lab.routes = []
    renderer = _render(out, lab)
    stale = html / "left" / "over.html"
    stale.parent.mkdir()
    stale.write_text((html / "topology.html").read_text())  # an owned file with no page at all
    second = htmlsite.export(out, html, renderer.generated)
    assert not (html / "services" / "plex.html").exists() and not (html / "routes.html").exists()
    assert not stale.exists() and stale.parent.is_dir()
    assert {html / "services" / "plex.html", html / "routes.html", stale} <= set(second.changed)
    for name, text in mine.items():
        assert (html / name).read_text() == text


def test_h10_the_command_prints_a_skipped_line_and_goes_on(config, tmp_path):
    html = tmp_path / "html"
    html.mkdir()
    (html / "index.html").write_text("<h1>My own start page</h1>")
    result = runner.invoke(app, ["run", "-c", str(config(f"html_dir: {html}\n"))])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-2:] == [
        "  skipped: index.html (a file that HomelabScribe did not write is in the way)",
        f"Updated 2 HTML file(s) in {html}.",
    ]
    assert (html / "index.html").read_text() == "<h1>My own start page</h1>"
    assert (html / "services" / "sonarr.html").is_file()


def _changed_plex(tmp_path):
    out, html, _ = _site(tmp_path)
    page = out / "services" / "plex.md"
    page.write_text(page.read_text().replace(notes.PLACEHOLDER, "Renews in May."))
    return out, html, html / "services" / "plex.html"


def _leftovers(html: Path) -> list[Path]:
    return [p for p in html.rglob("*") if p.name.endswith(htmlsite.TEMP_SUFFIX)]


def test_h10_a_hard_linked_copy_outside_html_dir_keeps_its_content(tmp_path):
    out, html, plex = _changed_plex(tmp_path)
    copy = tmp_path / "backup" / "plex.html"
    copy.parent.mkdir()
    os.link(plex, copy)
    old = copy.read_bytes()
    result = htmlsite.export(out, html, _render(out).generated)
    assert result.changed == [plex] and result.skipped == []
    assert copy.read_bytes() == old and b"Renews in May." not in old
    assert b"Renews in May." in plex.read_bytes() and plex.stat().st_nlink == 1
    assert not _leftovers(html)


@pytest.mark.skipif(not hasattr(os, "fchmod") or sys.platform == "win32", reason="no permission bits here")
def test_h10_a_replaced_file_keeps_the_permissions_of_the_file_it_replaces(tmp_path):
    out, html, plex = _changed_plex(tmp_path)
    plex.chmod(0o600)
    other = html / "index.html"
    other.chmod(0o640)
    result = htmlsite.export(out, html, _render(out).generated)
    assert result.changed == [plex] and b"Renews in May." in plex.read_bytes()
    assert plex.stat().st_mode & 0o777 == 0o600 and other.stat().st_mode & 0o777 == 0o640
    assert not _leftovers(html)


def test_h10_a_failed_write_of_a_replacement_leaves_the_old_file_and_no_temporary_file(tmp_path, monkeypatch):
    out, html, plex = _changed_plex(tmp_path)
    old = plex.read_bytes()

    def partial(fd, data, mode=None):
        os.write(fd, data[:40])
        os.close(fd)
        raise OSError("disk full")

    monkeypatch.setattr(htmlsite, "_write", partial)
    result = htmlsite.export(out, html, _render(out).generated)
    assert plex.read_bytes() == old and not _leftovers(html)
    assert result.skipped == ["services/plex.html (the file there could not be written)"]
    assert plex not in result.changed


def test_h10_a_failed_replace_leaves_the_old_file_and_no_temporary_file(tmp_path, monkeypatch):
    out, html, plex = _changed_plex(tmp_path)
    old = plex.read_bytes()

    def refuse(src, dst):
        raise PermissionError("no")

    monkeypatch.setattr(htmlsite.os, "replace", refuse)
    result = htmlsite.export(out, html, _render(out).generated)
    assert plex.read_bytes() == old and not _leftovers(html)
    assert len(result.skipped) == 1 and result.skipped[0].startswith("services/plex.html")


@pytest.mark.parametrize("fails", ["write", "close"])
def test_h10_a_failed_write_of_a_new_file_leaves_no_file(tmp_path, monkeypatch, fails):
    out, html, plex = _changed_plex(tmp_path)
    plex.unlink()  # a file that does not exist yet
    real = htmlsite._write

    def failing(fd, data, mode=None):
        if fails == "write":
            os.close(fd)
            raise OSError("disk full")
        real(fd, data, mode)  # the data is written and closed; the error only comes after, as at close
        raise OSError("close failed")

    monkeypatch.setattr(htmlsite, "_write", failing)
    result = htmlsite.export(out, html, _render(out).generated)
    assert not plex.exists() and not _leftovers(html)
    assert result.skipped == ["services/plex.html (the file there could not be written)"]
    monkeypatch.undo()
    assert htmlsite.export(out, html, _render(out).generated).changed == [plex]  # it comes back next run


def test_h10_a_leftover_temporary_file_of_the_export_is_removed_and_a_users_file_is_not(tmp_path):
    out, html, plex = _changed_plex(tmp_path)
    ours = html / "services" / f".plex.html.0123abcd{htmlsite.TEMP_SUFFIX}"
    ours.write_bytes(plex.read_bytes()[:200])  # cut short, but it starts with the generator
    plain = {
        html / f"index.html{htmlsite.TEMP_SUFFIX}": "not ours",  # similar name, no generator start
        html / f"mine{htmlsite.TEMP_SUFFIX}": htmlsite.OWNED_LINE,  # the line, but not at the start
    }
    for path, text in plain.items():
        path.write_text(text)
    link = html / f"link{htmlsite.TEMP_SUFFIX}"
    link.symlink_to(plex)  # a link to a file of ours is a link, not the export's own file
    result = htmlsite.export(out, html, _render(out).generated)
    assert not ours.exists()
    assert all(path.read_text() == text for path, text in plain.items())
    assert link.is_symlink() and plex.is_file()
    assert ours not in result.changed and result.pages == 21
    assert not any(p.name.endswith(".html") and p.name.startswith(".") for p in html.rglob("*"))


# ---- H11 and H12: free, and nothing else touched ----------------------------------------------


def test_h11_the_export_is_free_and_exports_pro_pages_when_they_exist(config, tmp_path):
    source = Path(htmlsite.__file__).read_text()
    assert "licensing" not in source and "features" not in source  # it never looks at the license

    result = runner.invoke(app, ["run", "-c", str(config(f"html_dir: {tmp_path / 'html'}\n"))])
    assert "Free edition" in result.output and (tmp_path / "html" / "index.html").is_file()

    cfg = Config(output_dir=tmp_path / "pro", state_dir=tmp_path / "s", html_dir=tmp_path / "pro-html")
    rendered = pipeline.render_pages(cfg, licensing.PRO_FEATURES, _lab())
    assert rendered.html.pages == 21
    for name in ("CHANGELOG.html", "break-glass.html", "topology.html"):
        assert (tmp_path / "pro-html" / name).is_file()
    again = pipeline.render_pages(cfg, frozenset(), _lab())  # the Pro pages are still there as Markdown...
    assert again.html.pages == 18 and (tmp_path / "pro" / "topology.md").is_file()
    assert not (tmp_path / "pro-html" / "topology.html").exists()  # ...but no longer pages of this run


_seen: list[tuple[str, str, int]] | None = None


def _audit(event, args):
    if _seen is not None and event in (
        "open",
        "os.remove",
        "os.mkdir",
        "os.rename",
        "os.rmdir",
        "os.symlink",
    ):
        flags = args[2] if event == "open" and isinstance(args[2], int) else 0
        _seen.append((event, os.fsdecode(args[0]) if isinstance(args[0], str | bytes) else "", flags))


sys.addaudithook(_audit)  # cannot be removed again, so it only records while a test asks it to


def test_h12_the_export_reads_the_markdown_folder_writes_the_html_folder_and_nothing_else(
    tmp_path, monkeypatch
):
    global _seen
    out, html = tmp_path / "docs", tmp_path / "html"
    lab = _lab()
    renderer = _render(out, lab)
    htmlsite.export(out, html, renderer.generated)
    lab.services = [s for s in lab.services if s.name != "plex"]
    renderer = _render(out, lab)  # so the next export also removes a file
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "changelog.json").write_text("[]")

    def no_network(*args, **kwargs):
        raise AssertionError("the export opened a network connection")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    _seen = []
    try:
        result = htmlsite.export(out, html, renderer.generated)
    finally:
        seen, _seen = _seen, None
    assert html / "services" / "plex.html" in result.changed
    writing = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    for event, path, flags in seen:
        where = Path(path)
        if not where.is_relative_to(tmp_path):
            continue  # the interpreter's own files
        if where.is_relative_to(html):
            continue
        assert where.is_relative_to(out), (event, path)  # nothing else: not the state folder
        assert event == "open" and not flags & writing, (event, path)  # and the Markdown is only read
    assert {p: p.read_bytes() for p in out.rglob("*") if p.is_file()} == before
    assert any(event == "os.remove" for event, _, _ in seen) and any(
        Path(p).is_relative_to(out) for _, p, _ in seen
    )


# ---- H13: folders and links -------------------------------------------------------------------


@pytest.mark.parametrize(
    "html_dir,relation",
    [
        ("docs", "is the same folder as output_dir"),
        ("docs/html", "lies inside output_dir"),
        ("docs/a/b/html", "lies inside output_dir"),
        (".", "contains output_dir"),
        ("state", "is the same folder as state_dir"),
        ("state/html", "lies inside state_dir"),
        ("link-to-docs", "is the same folder as output_dir"),  # compared after resolving both
        ("link-to-docs/html", "lies inside output_dir"),
    ],
)
@pytest.mark.parametrize("command", ["run", "render"])
def test_h13_html_dir_must_be_a_folder_of_its_own_or_the_command_stops_before_writing(
    config, tmp_path, html_dir, relation, command
):
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "link-to-docs").symlink_to(lab / "docs")
    path = lab / "homelabscribe.yaml"
    path.write_text(config().read_text().replace(str(tmp_path), str(lab)) + f"html_dir: {lab / html_dir}\n")
    config().unlink()
    before = _files(lab)
    result = runner.invoke(app, [command, "-c", str(path)])
    assert result.exit_code == 1
    assert relation in result.output and "Nothing was written." in result.output
    assert "Scanned" not in result.output and "Updated" not in result.output
    assert _files(lab) == before and not (lab / "docs").exists() and not (lab / "state").exists()


def test_h13_the_folder_rule_also_holds_for_the_demo_and_for_the_library(tmp_path):
    same = runner.invoke(app, ["demo", "-o", str(tmp_path / "demo"), "--html", str(tmp_path / "demo")])
    inside = runner.invoke(
        app, ["demo", "-o", str(tmp_path / "demo"), "--html", str(tmp_path / "demo" / "html")]
    )
    around = runner.invoke(app, ["demo", "-o", str(tmp_path / "a" / "demo"), "--html", str(tmp_path / "a")])
    for result, relation in ((same, "is the same folder as"), (inside, "lies inside"), (around, "contains")):
        assert result.exit_code == 1 and f"{relation} the demo folder" in result.output
    assert list(tmp_path.iterdir()) == []

    cfg = Config(
        output_dir=tmp_path / "docs", state_dir=tmp_path / "state", html_dir=tmp_path / "state" / "h"
    )
    with pytest.raises(ValueError, match="lies inside state_dir"):
        pipeline.render(cfg, frozenset(), _lab())
    with pytest.raises(ValueError, match="is the same folder as the Markdown folder"):
        htmlsite.export(tmp_path / "docs", tmp_path / "docs", [])
    assert list(tmp_path.iterdir()) == []
    (tmp_path / "a-file").write_text("x")
    assert "is not a folder" in htmlsite.folder_problem(tmp_path / "a-file", {})
    ok = Config(
        output_dir=tmp_path / "docs",
        state_dir=tmp_path / "data" / "state",
        html_dir=tmp_path / "data" / "html",
    )
    assert pipeline.html_dir_problem(ok) is None and pipeline.html_dir_problem(Config()) is None


def test_h13_a_page_that_is_a_link_is_not_read(tmp_path):
    out, html = tmp_path / "docs", tmp_path / "html"
    renderer = _render(out)
    secret = tmp_path / "secret.md"
    secret.write_text(f"# TOP-SECRET-PAGE\n{notes.START}\nTOP-SECRET-NOTE\n{notes.END}\n")
    (out / "services" / "plex.md").unlink()
    (out / "services" / "plex.md").symlink_to(secret)  # a generated page replaced by a link
    (out / "archive" / "services").mkdir(parents=True)
    (out / "archive" / "services" / "old.md").symlink_to(secret)  # an archived page that is a link
    (out / "archive" / "hosts").symlink_to(tmp_path)  # a linked folder where archived pages would be

    result = htmlsite.export(out, html, renderer.generated)
    assert result.skipped == ["services/plex.md (the page is a symbolic link or could not be read)"]
    assert not (html / "services" / "plex.html").exists() and not (html / "archive").exists()
    assert result.pages == 20
    assert all("TOP-SECRET" not in p.read_text() for p in html.rglob("*.html"))

    (out / "services").rename(out / "services-real")
    (out / "services").symlink_to(out / "services-real")  # the folder of the pages is a link
    result = htmlsite.export(out, tmp_path / "html2", renderer.generated)
    assert len(result.skipped) == 14 and not (tmp_path / "html2" / "services").exists()


def test_h13_a_destination_that_is_a_link_or_lies_behind_a_linked_folder_is_not_written(tmp_path):
    out, html = tmp_path / "docs", tmp_path / "html"
    renderer = _render(out)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "services").mkdir(parents=True)
    owned = htmlsite.page_html("# A page\n")  # even a file that is one of ours
    assert owned.startswith(htmlsite.OWNED_START)
    (elsewhere / "index.html").write_text(owned)
    (elsewhere / "services" / "old.html").write_text(owned)
    html.mkdir()
    (html / "index.html").symlink_to(elsewhere / "index.html")  # a destination that is a link
    (html / "topology.html").symlink_to(elsewhere / "not-there.html")  # a link to nothing
    (html / "services").symlink_to(elsewhere / "services")  # a linked folder
    (html / "stale.html").symlink_to(elsewhere / "index.html")

    result = htmlsite.export(out, html, renderer.generated)
    assert "index.html (the file there is a symbolic link)" in result.skipped
    assert "topology.html (the file there is a symbolic link)" in result.skipped
    assert "services/plex.html (a folder on the way is a symbolic link)" in result.skipped
    assert len(result.skipped) == 16 and result.pages == 5
    assert (html / "hosts" / "nova.html").is_file()
    # Nothing was written or removed through a link, and the clean-up did not enter the linked folder.
    assert _files(elsewhere) == {"index.html", "services/old.html"}
    assert (elsewhere / "index.html").read_text() == owned and (
        elsewhere / "services" / "old.html"
    ).read_text() == owned
    assert (html / "index.html").is_symlink() and (html / "stale.html").is_symlink()
    assert (html / "services").is_symlink() and (html / "topology.html").is_symlink()
