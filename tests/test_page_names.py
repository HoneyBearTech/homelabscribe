"""Page names: services and hosts whose names look alike each get a page of their own, and keep it."""

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from homelabscribe import htmlsite, licensing, notes
from homelabscribe import renderer as renderer_module
from homelabscribe.cli import app
from homelabscribe.collectors.proxmox import parse_guest
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


def test_look_alikes_with_one_host_and_title_get_codes_from_their_ids(tmp_path):
    snap = _snap(["h"], [_svc(n, "h", "Plex") for n in ("a", "b", "c")])
    Renderer(tmp_path).render(snap)
    names = {p.stem: front_matter(p)["id"] for p in (tmp_path / "services").glob("*.md")}
    assert names == {
        "plex-h": "h/a/container",  # the fixed order: by container name
        f"plex-h-{_code('h/Plex/h/b/container')}": "h/b/container",
        f"plex-h-{_code('h/Plex/h/c/container')}": "h/c/container",
    }
    assert Renderer(tmp_path).render(snap) == []


def test_coded_name_grows_the_code_and_then_counts():
    full = hashlib.sha256(b"k").hexdigest()
    assert coded_name("base", "k", set()) == f"base-{full[:8]}"
    assert coded_name("base", "k", {f"base-{full[:8]}", f"base-{full[:10]}"}) == f"base-{full[:12]}"
    every = {f"base-{full[:n]}" for n in range(8, 65, 2)}
    assert coded_name("base", "k", every) == f"base-{full}-2"
    assert coded_name("base", "k", every | {f"base-{full}-2"}) == f"base-{full}-3"
    held = {f"base-{full[:8]}"}.__contains__  # a page written for someone else is there
    assert coded_name("base", "k", set(), held) == f"base-{full[:10]}"
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


runner = CliRunner()


@pytest.mark.parametrize("command", ["render", "run"])
def test_the_command_line_prints_a_note_for_each_extra_page_and_exits_0(tmp_path, command):
    config = tmp_path / "homelabscribe.yaml"
    config.write_text(
        f"output_dir: {tmp_path / 'docs'}\n"
        f"state_dir: {tmp_path / 'state'}\n"
        "docker_hosts: []\n"
        "manual_services:\n"
        "  - {name: a, host: h, title: Plex Media}\n"
        "  - {name: b, host: h, title: Plex-Media}\n"
    )
    if command == "render":
        assert runner.invoke(app, ["scan", "-c", str(config)]).exit_code == 0
    for args in [[command, "-c", str(config)]] + (
        [[command, "-c", str(config), "--strict"]] if command == "run" else []
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        line = (
            f'  note: "Plex-Media" on h has its own page services/plex-media-{_code("h/Plex-Media")}.md'
            ' (its name matched "Plex Media")\n'
        )
        assert result.output.count(line) == 1
        assert result.output.index(line) < result.output.index("Updated ")
        assert "skipped:" not in result.output


# --- front matter that cannot be read never stops a render -----------------------------------------------

_FILLER = "".join(f"line{i}: {'x' * 60}\n" for i in range(1200))  # about 80 KiB, more than the cap

HOSTILE = {
    "bad date": "title: 2020-13-45\n",
    "bad date as a key": "2026-99-99: x\n",
    "bad timestamp": "modified: 2026-01-01T25:61:00Z\n",
    "huge integer": f"size: {'9' * 5000}\n",
    "block bigger than the cap": _FILLER,
    "not UTF-8 in the block": "x: caf\udce9\n",
    "a NUL in the block": "x: a\0b\n",
}


def _hostile(path: Path, owner: str, extra: str, host: str | None = "h") -> None:
    block = f"title: {owner}\n" + (f"host: {host}\n" if host else "") + extra
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"---\n{block}---\n# {owner}\n".encode("utf-8", "surrogateescape"))


@pytest.mark.parametrize("kind", HOSTILE)
def test_a_service_page_yaml_cannot_read_has_no_owner_and_the_render_goes_on(tmp_path, kind):
    page = tmp_path / "services" / "plex-media.md"
    _hostile(page, "Plex-Media", HOSTILE[kind])  # written for the one the fixed order puts second
    assert front_matter(page) is None
    renderer = Renderer(tmp_path)
    renderer.render(_snap(*CASES["titles that differ only in punctuation"]))
    coded = tmp_path / "services" / f"plex-media-{_code('h/Plex-Media')}.md"
    assert front_matter(coded)["title"] == "Plex-Media"  # the fixed order decided
    assert renderer.notices == [
        f'"Plex-Media" on h has its own page services/{coded.name} (its name matched "Plex Media")'
    ]
    assert (tmp_path / "index.md").is_file()


