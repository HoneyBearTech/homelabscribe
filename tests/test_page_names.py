"""Page names: services and hosts whose names look alike each get a page of their own, and keep it."""

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from homelabscribe import htmlsite, licensing, notes
from homelabscribe import renderer as renderer_module
from homelabscribe.demo import demo_snapshots
from homelabscribe.enrich import enrich, load_catalog
from homelabscribe.models import Host, Service, Snapshot
from homelabscribe.renderer import Renderer, coded_name, front_matter, slug


def _svc(name, host, title=None, category="Other", kind="container"):
    return Service(name=name, host=host, kind=kind, display_name=title, category=category)


def _code(key: str, n: int = 8) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:n]


CASES = {
    "titles that differ only in punctuation": (
        ["h"],
        [_svc("a", "h", "Plex Media"), _svc("b", "h", "Plex-Media")],
    ),
    "a title plus host that is another title": (
        ["atlas", "nova"],
        [_svc("plex", "atlas", "Plex"), _svc("plex", "nova", "Plex"), _svc("x", "nova", "Plex Atlas")],
    ),
    "one title twice on one host": (["h"], [_svc("Plex", "h"), _svc("plex", "h")]),
    "titles without a Latin letter or digit": (
        ["h"],
        [_svc("a", "h", "プレックス"), _svc("b", "h", "ソナー"), _svc("c", "h", "ラダー")],
    ),
    "host names that differ only in case and spaces": (
        ["NAS 1", "nas-1"],
        [_svc("a", "NAS 1", "A"), _svc("b", "nas-1", "B")],
    ),
}


TAKEN_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _snap(hosts, services, reverse=False) -> Snapshot:
    hs = [Host(name=h, kind="docker") for h in hosts]
    ss = [s.model_copy(deep=True) for s in services]
    if reverse:
        hs, ss = hs[::-1], ss[::-1]
    return Snapshot(hosts=hs, services=ss, taken_at=TAKEN_AT)


def _pages(out: Path) -> list[str]:
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*.md"))


def _check_own_pages_and_links(out: Path, snap: Snapshot) -> None:
    """Every service and host has a page of its own, and every link to one opens that page."""
    services = {(fm["title"], fm["host"]) for p in (out / "services").glob("*.md") if (fm := front_matter(p))}
    assert services == {(s.title, s.host) for s in snap.services}
    assert len(list((out / "services").glob("*.md"))) == len(snap.services)
    hosts = sorted(front_matter(p)["title"] for p in (out / "hosts").glob("*.md"))
    assert hosts == sorted(h.name for h in snap.hosts)
    links = 0
    for page in out.rglob("*.md"):
        for text, target in re.findall(r"\[([^\]]+)\]\(([^)#:\s]+\.md)\)", page.read_text()):
            if (page.parent / target).resolve().parent.name not in ("services", "hosts"):
                continue
            fm = front_matter((page.parent / target).resolve())
            assert fm is not None, (page, target)
            assert fm["title"] == text, (page, text, target)
            links += 1
    index = (out / "index.md").read_text()
    rows = re.findall(r"^\| \[(.+?)\]\((services/[^)]+)\) \| (.+?) \|", index, re.M)
    assert len(rows) == len(snap.services)
    for title, target, host in rows:
        assert (front_matter(out / target)["title"], front_matter(out / target)["host"]) == (title, host)
    assert links >= len(snap.services) * 2 + len(snap.hosts)  # index → each page, each service → its host


@pytest.mark.parametrize("case", CASES)
def test_each_look_alike_gets_its_own_page_and_every_link_points_to_it(tmp_path, case):
    snap = _snap(*CASES[case])
    renderer = Renderer(tmp_path, licensing.PRO_FEATURES)
    renderer.render(snap, [], None)
    _check_own_pages_and_links(tmp_path, snap)
    assert renderer.skipped == [] and len(renderer.notices) >= 1


