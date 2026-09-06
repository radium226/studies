"""Plain NSS-based resolution (ping, getent) via nss-mdns -- distinct
from avahi's own tools, and from avahi-daemon's IPC socket that
nss-mdns's IPv4 modules turned out to use under the hood. See README,
"Why ping foo.local didn't work at first".
"""
import pytest

pytestmark = pytest.mark.integration

NSS_MDNS_IPV6_BUG = (
    "Confirmed bug in Arch's nss-mdns 0.15.1-2: mdns6_minimal sends "
    "RESOLVE-HOSTNAME-IPV4 (not IPV6) to avahi's socket regardless of "
    "which module is loaded, so it always gets the IPv4 answer back. "
    "See README. Not something fixable from this playbook -- if this "
    "starts passing, the upstream bug got fixed and this xfail (and "
    "the README section) should be removed."
)


def test_ping_v4_via_nss_across_the_mesh(ssh):
    output = ssh("client1", "ping -c1 -W3 client2.local")
    assert "0% packet loss" in output


def test_ping_v4_via_nss_across_foreign_lan(ssh):
    output = ssh("client1", "ping -c1 -W3 foreign.local")
    assert "0% packet loss" in output


def test_getent_ahosts_v4(ssh):
    output = ssh("client1", "getent ahosts client2.local")
    assert "10.0.0.3" in output


@pytest.mark.xfail(reason=NSS_MDNS_IPV6_BUG, strict=True)
def test_getent_ahostsv6_resolves_the_real_address(ssh):
    # Actually returns a v4-mapped address (::ffff:10.0.0.3) synthesized
    # from the wrong-family IPv4 answer nss-mdns's buggy mdns6_minimal
    # gives back -- not the real fd00::3.
    output = ssh("client1", "getent ahostsv6 client2.local")
    assert "fd00::3" in output


@pytest.mark.xfail(reason=NSS_MDNS_IPV6_BUG, strict=True)
def test_ping_v6_is_a_known_upstream_bug(ssh):
    ssh("client1", "ping -6 -c1 -W3 client2.local")