@pytest.mark.parametrize("kind", HOSTILE)
def test_a_host_page_yaml_cannot_read_has_no_owner_and_the_render_goes_on(tmp_path, kind):
    page = tmp_path / "hosts" / "nas-1.md"
    _hostile(page, "nas-1", HOSTILE[kind], host=None)
    renderer = Renderer(tmp_path)
    renderer.render(_snap(*CASES["host names that differ only in case and spaces"]))
    assert front_matter(tmp_path / "hosts" / f"nas-1-{_code('nas-1')}.md")["title"] == "nas-1"
    assert renderer.notices == [
        f'host "nas-1" has its own page hosts/nas-1-{_code("nas-1")}.md (its name matched "NAS 1")'
    ]


def test_only_the_start_of_a_page_is_read(tmp_path, monkeypatch):
    page = tmp_path / "p.md"
    page.write_text("---\ntitle: Plex-Media\nhost: h\n---\n" + "x" * 2_000_000)
    assert front_matter(page) == {"title": "Plex-Media", "host": "h"}  # a long body is fine
    _hostile(page, "Plex-Media", "")
    assert front_matter(page) == {"title": "Plex-Media", "host": "h"}
    sizes = []
    real_open = Path.open

    def spy(self, *args, **kwargs):
        f = real_open(self, *args, **kwargs)
        real_read = f.read
        f.read = lambda n=-1: sizes.append(n) or real_read(n)
        return f

    monkeypatch.setattr(Path, "open", spy)
    front_matter(page)
    assert sizes == [renderer_module.FRONT_MATTER_MAX]


def test_front_matter_never_raises(tmp_path, monkeypatch):
    page = tmp_path / "p.md"
    page.write_text("---\ntitle: a\n---\n")

    def boom(text):
        raise TypeError("anything at all")

    monkeypatch.setattr(renderer_module.yaml, "safe_load", boom)
    assert front_matter(page) is None


# --- the `id` key: look-alikes with the same title and host keep their pages ---------------------------


def _ids(out: Path) -> dict[str, str | None]:
    return {p.stem: front_matter(p).get("id") for p in (out / "services").glob("*.md")}


def _notes_of(out: Path) -> dict[str, str]:
    """The notes text of each page that has any, by the id in its front matter."""
    found = {}
    for p in (out / "services").glob("*.md"):
        kept = notes.extract(p.read_text())
        if kept and kept.strip() != notes.PLACEHOLDER:
            found[front_matter(p)["id"]] = kept.strip()
    return found


def _plex(*names, host="h"):
    return _snap([host], [_svc(n, host, "Plex") for n in names])


def test_a_newcomer_with_the_same_title_and_host_does_not_take_a_page(tmp_path):
    Renderer(tmp_path).render(_plex("pLEX", "plex"))
    plain, coded = "plex-h", f"plex-h-{_code('h/Plex/h/plex/container')}"
    assert _ids(tmp_path) == {plain: "h/pLEX/container", coded: "h/plex/container"}
    _add_notes(tmp_path / "services" / f"{plain}.md", "About pLEX.")
    _add_notes(tmp_path / "services" / f"{coded}.md", "About plex.")

    renderer = Renderer(tmp_path)
    renderer.render(_plex("pLEX", "plex", "PLEX"))  # PLEX comes first in the fixed order
    newcomer = f"plex-h-{_code('h/Plex/h/PLEX/container')}"
    assert _ids(tmp_path) == {
        plain: "h/pLEX/container",
        coded: "h/plex/container",
        newcomer: "h/PLEX/container",
    }
    assert _notes_of(tmp_path) == {"h/pLEX/container": "About pLEX.", "h/plex/container": "About plex."}
    assert not (tmp_path / "archive").exists()
    assert renderer.notices == [
        f'"Plex" on h has its own page services/{newcomer}.md (its name matched "Plex")',
        f'"Plex" on h has its own page services/{coded}.md (its name matched "Plex")',
    ]