@pytest.mark.parametrize("case", CASES)
def test_the_scan_order_does_not_change_the_names(tmp_path, case):
    Renderer(tmp_path / "a").render(_snap(*CASES[case]))
    Renderer(tmp_path / "b").render(_snap(*CASES[case], reverse=True))
    assert _pages(tmp_path / "a") == _pages(tmp_path / "b")
    for rel in _pages(tmp_path / "a"):  # each name is the page of the same service or host
        fm_a, fm_b = front_matter(tmp_path / "a" / rel), front_matter(tmp_path / "b" / rel)
        assert (fm_a["title"], fm_a.get("host")) == (fm_b["title"], fm_b.get("host"))


@pytest.mark.parametrize("case", CASES)
def test_a_second_render_writes_nothing(tmp_path, case):
    Renderer(tmp_path).render(_snap(*CASES[case]))
    assert Renderer(tmp_path).render(_snap(*CASES[case])) == []


def test_the_expected_names_and_notes(tmp_path):
    renderer = Renderer(tmp_path)
    renderer.render(_snap(*CASES["titles that differ only in punctuation"]))
    code = _code("h/Plex-Media")
    assert _pages(tmp_path) == [
        "hosts/h.md",
        "index.md",
        f"services/plex-media-{code}.md",
        "services/plex-media.md",
    ]
    assert front_matter(tmp_path / "services" / "plex-media.md")["title"] == "Plex Media"  # the fixed order
    assert renderer.notices == [
        f'"Plex-Media" on h has its own page services/plex-media-{code}.md (its name matched "Plex Media")'
    ]

    renderer = Renderer(tmp_path / "b")
    renderer.render(_snap(*CASES["host names that differ only in case and spaces"]))
    assert renderer.notices == [
        f'host "nas-1" has its own page hosts/nas-1-{_code("nas-1")}.md (its name matched "NAS 1")'
    ]
    assert front_matter(tmp_path / "b" / "hosts" / "nas-1.md")["title"] == "NAS 1"

    renderer = Renderer(tmp_path / "c")
    renderer.render(_snap(*CASES["a title plus host that is another title"]))
    assert front_matter(tmp_path / "c" / "services" / "plex-atlas.md")["title"] == "Plex Atlas"
    assert (tmp_path / "c" / "services" / f"plex-atlas-{_code('atlas/Plex')}.md").is_file()
    assert (tmp_path / "c" / "services" / "plex-nova.md").is_file()


def test_a_normal_lab_keeps_every_name(tmp_path):
    _, snap = demo_snapshots()
    enrich(snap.services, load_catalog())
    renderer = Renderer(tmp_path, licensing.PRO_FEATURES)
    renderer.render(snap, [], None)
    services = snap.visible_services()
    titles = [s.title.lower() for s in services]
    old = {
        slug(s.title) if titles.count(s.title.lower()) == 1 else slug(f"{s.title}-{s.host}") for s in services
    }  # the rule before look-alikes got a name of their own
    assert {p.stem for p in (tmp_path / "services").glob("*.md")} == old
    assert {p.stem for p in (tmp_path / "hosts").glob("*.md")} == {slug(h.name) for h in snap.hosts}
    assert renderer.notices == []


def _add_notes(page: Path, text: str) -> None:
    page.write_text(page.read_text().replace(notes.PLACEHOLDER, text))


@pytest.mark.parametrize("first", ["Plex Media", "Plex-Media"])
def test_the_page_already_there_stays_with_its_service_and_its_notes(tmp_path, first):
    a, b = _svc("a", "h", "Plex Media"), _svc("b", "h", "Plex-Media")
    earlier, later = (a, b) if first == "Plex Media" else (b, a)
    Renderer(tmp_path).render(_snap(["h"], [earlier]))
    page = tmp_path / "services" / "plex-media.md"
    _add_notes(page, f"Notes about {first}.")

    renderer = Renderer(tmp_path)
    renderer.render(_snap(["h"], [a, b]))  # the look-alike arrives
    assert front_matter(page)["title"] == first and f"Notes about {first}." in page.read_text()
    newcomer = tmp_path / "services" / f"plex-media-{_code(f'h/{later.title}')}.md"
    assert front_matter(newcomer)["title"] == later.title and "Notes about" not in newcomer.read_text()
    assert renderer.notices == [
        f'"{later.title}" on h has its own page services/{newcomer.name} (its name matched "{first}")'
    ]
    assert not (tmp_path / "archive").exists()
    _check_own_pages_and_links(tmp_path, _snap(["h"], [a, b]))
    for snap in (_snap(["h"], [a, b]), _snap(["h"], [a, b], reverse=True)):
        assert Renderer(tmp_path).render(snap) == []  # and it keeps it


