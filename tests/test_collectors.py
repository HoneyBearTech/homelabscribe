import json
from pathlib import Path

from homelabscribe.collectors.docker import DockerCollector, parse_container
from homelabscribe.collectors.proxmox import ProxmoxCollector, parse_guest
from homelabscribe.config import DockerHostConfig, ProxmoxConfig

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sonarr_inspect.json").read_text())


def test_parse_container_basics():
    s = parse_container(FIXTURE, "nova")
    assert s.name == "sonarr" and s.host == "nova" and s.status == "running"
    assert s.compose_project == "media" and s.compose_service == "sonarr"
    assert s.depends_on == ["prowlarr", "qbittorrent"]
    assert s.restart_policy == "unless-stopped"
    assert s.networks == ["media_default", "proxy"]


def test_ports_deduplicate_ipv4_ipv6_and_keep_internal():
    s = parse_container(FIXTURE, "nova")
    assert [(p.host, p.container) for p in s.ports] == [(None, 9898), (8989, 8989)]


def test_stopped_container_falls_back_to_configured_ports():
    attrs = json.loads(json.dumps(FIXTURE))
    attrs["State"]["Status"] = "exited"
    attrs["NetworkSettings"]["Ports"] = {}
    attrs["Config"]["ExposedPorts"] = {"8989/tcp": {}, "9898/tcp": {}}
    attrs["HostConfig"]["PortBindings"] = {"8989/tcp": [{"HostIp": "", "HostPort": "8989"}]}
    s = parse_container(attrs, "nova")
    assert [(p.host, p.container) for p in s.ports] == [(None, 9898), (8989, 8989)]


def test_volume_mount_uses_volume_name():
    s = parse_container(FIXTURE, "nova")
    config = next(m for m in s.mounts if m.destination == "/config")
    assert config.source == "sonarr_config"
    assert next(m for m in s.mounts if m.destination == "/etc/localtime").read_only


def test_secrets_never_stored():
    s = parse_container(FIXTURE, "nova")
    dumped = s.model_dump_json()
    assert "supersecretvalue123" not in dumped
    assert "$apr1$" not in dumped
    assert "SONARR__API_KEY" in s.env_names  # the name is fine
    assert "org.opencontainers.image.version" not in s.labels


class FakeDockerAPI:
    def info(self):
        return {
            "OperatingSystem": "Ubuntu 24.04",
            "NCPU": 8,
            "MemTotal": 32 * 1024**3,
            "ServerVersion": "27.3.1",
        }

    def containers(self, all):
        return [{"Id": FIXTURE["Id"]}]

    def inspect_container(self, cid):
        return FIXTURE


class FakeDockerClient:
    api = FakeDockerAPI()


def test_docker_collector_with_fake_client():
    hosts, services = DockerCollector(DockerHostConfig(name="nova"), client=FakeDockerClient()).collect()
    assert hosts[0].memory_gb == 32.0 and hosts[0].address is None
    assert [s.name for s in services] == ["sonarr"]


def test_parse_guest():
    s = parse_guest(
        {"vmid": 101, "name": "homeassistant", "status": "running", "maxmem": 4 * 1024**3, "cpus": 2},
        {"cores": 2, "tags": "home;iot", "net0": "virtio=...", "description": "HAOS\nZigbee on USB"},
        "pve1",
        "vm",
    )
    assert s.vmid == 101 and s.memory_gb == 4.0 and s.tags == ["home", "iot"]
    assert s.networks == ["net0"] and s.notes.startswith("HAOS")


class _Endpoint:
    """Tiny stand-in for proxmoxer's chained API."""

    def __init__(self, data, children=None):
        self.data, self.children = data, children or {}

    def get(self):
        return self.data

    def __call__(self, key):
        return self.children[str(key)]

    def __getattr__(self, name):
        return self.children[name]


def test_proxmox_collector_with_fake_api():
    guest_cfg = _Endpoint(None, {"config": _Endpoint({"cores": 2})})
    node = _Endpoint(
        None,
        {
            "qemu": _Endpoint([{"vmid": 101, "name": "ha", "status": "running"}], {"101": guest_cfg}),
            "lxc": _Endpoint([], {}),
        },
    )
    api = _Endpoint(
        None,
        {
            "version": _Endpoint({"version": "8.2.4"}),
            "nodes": _Endpoint([{"node": "pve1", "status": "online", "maxcpu": 12}], {"pve1": node}),
        },
    )
    cfg = ProxmoxConfig(host="pve", user="u@pve", token_name="t", token_value="v")
    hosts, services = ProxmoxCollector(cfg, api=api).collect()
    assert hosts[0].name == "pve1" and hosts[0].version == "8.2.4"
    assert services[0].name == "ha" and services[0].kind == "vm"


def test_proxmox_nodes_get_their_own_address():
    empty = _Endpoint(None, {"qemu": _Endpoint([], {}), "lxc": _Endpoint([], {})})
    api = _Endpoint(
        None,
        {
            "version": _Endpoint({"version": "9"}),
            "nodes": _Endpoint(
                [{"node": "a", "status": "online"}, {"node": "b", "status": "online"}],
                {"a": empty, "b": empty},
            ),
            "cluster": _Endpoint(
                None, {"status": _Endpoint([{"type": "node", "name": "a", "ip": "10.0.0.1"}])}
            ),
        },
    )
    cfg = ProxmoxConfig(host="10.0.0.1", user="u@pve", token_name="t", token_value="v")
    hosts, _ = ProxmoxCollector(cfg, api=api).collect()
    # b is unknown: no address rather than a wrong one (the API host belongs to a)
    assert [h.address for h in hosts] == ["10.0.0.1", None]
