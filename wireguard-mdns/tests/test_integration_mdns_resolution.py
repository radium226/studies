"""mDNS hostname resolution (avahi-resolve, ping) across the WireGuard
mesh, in both address families.
"""
import pytest

pytestmark = pytest.mark.integration

MESH_HOSTS = ["server", "client1", "client2"]
MESH_IPS_V4 = {"server": "10.0.0.1", "client1": "10.0.0.2", "client2": "10.0.0.3"}
MESH_IPS_V6 = {"server": "fd00::1", "client1": "fd00::2", "client2": "fd00::3"}


def _resolve_pairs():
    return [(f, t) for f in MESH_HOSTS for t in MESH_HOSTS if f != t]


@pytest.mark.parametrize("from_host,to_host", _resolve_pairs())
def test_avahi_resolve_v4_across_the_mesh(ssh, from_host, to_host):
    output = ssh(from_host, f"avahi-resolve -4 -n {to_host}.local")
    name, _, addr = output.strip().partition("\t")
    assert name == f"{to_host}.local"
    assert addr == MESH_IPS_V4[to_host]


@pytest.mark.parametrize("from_host,to_host", _resolve_pairs())
def test_avahi_resolve_v6_across_the_mesh(ssh, from_host, to_host):
    output = ssh(from_host, f"avahi-resolve -6 -n {to_host}.local")
    name, _, addr = output.strip().partition("\t")
    assert name == f"{to_host}.local"
    assert addr == MESH_IPS_V6[to_host]


@pytest.mark.parametrize("from_host,to_host", _resolve_pairs())
def test_ping_across_the_mesh(ssh, from_host, to_host):
    output = ssh(from_host, f"ping -c2 -W3 {to_host}.local")
    assert "0% packet loss" in output


def test_full_round_trip_tcp_connect_to_a_discovered_service(ssh):
    """discover (avahi-browse elsewhere) -> resolve -> real TCP connect,
    relayed through the hub's unicast repeater -- not just a cached
    answer with nothing behind it.

    Uses bash's `read -t` (not the external `timeout` command wrapping
    `cat`) specifically so the command exits 0 on success: `cat` never
    sees EOF on an open SSH connection, so `timeout cat ...` always gets
    killed and exits 124 even after successfully printing the banner.
    """
    banner = ssh(
        "client1",
        "bash -c 'exec 3<>/dev/tcp/client2.local/22; read -r -t3 line <&3; echo \"$line\"'",
    )
    assert banner.strip().startswith("SSH-2.0-OpenSSH")