def test_a_host_page_already_there_stays_with_its_host(tmp_path):
    Renderer(tmp_path).render(_snap(["nas-1"], [_svc("b", "nas-1", "B")]))
    page = tmp_path / "hosts" / "nas-1.md"
    _add_notes(page, "The small NAS.")
    Renderer(tmp_path).render(_snap(*CASES["host names that differ only in case and spaces"]))
    assert front_matter(page)["title"] == "nas-1" and "The small NAS." in page.read_text()
    assert front_matter(tmp_path / "hosts" / f"nas-1-{_code('NAS 1')}.md")["title"] == "NAS 1"
    _check_own_pages_and_links(tmp_path, _snap(*CASES["host names that differ only in case and spaces"]))


def test_a_category_change_does_not_move_a_page_or_its_notes(tmp_path):
    def lab(category_b):
        return _snap(
            ["h"],
            [
                _svc("a", "h", "Plex Media", category="Media"),
                _svc("b", "h", "Plex-Media", category=category_b),
            ],
        )

    Renderer(tmp_path).render(lab("Zeta"))
    code = _code("h/Plex-Media")
    _add_notes(tmp_path / "services" / "plex-media.md", "About Plex Media.")
    _add_notes(tmp_path / "services" / f"plex-media-{code}.md", "About Plex-Media.")
    Renderer(tmp_path).render(lab("Alpha"))  # b now sorts first in the scan and the index
    one, other = (
        (tmp_path / "services" / "plex-media.md").read_text(),
        (tmp_path / "services" / f"plex-media-{code}.md").read_text(),
    )
    assert "# Plex Media\n" in one and "About Plex Media." in one and "About Plex-Media." not in one
    assert "# Plex-Media\n" in other and "About Plex-Media." in other and "About Plex Media." not in other
    assert not (tmp_path / "archive").exists()


def test_a_coded_page_of_a_service_that_is_gone_is_archived_with_its_notes(tmp_path):
    Renderer(tmp_path).render(_snap(*CASES["titles that differ only in punctuation"]))
    coded = tmp_path / "services" / f"plex-media-{_code('h/Plex-Media')}.md"
    _add_notes(coded, "About Plex-Media.")
    Renderer(tmp_path).render(_snap(["h"], [_svc("a", "h", "Plex Media")]))
    assert not coded.exists()
    assert "About Plex-Media." in (tmp_path / "archive" / "services" / coded.name).read_text()
    assert front_matter(tmp_path / "services" / "plex-media.md")["title"] == "Plex Media"


def test_a_long_name_with_its_code_stays_within_200_characters(tmp_path):
    snap = _snap(["h"], [_svc("a", "h", "A" * 300), _svc("b", "h", "a" * 300)])
    renderer = Renderer(tmp_path)
    renderer.render(snap)
    stems = [p.stem for p in (tmp_path / "services").glob("*.md")]
    assert len(stems) == 2 and all(len(s) <= renderer_module.SLUG_MAX for s in stems)
    assert all("--" not in s and not s.endswith("-") for s in stems)
    assert len(renderer.notices) == 1
    _check_own_pages_and_links(tmp_path, snap)


def test_a_code_that_is_taken_grows_until_it_is_free(tmp_path):
    code = _code("h/Plex-Media")
    snap = _snap(
        ["h"],
        [
            _svc("a", "h", "Plex Media"),
            _svc("b", "h", "Plex-Media"),
            _svc("c", "h", f"Plex Media {code}"),  # its own name is the code Plex-Media would get
        ],
    )
    Renderer(tmp_path).render(snap)
    assert front_matter(tmp_path / "services" / f"plex-media-{code}.md")["title"] == f"Plex Media {code}"
    longer = tmp_path / "services" / f"plex-media-{_code('h/Plex-Media', 10)}.md"
    assert front_matter(longer)["title"] == "Plex-Media"
    _check_own_pages_and_links(tmp_path, snap)


