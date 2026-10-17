import json

import pytest

from homelabscribe import licensing, pipeline
from homelabscribe.collectors.npm import NpmCollector, parse_proxy_hosts
from homelabscribe.config import Config, NpmConfig
from homelabscribe.enrich import enrich, link_routes, load_catalog
from homelabscribe.models import Host, Port, Route, Service, Snapshot
from homelabscribe.renderer import Renderer

ITEMS = [
    {
        "domain_names": ["sonarr.example.com"],
        "forward_scheme": "http",
        "forward_host": "192.168.1.68",
        "forward_port": 8989,
        "certificate_id": 3,
        "enabled": True,
    },
    {
        "domain_names": ["old.example.com"],
        "forward_host": "192.168.1.68",
        "forward_port": 9,
        "enabled": False,
    },
    {"domain_names": [], "forward_host": "x", "forward_port": 1},  # no domain: skipped
    {"domain_names": ["broken.example.com"], "forward_host": "x"},  # no port: skipped
]


def test_parse_proxy_hosts_keeps_valid_entries_only():
    routes = parse_proxy_hosts(ITEMS, "npm")
    assert [r.domains for r in routes] == [["sonarr.example.com"], ["old.example.com"]]
    assert (routes[0].https, routes[0].enabled) == (True, True)
    assert (routes[1].https, routes[1].enabled) == (False, False)


class _Resp:
    def __init__(self, data, ok=True):
        self._data, self._ok = data, ok

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("401")

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def post(self, url, json=None, **kw):
        self.calls.append(("POST", url, json, kw))
        return _Resp({"token": "SESSION-TOKEN"}, self.ok)

    def get(self, url, headers=None, **kw):
        self.calls.append(("GET", url, headers, kw))
        return _Resp(ITEMS)


def _cfg():
    return NpmConfig(url="http://npm:81/", identity="viewer@example.com", secret="s3cret")


def test_collector_logs_in_then_reads_proxy_hosts():
    session = FakeSession()
    routes = NpmCollector(_cfg(), session=session).collect()
    (post, get) = session.calls
    assert post[1] == "http://npm:81/api/tokens" and post[2] == {
        "identity": "viewer@example.com",
        "secret": "s3cret",
    }
    assert get[1] == "http://npm:81/api/nginx/proxy-hosts" and get[2] == {
        "Authorization": "Bearer SESSION-TOKEN"
    }
    assert len(routes) == 2 and all(r.proxy == "npm" for r in routes)


def test_collector_fails_cleanly_on_a_bad_login():
    with pytest.raises(RuntimeError):
        NpmCollector(_cfg(), session=FakeSession(ok=False)).collect()


def _lab():
    hosts = [Host(name="nova", kind="docker", link_host="192.168.1.68")]
    sonarr = Service(name="sonarr", host="nova", kind="container", ports=[Port(container=8989, host=8988)])
    radarr = Service(name="radarr", host="nova", kind="container", ports=[Port(container=7878, host=7878)])
    return hosts, [sonarr, radarr]


def test_route_matches_by_machine_and_published_port():
    hosts, svcs = _lab()
    routes = [
        Route(
            proxy="npm", domains=["s.example.com"], forward_host="192.168.1.68", forward_port=8988, https=True
        )
    ]
    link_routes(routes, hosts, svcs)
    assert routes[0].service == "nova/sonarr" and svcs[0].url == "https://s.example.com"


def test_route_matches_by_container_name_and_container_port():
    hosts, svcs = _lab()
    routes = [Route(proxy="npm", domains=["r.example.com"], forward_host="radarr", forward_port=7878)]
    link_routes(routes, hosts, svcs)
    assert routes[0].service == "nova/radarr" and svcs[1].url == "http://r.example.com"


def test_unmatched_wrong_port_disabled_and_explicit_url():
    hosts, svcs = _lab()
    svcs[1].url = "https://label-wins.example.com"
    routes = [
        Route(proxy="npm", domains=["a.example.com"], forward_host="192.168.1.68", forward_port=1234),
        Route(
            proxy="npm",
            domains=["b.example.com"],
            forward_host="192.168.1.68",
            forward_port=8988,
            enabled=False,
        ),
        Route(proxy="npm", domains=["c.example.com"], forward_host="192.168.1.68", forward_port=7878),
    ]
    link_routes(routes, hosts, svcs)
    assert routes[0].service is None
    assert routes[1].service == "nova/sonarr" and svcs[0].url is None  # matched, but disabled: no URL
    assert svcs[1].url == "https://label-wins.example.com"  # an explicit URL is never overwritten


def test_scan_collects_routes_records_errors_and_never_stores_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(NpmCollector, "collect", lambda self: parse_proxy_hosts(ITEMS, self.cfg.name))
    cfg = Config(state_dir=tmp_path / "s", output_dir=tmp_path / "o", docker_hosts=[], npm=[_cfg()])
    result = pipeline.scan(cfg, licensing.PRO_FEATURES, collectors=[])
    assert len(result.snapshot.routes) == 2 and not result.errors
    stored = "".join(p.read_text() for p in (tmp_path / "s").rglob("*.json"))
    assert "s3cret" not in stored and "SESSION-TOKEN" not in stored and "viewer@example.com" not in stored

    def boom(self):
        raise RuntimeError("down")

    monkeypatch.setattr(NpmCollector, "collect", boom)
    result = pipeline.scan(cfg, licensing.PRO_FEATURES, collectors=[])
    assert result.errors == ["Nginx Proxy Manager 'npm': RuntimeError"]
    assert len(result.snapshot.routes) == 2  # the last known routes are kept, not dropped


