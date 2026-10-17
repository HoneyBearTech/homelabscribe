from homelabscribe import licensing, manual, pipeline
from homelabscribe.config import BreakGlassConfig, Config, ManualServiceConfig
from homelabscribe.models import Host
from homelabscribe.renderer import Renderer


def _cfg(tmp_path, *entries):
    return Config(
        output_dir=tmp_path / "out",
        state_dir=tmp_path / "state",
        manual_services=list(entries),
        docker_hosts=[],
    )


def test_build_adds_a_host_only_when_it_is_not_already_scanned():
    entries = [
        ManualServiceConfig(name="pihole", host="pi"),
        ManualServiceConfig(name="nas-ui", host="pi"),
        ManualServiceConfig(name="thing", host="nova"),
    ]
    hosts, services = manual.build(entries, {"nova"})
    assert [(h.name, h.kind) for h in hosts] == [("pi", "manual")]
    assert [s.host for s in services] == ["pi", "pi", "nova"]
    assert all(s.kind == "manual" and s.status == manual.NOT_SCANNED for s in services)


def test_dns_is_flagged_by_name_or_explicitly():
    _, services = manual.build(
        [
            ManualServiceConfig(name="Pihole", host="pi"),
            ManualServiceConfig(name="resolver", host="pi", dns=True),
            ManualServiceConfig(name="router", host="pi"),
        ],
        set(),
    )
    assert [s.is_dns for s in services] == [True, True, False]


def test_manual_service_flows_through_scan_and_catalog(tmp_path):
    cfg = _cfg(tmp_path, ManualServiceConfig(name="pihole", host="pi", url="http://pi.lan/admin"))
    result = pipeline.scan(cfg, licensing.PRO_FEATURES, collectors=[])
    (svc,) = result.snapshot.services
    assert svc.title == "Pi-hole" and svc.category == "Network"  # from the built-in catalog
    assert svc.url == "http://pi.lan/admin"  # explicit config wins
    assert [h.name for h in result.snapshot.hosts] == ["pi"]


def test_explicit_fields_override_the_catalog():
    from homelabscribe.enrich import enrich, load_catalog

    _, services = manual.build(
        [
            ManualServiceConfig(
                name="pihole", host="pi", title="House DNS", category="Core", description="Ours"
            )
        ],
        set(),
    )
    enrich(services, load_catalog())
    assert (services[0].title, services[0].category, services[0].description) == ("House DNS", "Core", "Ours")


def test_pages_and_break_glass_for_a_hand_described_pihole(tmp_path):
    cfg = _cfg(
        tmp_path, ManualServiceConfig(name="pihole", host="pi", restart="Unplug the Pi, wait, plug it in.")
    )
    snap = pipeline.scan(cfg, licensing.PRO_FEATURES, collectors=[]).snapshot
    bg = BreakGlassConfig(owner="Sam", critical=["pi-hole"])
    Renderer(tmp_path / "docs", licensing.PRO_FEATURES).render(snap, [], bg)
    page = (tmp_path / "docs" / "services" / "pi-hole.md").read_text()
    assert "Unplug the Pi, wait, plug it in." in page and "docker" not in page.lower().split("## notes")[0]
    index = (tmp_path / "docs" / "index.md").read_text()
    assert "1 added by hand (not scanned)" in index and "0 not running" in index
    assert "Added by hand" in (tmp_path / "docs" / "hosts" / "pi.md").read_text()
    guide = (tmp_path / "docs" / "break-glass.md").read_text()
    assert "look up website names" in guide and "**pi**" in guide and "### Pi-hole" in guide


def test_host_model_labels():
    assert Host(name="a", kind="manual").type_label == "Added by hand"
    assert Host(name="a", kind="docker").type_label == "Docker host"


def test_break_glass_names_every_dns_server(tmp_path):
    cfg = _cfg(
        tmp_path,
        ManualServiceConfig(name="pihole", host="pi"),
        ManualServiceConfig(name="pihole", host="pi-two"),
    )
    snap = pipeline.scan(cfg, licensing.PRO_FEATURES, collectors=[]).snapshot
    Renderer(tmp_path / "docs", licensing.PRO_FEATURES).render(snap, [], BreakGlassConfig(owner="Sam"))
    guide = (tmp_path / "docs" / "break-glass.md").read_text()
    assert "**Pi-hole** (on the machine **pi**) and **Pi-hole** (on the machine **pi-two**)" in guide
    assert "those machines, one at a time (see below)," in guide
