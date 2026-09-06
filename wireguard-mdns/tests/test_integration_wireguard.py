"""WireGuard tunnel state: the hub's single shared wg0, its two peers,
and each spoke's own single peer.
"""
import time

import pytest

pytestmark = pytest.mark.integration

HANDSHAKE_FRESHNESS_SECONDS = 300


def test_server_wg0_has_exactly_two_peers(ssh):
    peers = ssh("server", "sudo wg show wg0 peers").strip().splitlines()
    assert len(peers) == 2


def test_server_peer_allowed_ips_match_the_spokes(ssh):
    output = ssh("server", "sudo wg show wg0 allowed-ips")
    allowed_ips = {line.split("\t", 1)[1] for line in output.strip().splitlines()}
    assert allowed_ips == {"10.0.0.2/32 fd00::2/128", "10.0.0.3/32 fd00::3/128"}


def test_server_listens_on_the_expected_port(ssh):
    output = ssh("server", "sudo wg show wg0 listen-port")
    assert output.strip() == "51820"


@pytest.mark.parametrize("host", ["client1", "client2"])
def test_spoke_has_exactly_one_peer(ssh, host):
    peers = ssh(host, "sudo wg show wg0 peers").strip().splitlines()
    assert len(peers) == 1


@pytest.mark.parametrize("host,expected_ip", [("client1", "10.0.0.2"), ("client2", "10.0.0.3")])
def test_spoke_has_the_expected_address(ssh, host, expected_ip):
    output = ssh(host, "ip -4 -o addr show wg0")
    assert f"inet {expected_ip}/24" in output


@pytest.mark.parametrize("host", ["server", "client1", "client2"])
def test_handshake_is_fresh(ssh, host):
    output = ssh(host, "sudo wg show wg0 latest-handshakes")
    timestamps = [int(line.split("\t")[1]) for line in output.strip().splitlines()]
    assert timestamps, "no peers configured on wg0"
    assert all(t > 0 for t in timestamps), "no handshake has ever completed"
    now = int(time.time())
    assert all(now - t < HANDSHAKE_FRESHNESS_SECONDS for t in timestamps), (
        f"stale handshake(s): {timestamps} vs now={now}"
    )


@pytest.mark.parametrize("host", ["server", "client1", "client2"])
def test_wg0_has_the_multicast_link_flag(ssh, host):
    # WireGuard interfaces don't get this by default -- avahi silently
    # ignores an interface without it. See README.
    output = ssh(host, "ip -o link show wg0")
    assert "MULTICAST" in output


def test_ipv4_forwarding_enabled_on_the_server(ssh):
    output = ssh("server", "sysctl -n net.ipv4.ip_forward")
    assert output.strip() == "1"