def test_look_alikes_come_and_go_over_several_runs_and_nothing_moves(tmp_path):
    plain = "plex-h"
    coded = {n: f"plex-h-{_code(f'h/Plex/h/{n}/container')}" for n in ("plex", "PLEX", "Plex")}

    Renderer(tmp_path).render(_plex("pLEX", "plex"))
    _add_notes(tmp_path / "services" / f"{plain}.md", "About pLEX.")
    _add_notes(tmp_path / "services" / f"{coded['plex']}.md", "About plex.")

    Renderer(tmp_path).render(_plex("pLEX", "plex", "PLEX"))
    _add_notes(tmp_path / "services" / f"{coded['PLEX']}.md", "About PLEX.")
    want = {plain: "h/pLEX/container", coded["plex"]: "h/plex/container", coded["PLEX"]: "h/PLEX/container"}
    assert _ids(tmp_path) == want

    Renderer(tmp_path).render(_plex("PLEX", "pLEX"))  # plex goes away: its page is archived with its notes
    assert _ids(tmp_path) == {plain: "h/pLEX/container", coded["PLEX"]: "h/PLEX/container"}
    assert "About plex." in (tmp_path / "archive" / "services" / f"{coded['plex']}.md").read_text()

    Renderer(tmp_path).render(_plex("plex", "Plex", "PLEX", "pLEX"))  # plex is back, and one more
    assert _ids(tmp_path) == want | {coded["Plex"]: "h/Plex/container"}
    assert _notes_of(tmp_path) == {"h/pLEX/container": "About pLEX.", "h/PLEX/container": "About PLEX."}

    for order in (_plex("pLEX", "Plex", "PLEX", "plex"), _plex("plex", "PLEX", "Plex", "pLEX")):
        Renderer(tmp_path).render(order)
        assert _ids(tmp_path) == want | {coded["Plex"]: "h/Plex/container"}
    assert _notes_of(tmp_path) == {"h/pLEX/container": "About pLEX.", "h/PLEX/container": "About PLEX."}
    assert sorted(p.name for p in (tmp_path / "archive" / "services").iterdir()) == [f"{coded['plex']}.md"]


def test_a_coded_page_stays_when_its_code_would_now_come_from_the_id(tmp_path):
    # "Plex" and "plex" on h: one name, and each host and title its own, so the code is from host and title.
    Renderer(tmp_path).render(_snap(["h"], [_svc("a", "h", "Plex"), _svc("b", "h", "plex")]))
    short = f"plex-h-{_code('h/plex')}"
    assert _ids(tmp_path) == {"plex-h": "h/a/container", short: "h/b/container"}
    _add_notes(tmp_path / "services" / f"{short}.md", "About b.")
    # c has b's host and title: new codes come from the id now, but b keeps the page it has.
    snap = _snap(["h"], [_svc("a", "h", "Plex"), _svc("b", "h", "plex"), _svc("c", "h", "plex")])
    Renderer(tmp_path).render(snap)
    assert _ids(tmp_path) == {
        "plex-h": "h/a/container",
        short: "h/b/container",
        f"plex-h-{_code('h/plex/h/c/container')}": "h/c/container",
    }
    assert _notes_of(tmp_path) == {"h/b/container": "About b."}
    assert Renderer(tmp_path).render(snap) == []


def test_an_owner_page_without_an_id_falls_back_to_title_and_host_only_when_one_matches(tmp_path):
    page = tmp_path / "services" / "plex-h.md"
    _hostile(page, "Plex", "")  # an older page: title and host, no id
    Renderer(tmp_path).render(_plex("pLEX", "plex"))
    assert _ids(tmp_path)["plex-h"] == "h/pLEX/container"  # both match: the fixed order

    page = tmp_path / "b" / "services" / "plex-h.md"
    _hostile(page, "plex", "")
    Renderer(tmp_path / "b").render(_snap(["h"], [_svc("a", "h", "Plex"), _svc("b", "h", "plex")]))
    assert _ids(tmp_path / "b")["plex-h"] == "h/b/container"  # only b matches


def test_an_owner_page_with_an_unknown_id_falls_back_to_title_and_host(tmp_path):
    _hostile(tmp_path / "services" / "plex-h.md", "plex", "id: h/gone\n")
    Renderer(tmp_path).render(_snap(["h"], [_svc("a", "h", "Plex"), _svc("b", "h", "plex")]))
    assert _ids(tmp_path)["plex-h"] == "h/b/container"


def _guest(vmid: int, name: str = "Plex") -> Service:
    return parse_guest({"name": name, "vmid": vmid, "status": "running"}, {}, "h", "vm")


def _lab(services: list[Service]) -> Snapshot:
    return Snapshot(hosts=[Host(name="h", kind="proxmox-node")], services=services, taken_at=TAKEN_AT)


