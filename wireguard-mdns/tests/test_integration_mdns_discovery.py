"""Service discovery (avahi-browse) and the two-domain isolation this
study is built around: the WireGuard mesh (wg0, all 3 mesh hosts) vs.
foreign_lan (client1 + foreign, unrelated to WireGuard). Nothing should
ever bridge the two.
"""
import pytest

pytestmark = pytest.mark.integration

# (name, type) -- deliberately no interface here: each host names its own
# NICs independently, so "eth2" from client1's perspective is "eth1" from
# foreign's. Mesh entries are always tagged "wg0" everywhere, which the
# tests below check separately where it matters.
MESH_SERVICES = {
    ("server SSH", "SSH Remote Terminal"),
    ("server Fake NAS", "Microsoft Windows Network"),
    ("client1 SSH", "SSH Remote Terminal"),
    ("client1 Fake Web App", "Web Site"),
    ("client2 SSH", "SSH Remote Terminal"),
    ("client2 Fake Printer", "Internet Printer"),
}

FOREIGN_LAN_SERVICES = {
    ("client1 SSH", "SSH Remote Terminal"),
    ("client1 Fake Web App", "Web Site"),
    ("foreign SSH", "SSH Remote Terminal"),
    ("foreign Foreign Widget", "Web Site"),
}


def _name_types(services: set[tuple[str, str, str]]) -> set[tuple[str, str]]:
    return {(name, service_type) for (_iface, name, service_type) in services}


@pytest.mark.parametrize("host", ["server", "client1", "client2"])
def test_mesh_services_all_discoverable(ssh, browse, host):
    assert MESH_SERVICES <= _name_types(browse(host))


@pytest.mark.parametrize("host", ["server", "client1", "client2"])
def test_mesh_services_all_tagged_as_wg0(ssh, browse, host):
    wg0_entries = {(name, t) for (iface, name, t) in browse(host) if iface == "wg0"}
    assert MESH_SERVICES <= wg0_entries


def test_server_sees_exactly_the_mesh_no_more(ssh, browse):
    assert _name_types(browse("server")) == MESH_SERVICES


def test_client2_sees_exactly_the_mesh_no_more(ssh, browse):
    assert _name_types(browse("client2")) == MESH_SERVICES


def test_client1_sees_mesh_plus_foreign_lan(ssh, browse):
    services = _name_types(browse("client1"))
    assert MESH_SERVICES <= services
    assert FOREIGN_LAN_SERVICES <= services
    assert services == MESH_SERVICES | FOREIGN_LAN_SERVICES


def test_foreign_only_sees_its_own_lan(ssh, browse):
    assert _name_types(browse("foreign")) == FOREIGN_LAN_SERVICES


@pytest.mark.parametrize("host", ["server", "client2"])
def test_foreign_lan_invisible_from_mesh_only_hosts(ssh, browse, host):
    names = {name for (name, _t) in _name_types(browse(host))}
    assert not any("foreign" in name for name in names)


def test_reflector_disabled_everywhere(ssh):
    """avahi's own reflector is superseded by mdns-unicast-repeater.py on
    the server -- nothing should have it turned on any more, mesh or
    foreign_lan side.
    """
    for host in ["server", "client1", "client2", "foreign"]:
        output = ssh(host, "cat /etc/avahi/avahi-daemon.conf")
        assert "enable-reflector=no" in output
