"""The unicast mDNS repeater daemon itself, running on the server."""
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_REPEATER_SCRIPT = Path(__file__).resolve().parent.parent / "pyinfra" / "files" / "mdns" / "mdns-unicast-repeater"


def test_repeater_service_is_active(ssh):
    assert ssh("server", "systemctl is-active mdns-unicast-repeater").strip() == "active"


def test_repeater_service_is_enabled(ssh):
    assert ssh("server", "systemctl is-enabled mdns-unicast-repeater").strip() == "enabled"


def test_repeater_env_file_matches_the_mesh_topology(ssh):
    output = ssh("server", "cat /etc/mdns-unicast-repeater.env")
    assert "MDNS_REPEATER_IFACE=wg0" in output
    assert "MDNS_REPEATER_LOCAL_V4=10.0.0.1" in output
    assert "MDNS_REPEATER_PEERS_V4=10.0.0.2,10.0.0.3" in output
    assert "MDNS_REPEATER_PEERS_V6=fd00::2,fd00::3" in output


def test_repeater_deployed_script_matches_the_repo(ssh):
    """Catches drift between what's committed and what's actually
    running -- e.g. after editing the script but forgetting to
    re-provision.
    """
    deployed = ssh("server", "cat /usr/local/bin/mdns-unicast-repeater")
    assert deployed.strip() == REPO_REPEATER_SCRIPT.read_text().strip()


def test_repeater_is_actually_forwarding_live_traffic(ssh):
    """Triggers real mDNS traffic and checks the repeater logged
    forwarding it -- proves discovery isn't just being served from a
    stale avahi cache with the repeater silently dead.

    Uses avahi-browse (an active, ongoing query) rather than
    avahi-resolve, which can be answered purely from avahi's local
    cache without putting anything on the wire at all.
    """
    ssh("client1", "timeout 3 avahi-browse -at")
    output = ssh(
        "server",
        "sudo journalctl -u mdns-unicast-repeater --since '-30 seconds' --no-pager",
    )
    assert "forwarding to" in output
