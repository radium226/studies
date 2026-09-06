"""client1 is dual-homed: WireGuard mesh (wg0) plus a second, unrelated
network (foreign_lan) shared with the standalone `foreign` VM. Service
discovery isolation is covered in test_integration_mdns_discovery.py --
this file covers the underlying interface/address/config setup.
"""
import re

import pytest

pytestmark = pytest.mark.integration


def test_client1_has_a_foreign_lan_address(ssh):
    output = ssh("client1", "ip -4 -o addr show")
    assert "192.168.60.11/24" in output


def test_foreign_has_a_foreign_lan_address(ssh):
    output = ssh("foreign", "ip -4 -o addr show")
    assert "192.168.60.20/24" in output


def test_client1_has_no_extra_nics_beyond_the_three_expected(ssh):
    # lo, eth0 (underlay NAT), eth1 (wireguard_mdns_study underlay),
    # eth2-or-similar (foreign_lan), wg0.
    output = ssh("client1", "ip -4 -o addr show")
    interfaces = {line.split()[1] for line in output.strip().splitlines()}
    assert len(interfaces) == 5, interfaces


def test_avahi_resolve_foreign_from_client1(ssh):
    output = ssh("client1", "avahi-resolve -4 -n foreign.local")
    name, _, addr = output.strip().partition("\t")
    assert name == "foreign.local"
    assert addr == "192.168.60.20"


def test_ping_foreign_from_client1(ssh):
    output = ssh("client1", "ping -c2 -W3 foreign.local")
    assert "0% packet loss" in output


def test_client1_avahi_allows_both_wg0_and_its_foreign_lan_iface(ssh):
    output = ssh("client1", "cat /etc/avahi/avahi-daemon.conf")
    match = re.search(r"^allow-interfaces=(.+)$", output, re.MULTILINE)
    assert match, output
    interfaces = match.group(1).split(",")
    assert "wg0" in interfaces
    assert len(interfaces) == 2


def test_foreign_avahi_only_allows_its_own_iface(ssh):
    output = ssh("foreign", "cat /etc/avahi/avahi-daemon.conf")
    match = re.search(r"^allow-interfaces=(.+)$", output, re.MULTILINE)
    assert match, output
    assert "," not in match.group(1)
    assert match.group(1) != "wg0"
