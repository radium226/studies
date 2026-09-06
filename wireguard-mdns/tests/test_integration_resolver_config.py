"""Regression checks for the two nsswitch/resolved fixes this study
needed (see README, "Why ping foo.local didn't work at first"):
resolved's own competing mDNS implementation disabled, and nsswitch's
`resolve` entry no longer blocking fallthrough to nss-mdns.
"""
import pytest

pytestmark = pytest.mark.integration

HOSTS = ["server", "client1", "client2", "foreign"]


@pytest.mark.parametrize("host", HOSTS)
def test_nsswitch_has_family_specific_mdns_modules(ssh, host):
    output = ssh(host, "grep '^hosts:' /etc/nsswitch.conf")
    assert "mdns4_minimal [NOTFOUND=return]" in output
    assert "mdns6_minimal [NOTFOUND=return]" in output


@pytest.mark.parametrize("host", HOSTS)
def test_nsswitch_resolve_entry_does_not_block_fallthrough(ssh, host):
    output = ssh(host, "grep '^hosts:' /etc/nsswitch.conf")
    assert "[!UNAVAIL=return]" not in output


@pytest.mark.parametrize("host", HOSTS)
def test_systemd_resolved_mdns_disabled(ssh, host):
    output = ssh(host, "grep '^MulticastDNS=' /etc/systemd/resolved.conf")
    assert output.strip() == "MulticastDNS=no"