def _service_pages_written(out: Path, snap: Snapshot) -> list[str]:
    return sorted(p.name for p in Renderer(out).render(snap) if p.parent.name == "services")


def test_two_guests_with_one_name_keep_their_pages_and_notes_in_any_scan_order(tmp_path):
    first, second = _guest(101), _guest(102)
    assert first.id == second.id == "h/Plex"
    Renderer(tmp_path).render(_lab([first, second]))
    coded = f"plex-h-{_code('h/Plex/h/Plex/vm/102')}"
    assert _ids(tmp_path) == {"plex-h": "h/Plex/vm/101", coded: "h/Plex/vm/102"}
    _add_notes(tmp_path / "services" / "plex-h.md", "VM 101 notes.")
    _add_notes(tmp_path / "services" / f"{coded}.md", "VM 102 notes.")
    for run in range(4):
        lab = [first, second] if run % 2 else [second, first]
        assert _service_pages_written(tmp_path, _lab(lab)) == []  # notes and pages stay put
        assert _ids(tmp_path) == {"plex-h": "h/Plex/vm/101", coded: "h/Plex/vm/102"}
        assert _notes_of(tmp_path) == {"h/Plex/vm/101": "VM 101 notes.", "h/Plex/vm/102": "VM 102 notes."}
        assert "| Proxmox ID | 101 (VM) |" in (tmp_path / "services" / "plex-h.md").read_text()
        assert "| Proxmox ID | 102 (VM) |" in (tmp_path / "services" / f"{coded}.md").read_text()
    assert Renderer(tmp_path).render(_lab([first, second])) == []  # the same scan again: nothing written
    assert not (tmp_path / "archive").exists()


def test_a_vm_and_a_container_that_share_a_name_keep_their_pages(tmp_path):
    vm = Service(name="x", host="h", kind="vm", display_name="Plex", vmid=7)
    box = Service(name="x", host="h", kind="container", display_name="Plex")
    Renderer(tmp_path).render(_lab([vm, box]))
    coded = f"plex-h-{_code('h/Plex/h/x/vm/7')}"
    assert _ids(tmp_path) == {"plex-h": "h/x/container", coded: "h/x/vm/7"}  # the fixed order: by kind
    _add_notes(tmp_path / "services" / "plex-h.md", "The container.")
    _add_notes(tmp_path / "services" / f"{coded}.md", "The VM.")
    for lab in ([box, vm], [vm, box], [box, vm]):
        assert _service_pages_written(tmp_path, _lab(lab)) == []
        assert _notes_of(tmp_path) == {"h/x/container": "The container.", "h/x/vm/7": "The VM."}
    assert Renderer(tmp_path).render(_lab([box, vm])) == []


def test_services_the_same_in_every_way_get_two_pages_whatever_the_scan_order(tmp_path):
    twins = [Service(name="x", host="h", kind="manual", display_name="Plex") for _ in range(2)]
    Renderer(tmp_path).render(_lab(twins))
    pages = {p.name: p.read_text() for p in (tmp_path / "services").glob("*.md")}
    assert len(pages) == 2 and set(_ids(tmp_path).values()) == {"h/x/manual"}
    twins.reverse()
    assert _service_pages_written(tmp_path, _lab(twins)) == []


def _runs(tmp_path: Path, labs: list[list[Service]], check) -> None:
    """Render each lab in both scan orders, then `check`; the same scan again writes nothing."""
    for lab in labs:
        for order in (lab, lab[::-1]):
            Renderer(tmp_path).render(_lab(order))
            check(lab)
            assert Renderer(tmp_path).render(_lab(order)) == []  # the same scan again


def test_a_look_alike_that_comes_and_goes_never_moves_the_plain_page(tmp_path):
    # VM 102 "Plex" has the plain page; VM 101, also "Plex", comes, goes and comes back.
    v101, v102, v103 = _guest(101, "Plex"), _guest(102, "Plex"), _guest(103, "plex")
    Renderer(tmp_path).render(_lab([v102, v103]))
    plain, c103 = "plex-h", f"plex-h-{_code('h/plex')}"
    assert _ids(tmp_path) == {plain: "h/Plex/vm/102", c103: "h/plex/vm/103"}
    _add_notes(tmp_path / "services" / f"{plain}.md", "VM 102 notes.")
    _add_notes(tmp_path / "services" / f"{c103}.md", "VM 103 notes.")
    c101 = f"plex-h-{_code('h/Plex/h/Plex/vm/101')}"

    def check(lab):
        want = {plain: "h/Plex/vm/102", c103: "h/plex/vm/103"} | (
            {c101: "h/Plex/vm/101"} if v101 in lab else {}
        )
        assert _ids(tmp_path) == want
        assert _notes_of(tmp_path) == {"h/Plex/vm/102": "VM 102 notes.", "h/plex/vm/103": "VM 103 notes."}
        assert not (tmp_path / "archive").exists()

    _runs(tmp_path, [[v101, v102, v103], [v102, v103], [v103, v101, v102], [v102, v103]], check)