def test_two_look_alikes_with_one_host_and_title_get_two_codes(tmp_path):
    snap = _snap(["h"], [_svc(n, "h", "Plex") for n in ("a", "b", "c")])
    Renderer(tmp_path).render(snap)
    names = {p.stem for p in (tmp_path / "services").glob("*.md")}
    key = _code("h/Plex", 64)
    assert names == {"plex-h", f"plex-h-{key[:8]}", f"plex-h-{key[:10]}"}  # by name: a, b, c
    assert Renderer(tmp_path).render(snap) == []


def test_coded_name_grows_the_code_and_then_counts():
    full = hashlib.sha256(b"k").hexdigest()
    assert coded_name("base", "k", set()) == f"base-{full[:8]}"
    assert coded_name("base", "k", {f"base-{full[:8]}", f"base-{full[:10]}"}) == f"base-{full[:12]}"
    every = {f"base-{full[:n]}" for n in range(8, 65, 2)}
    assert coded_name("base", "k", every) == f"base-{full}-2"
    assert coded_name("base", "k", every | {f"base-{full}-2"}) == f"base-{full}-3"
    long = coded_name("x" * 199 + "-" + "y", "k", set())
    assert len(long) <= 200 and long == "x" * 191 + f"-{full[:8]}"
    assert coded_name("a" * 190 + "-" + "b" * 9, "k", set()) == "a" * 190 + f"-{full[:8]}"  # no "--"


@pytest.mark.parametrize(
    "existing",
    ["utf16", "link", "no front matter", "not a mapping", "bad yaml", "a folder"],
)
def test_a_page_whose_owner_cannot_be_read_falls_back_to_the_fixed_order(tmp_path, existing):
    services = tmp_path / "out" / "services"
    services.mkdir(parents=True)
    page = services / "plex-media.md"
    written_for_b = "---\ntitle: Plex-Media\nhost: h\n---\n# Plex-Media\n"
    if existing == "utf16":
        page.write_bytes(written_for_b.encode("utf-16"))
    elif existing == "link":
        (tmp_path / "elsewhere.md").write_text(written_for_b)
        page.symlink_to(tmp_path / "elsewhere.md")
    elif existing == "no front matter":
        page.write_text("# Plex-Media\ntitle: Plex-Media\nhost: h\n")
    elif existing == "not a mapping":
        page.write_text("---\n- title\n- Plex-Media\n---\n")
    elif existing == "bad yaml":
        page.write_text("---\ntitle: [Plex-Media\nhost: h\n---\n")
    else:
        page.mkdir()
    renderer = Renderer(tmp_path / "out")
    renderer.render(_snap(*CASES["titles that differ only in punctuation"]))
    coded = services / f"plex-media-{_code('h/Plex-Media')}.md"
    assert front_matter(coded)["title"] == "Plex-Media"  # the fixed order: Plex Media keeps the name
    assert renderer.notices == [
        f'"Plex-Media" on h has its own page services/{coded.name} (its name matched "Plex Media")'
    ]


def test_front_matter_is_read_exactly(tmp_path):
    page = tmp_path / "p.md"
    page.write_text('---\ntitle: "yes"\nhost: "a: b"\n---\n# x\n')
    assert front_matter(page) == {"title": "yes", "host": "a: b"}
    page.write_text("---\ntitle: yes\n---\n")
    assert front_matter(page) == {"title": True}  # never equal to a title, which is a str
    page.write_text("---\ntitle: a\n")
    assert front_matter(page) is None
    assert front_matter(tmp_path / "missing.md") is None


def test_the_html_copy_has_one_file_for_each_page(tmp_path):
    for case in ("titles that differ only in punctuation", "host names that differ only in case and spaces"):
        out, html = tmp_path / case / "md", tmp_path / case / "html"
        renderer = Renderer(out)
        renderer.render(_snap(*CASES[case]))
        result = htmlsite.export(out, html, renderer.generated)
        assert result.skipped == []
        md = {p.relative_to(out).with_suffix(".html").as_posix() for p in out.rglob("*.md")}
        assert {p.relative_to(html).as_posix() for p in html.rglob("*.html")} == md
        assert len(md) == len(renderer.generated)
