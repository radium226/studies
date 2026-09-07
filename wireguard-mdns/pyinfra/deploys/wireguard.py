# WireGuard topology:
#
#            wg0 (10.0.0.1/24)
#   server  -------------------  client1 (10.0.0.2/24, wg0)
#     |
#     +----------------------    client2 (10.0.0.3/24, wg0)
#
# ONE shared wg0 on the server with both spokes as peers -- this scales to
# any number of spokes, unlike an earlier version of this study that gave
# the server one dedicated WireGuard interface per spoke (see git history
# / README for why, and why that doesn't scale). The tradeoff: WireGuard's
# cryptokey routing can only ever deliver a given destination to a single
# peer per interface, so it genuinely cannot forward a multicast mDNS
# packet to multiple peers on one shared interface -- see deploys/mdns.py
# for how that's worked around instead.
from pyinfra import host, inventory
from pyinfra.api import deploy
from pyinfra.operations import files, pacman, server, systemd
from pyinfra.operations.util import any_changed

from facts import WireguardKey

WG_DNS_SERVER = "10.0.0.1"


@deploy("WireGuard")
def wireguard():
    # wireguard-tools is also installed inline by the WireguardKey fact
    # itself (see facts.py) -- pyinfra runs facts before any operation on
    # any host, so that's what actually guarantees it's present by the
    # time this operation's idempotency check runs. This is just an
    # explicit, visible install alongside it.
    pacman.packages(
        name="Install wireguard-tools",
        packages=["wireguard-tools"],
    )

    files.directory(
        name="Ensure /etc/wireguard exists",
        path="/etc/wireguard",
        mode="700",
    )

    # Leftover from an earlier version of this study, where the server had
    # one dedicated WireGuard interface per spoke instead of one shared wg0.
    # Both used ListenPort 51820, so if they're still running they'll keep
    # that UDP port bound and the new wg0 fails to start.
    if host.name == "server":
        for iface in ("wg-c1", "wg-c2"):
            systemd.service(
                name=f"Stop and disable {iface}, if present",
                service=iface,
                running=False,
                enabled=False,
                _ignore_errors=True,
            )

        for leftover in ("wg-c1.conf", "wg-c2.conf", "wg-c1.key", "wg-c2.key"):
            files.file(
                name=f"Remove {leftover}, if present",
                path=f"/etc/wireguard/{leftover}",
                present=False,
            )

    # --- Interface config templating -----------------------------------
    #
    # No more Table=off/manual-route juggling: since the server now has only
    # one WireGuard interface, there's no cross-interface multicast route
    # collision to work around any more, and none of the peers here need
    # multicast in their AllowedIPs at all -- see the file header.

    if host.name == "server":
        wg_conf = files.template(
            name="Write wg0.conf (server)",
            src="templates/wg-interface.conf.j2",
            dest="/etc/wireguard/wg0.conf",
            mode="600",
            local_address="10.0.0.1/24,fd00::1/64",
            wg_private_key=host.get_fact(WireguardKey).private,
            listen_port=51820,
            wg_dns_server=WG_DNS_SERVER,
            wg_dns_ipv4="10.0.0.1",
            wg_dns_ipv6="fd00::1",
            peers=[
                {
                    "pubkey": inventory.get_host("client1").get_fact(WireguardKey).public,
                    "allowed_ips": "10.0.0.2/32,fd00::2/128",
                },
                {
                    "pubkey": inventory.get_host("client2").get_fact(WireguardKey).public,
                    "allowed_ips": "10.0.0.3/32,fd00::3/128",
                },
            ],
        )

    elif host.name in ("client1", "client2"):
        spoke_address = {"client1": "10.0.0.2/24,fd00::2/64", "client2": "10.0.0.3/24,fd00::3/64"}[host.name]
        spoke_dns_ipv4 = {"client1": "10.0.0.2", "client2": "10.0.0.3"}[host.name]
        spoke_dns_ipv6 = {"client1": "fd00::2", "client2": "fd00::3"}[host.name]
        server_host = inventory.get_host("server")

        wg_conf = files.template(
            name=f"Write wg0.conf ({host.name})",
            src="templates/wg-interface.conf.j2",
            dest="/etc/wireguard/wg0.conf",
            mode="600",
            local_address=spoke_address,
            wg_private_key=host.get_fact(WireguardKey).private,
            wg_dns_server=WG_DNS_SERVER,
            wg_dns_ipv4=spoke_dns_ipv4,
            wg_dns_ipv6=spoke_dns_ipv6,
            peers=[
                {
                    "pubkey": server_host.get_fact(WireguardKey).public,
                    "endpoint": f"{server_host.data.ssh_hostname}:51820",
                    "allowed_ips": "10.0.0.0/24,224.0.0.0/4,fd00::/64,ff02::fb/128",
                    "persistent_keepalive": 25,
                },
            ],
        )

    else:
        wg_conf = None

    # --- Bring the tunnel up ---------------------------------------------

    if host.name in ("server", "client1", "client2"):
        assert wg_conf is not None  # always set: same host.name check as above

        systemd.service(
            name="Start and enable WireGuard",
            service="wg-quick@wg0",
            running=True,
            enabled=True,
        )

        # See deploy.py's file header for why a plain
        # `restarted=wg_conf.did_change()` doesn't work here.
        systemd.service(
            name="Restart WireGuard (config changed)",
            service="wg-quick@wg0",
            running=True,
            restarted=True,
            _if=any_changed(wg_conf),
        )

    if host.name == "server":
        server.sysctl(
            name="Allow IPv4 forwarding on the server (routes client1 <-> client2)",
            key="net.ipv4.ip_forward",
            value=1,
            persist=True,
        )
        server.sysctl(
            name="Allow IPv6 forwarding on the server (routes client1 <-> client2)",
            key="net.ipv6.conf.all.forwarding",
            value=1,
            persist=True,
        )