def test_a_look_alike_that_comes_and_goes_never_moves_a_coded_page(tmp_path):
    # VM 100 "PLEX" has the plain page and VM 102 "Plex" a coded one, from its host and title alone. VM 101,
    # also "Plex", makes new codes come from the page key; VM 102 keeps its page both ways.
    v100, v101, v102 = _guest(100, "PLEX"), _guest(101, "Plex"), _guest(102, "Plex")
    Renderer(tmp_path).render(_lab([v100, v102]))
    plain, c102 = "plex-h", f"plex-h-{_code('h/Plex')}"
    assert _ids(tmp_path) == {plain: "h/PLEX/vm/100", c102: "h/Plex/vm/102"}
    _add_notes(tmp_path / "services" / f"{plain}.md", "VM 100 notes.")
    _add_notes(tmp_path / "services" / f"{c102}.md", "VM 102 notes.")
    c101 = f"plex-h-{_code('h/Plex/h/Plex/vm/101')}"

    def check(lab):
        want = {plain: "h/PLEX/vm/100", c102: "h/Plex/vm/102"} | (
            {c101: "h/Plex/vm/101"} if v101 in lab else {}
        )
        assert _ids(tmp_path) == want
        assert _notes_of(tmp_path) == {"h/PLEX/vm/100": "VM 100 notes.", "h/Plex/vm/102": "VM 102 notes."}
        assert not (tmp_path / "archive").exists()

    _runs(tmp_path, [[v100, v101, v102], [v100, v102], [v102, v101, v100], [v100, v102]], check)


def test_a_coded_page_made_while_the_title_was_shared_is_kept_when_it_no_longer_is(tmp_path):
    v100, v101, v102 = _guest(100, "PLEX"), _guest(101, "Plex"), _guest(102, "Plex")
    Renderer(tmp_path).render(_lab([v100, v101, v102]))
    c102 = f"plex-h-{_code('h/Plex/h/Plex/vm/102')}"  # made from the page key: VM 101 has its host and title
    assert _ids(tmp_path)[c102] == "h/Plex/vm/102"
    _add_notes(tmp_path / "services" / f"{c102}.md", "VM 102 notes.")
    for lab in ([v100, v102], [v100, v101, v102], [v102, v100]):
        Renderer(tmp_path).render(_lab(lab))
        assert _ids(tmp_path)[c102] == "h/Plex/vm/102"
        assert _notes_of(tmp_path) == {"h/Plex/vm/102": "VM 102 notes."}
    assert not (tmp_path / "archive").exists()


def test_an_older_page_with_the_short_id_is_kept_only_when_one_service_has_that_id(tmp_path):
    v101, v102, v103 = _guest(101, "Plex"), _guest(102, "Plex"), _guest(103, "plex")
    page = tmp_path / "services" / "plex-h.md"
    _hostile(page, "plex", "id: h/plex\n")  # written before page ids had the kind: VM 103's (h/plex)
    Renderer(tmp_path).render(_lab([v102, v103]))
    assert _ids(tmp_path)["plex-h"] == "h/plex/vm/103"  # the only service with that id; now the full form
    _hostile(page, "Plex", "id: h/Plex\n")  # h/Plex is the id of both VM 101 and VM 102
    Renderer(tmp_path).render(_lab([v101, v102, v103]))
    assert _ids(tmp_path)["plex-h"] == "h/Plex/vm/101"  # neither is named by it: the fixed order