def test_routes_page_and_index_link(tmp_path):
    hosts, svcs = _lab()
    routes = [
        Route(
            proxy="npm", domains=["s.example.com"], forward_host="192.168.1.68", forward_port=8988, https=True
        ),
        Route(proxy="npm", domains=["lost.example.com"], forward_host="10.9.9.9", forward_port=80),
    ]
    link_routes(routes, hosts, svcs)
    snap = Snapshot(hosts=hosts, services=svcs, routes=routes)
    Renderer(tmp_path).render(snap)
    page = (tmp_path / "routes.md").read_text()
    assert "[sonarr](services/sonarr.md)" in page
    assert "_not matched to a scanned service_" in page and "`http://10.9.9.9:80`" in page
    assert "[Reverse proxy routes](routes.md)" in (tmp_path / "index.md").read_text()
    assert json.loads(snap.model_dump_json())["routes"][0]["service"] == "nova/sonarr"


def test_route_matches_a_hand_described_service_by_the_address_of_its_url():
    from homelabscribe.config import ManualServiceConfig
    from homelabscribe.manual import build

    entries = [
        ManualServiceConfig(name="pihole", host="pi", url="http://10.0.0.51/admin"),
        ManualServiceConfig(name="nas-ui", host="nas", url="http://10.0.0.20:5000"),
        ManualServiceConfig(name="nas-files", host="nas", url="http://10.0.0.20:5001"),
    ]
    hosts, svcs = build(entries, set())
    enrich(svcs, load_catalog())  # the pipeline always enriches first; this is what sets each URL
    routes = [
        Route(
            proxy="npm", domains=["dns.example.com"], forward_host="10.0.0.51", forward_port=443, https=True
        ),
        Route(proxy="npm", domains=["nas.example.com"], forward_host="10.0.0.20", forward_port=5000),
    ]
    link_routes(routes, hosts, svcs)
    assert routes[0].service == "pi/pihole"
    assert svcs[0].url == "http://10.0.0.51/admin"  # an explicit URL is never replaced
    assert routes[1].service is None  # two services share that address: not guessed


def test_doctor_reports_the_npm_login(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from homelabscribe.cli import app

    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "docker_hosts: []\nnpm:\n  - {name: edge, url: 'http://x:81', identity: a@b.c, secret: pw}\n"
    )
    monkeypatch.setattr(NpmCollector, "collect", lambda self: parse_proxy_hosts(ITEMS, "edge"))
    out = CliRunner().invoke(app, ["doctor", "-c", str(cfg)]).output
    assert "Nginx Proxy Manager 'edge': OK (2 proxy hosts)" in out and "pw" not in out

    def boom(self):
        raise RuntimeError("401")

    monkeypatch.setattr(NpmCollector, "collect", boom)
    assert (
        "Nginx Proxy Manager 'edge': FAILED (RuntimeError)"
        in CliRunner().invoke(app, ["doctor", "-c", str(cfg)]).output
    )


def test_parse_aliases_drops_the_container_id_and_own_name():
    from homelabscribe.collectors.docker import parse_aliases

    nets = {
        "app_default": {"Aliases": ["argus-grafana", "grafana", "argus-grafana-1", "0a1b2c3d4e5f"]},
        "proxy_default": {"Aliases": ["argus-grafana", None, ""]},
    }
    assert parse_aliases(nets, "argus-grafana-1") == ["argus-grafana", "grafana"]
    assert parse_aliases(None, "x") == [] and parse_aliases({"n": None}, "x") == []


def test_route_matches_a_network_alias_only_when_it_is_unique():
    hosts = [Host(name="a", kind="docker"), Host(name="b", kind="docker")]
    ports = [Port(container=3000, host=3000)]
    one = Service(name="argus-grafana-1", host="a", kind="container", ports=ports, aliases=["argus-grafana"])
    twin_a = Service(name="web-1", host="a", kind="container", ports=ports, aliases=["web"])
    twin_b = Service(name="web-1", host="b", kind="container", ports=ports, aliases=["web"])
    routes = [
        Route(
            proxy="npm",
            domains=["g.example.com"],
            forward_host="argus-grafana",
            forward_port=3000,
            https=True,
        ),
        Route(proxy="npm", domains=["w.example.com"], forward_host="web", forward_port=3000),
        Route(proxy="npm", domains=["x.example.com"], forward_host="argus-grafana", forward_port=9999),
    ]
    link_routes(routes, hosts, [one, twin_a, twin_b])
    assert routes[0].service == "a/argus-grafana-1" and one.url == "https://g.example.com"
    assert routes[1].service is None  # two services answer to "web": not guessed
    assert routes[2].service is None  # right alias, wrong port