def test_a_new_code_never_takes_a_page_written_for_another_service(tmp_path):
    # Both titles give plex-media, and the SHA-256 of "h/<title>" starts with 5b50911b for both.
    b_title, c_title = "Plex*!$$!Media", "Plex|~($!Media"
    assert _code(f"h/{b_title}") == _code(f"h/{c_title}") == "5b50911b"
    owner = _svc("a", "h", "Plex Media")
    Renderer(tmp_path).render(_snap(["h"], [owner, _svc("b", "h", b_title)]))
    b_page = tmp_path / "services" / "plex-media-5b50911b.md"
    assert front_matter(b_page)["id"] == "h/b/container"
    _add_notes(b_page, "About b.")
    renderer = Renderer(tmp_path)
    renderer.render(_snap(["h"], [owner, _svc("c", "h", c_title)]))  # b goes, c comes
    c_page = f"plex-media-{_code(f'h/{c_title}', 10)}"
    assert _ids(tmp_path) == {"plex-media": "h/a/container", c_page: "h/c/container"}
    assert _notes_of(tmp_path) == {}
    assert "About b." in (tmp_path / "archive" / "services" / b_page.name).read_text()
    assert renderer.notices == [
        f'"{c_title}" on h has its own page services/{c_page}.md (its name matched "Plex Media")'
    ]


def test_a_new_code_never_takes_an_older_page_without_an_id_written_for_another_service(tmp_path):
    folder = tmp_path / "services"
    _hostile(folder / f"plex-media-{_code('h/Plex-Media')}.md", "Plex Other", "")  # no id: title and host
    Renderer(tmp_path).render(_snap(*CASES["titles that differ only in punctuation"]))
    assert _ids(tmp_path)[f"plex-media-{_code('h/Plex-Media', 10)}"] == "h/b/container"


def test_a_new_host_code_never_takes_a_page_written_for_another_host(tmp_path):
    folder = tmp_path / "hosts"
    _hostile(folder / f"nas-1-{_code('nas-1')}.md", "nas 1", "", host=None)  # another host's page
    Renderer(tmp_path).render(_snap(*CASES["host names that differ only in case and spaces"]))
    assert front_matter(folder / f"nas-1-{_code('nas-1', 10)}.md")["title"] == "nas-1"


def test_only_pages_of_look_alikes_have_an_id(tmp_path):
    _, snap = demo_snapshots()
    enrich(snap.services, load_catalog())
    Renderer(tmp_path / "demo", licensing.PRO_FEATURES).render(snap, [], None)
    assert not any("\nid: " in p.read_text() for p in (tmp_path / "demo").rglob("*.md"))
    Renderer(tmp_path / "a").render(_snap(*CASES["titles that differ only in punctuation"]))
    assert _ids(tmp_path / "a") == {
        "plex-media": "h/a/container",
        f"plex-media-{_code('h/Plex-Media')}": "h/b/container",
    }
    text = (tmp_path / "a" / "services" / "plex-media.md").read_text()
    assert text.startswith("---\ntitle: Plex Media\nhost: h\nid: h/a/container\ncategory: Other\n")
    Renderer(tmp_path / "a").render(_snap(["h"], [_svc("a", "h", "Plex Media")]))  # alone again: no id
    assert _ids(tmp_path / "a") == {"plex-media": None}


@pytest.mark.parametrize("folder", ["services", "hosts"])
def test_a_page_at_the_plain_name_that_is_not_utf8_is_skipped_and_the_command_exits_0(tmp_path, folder):
    config = tmp_path / "homelabscribe.yaml"
    config.write_text(
        f"output_dir: {tmp_path / 'docs'}\n"
        f"state_dir: {tmp_path / 'state'}\n"
        f"html_dir: {tmp_path / 'html'}\n"
        "docker_hosts: []\n"
        "manual_services:\n"
        "  - {name: a, host: NAS 1, title: Plex Media}\n"
        "  - {name: b, host: nas-1, title: Plex-Media}\n"
    )
    assert runner.invoke(app, ["run", "-c", str(config)]).exit_code == 0
    name = "plex-media" if folder == "services" else "nas-1"
    page = tmp_path / "docs" / folder / f"{name}.md"
    page.write_bytes(page.read_text().encode("utf-16"))
    before = page.read_bytes()
    for args, status in (
        (["render", "-c", str(config)], 0),
        (["run", "-c", str(config)], 0),
        (["run", "-c", str(config), "--strict"], 1),
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == status, (args, result.output)
        assert result.output.count(f"skipped: {folder}/{name}.md (not UTF-8, left as it is)") == 1
        assert "Traceback" not in result.output and "note: " in result.output
        assert page.read_bytes() == before
    coded = {"services": f"plex-media-{_code('nas-1/Plex-Media')}", "hosts": f"nas-1-{_code('nas-1')}"}
    assert (tmp_path / "docs" / folder / f"{coded[folder]}.md").is_file()
