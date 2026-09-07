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

from facts import WireguardKey
from .defaults import WG_DATA_DEFAULTS

# Without these, an untaken {% if %}/{% endif %} (or {% for %} with no
# items) still leaves its own tag line's newline in the rendered output --
# pyinfra's Jinja environment doesn't set them by default. See
# templates/wireguard/wg-interface.conf.j2, whose blank-line separators
# are placed as the first line *inside* each conditional/loop specifically
# so they only appear when that block actually renders.
JINJA_ENV_KWARGS = {"trim_blocks": True, "lstrip_blocks": True}


def _local_address(h):
    """h's own wg0 address in both families, each with the mesh's prefix length."""
    return f"{h.data.wg_ipv4}/{h.data.wg_ipv4_prefixlen},{h.data.wg_ipv6}/{h.data.wg_ipv6_prefixlen}"


# data_defaults makes WG_DATA_DEFAULTS the fallback for every host.data.wg_*
# lookup below, so this deploy works even against an inventory that doesn't
# override them -- see deploys/defaults.py's header.
@deploy("Setup WireGuard", data_defaults=WG_DATA_DEFAULTS)
def setup_wireguard():
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
    if "hub" in host.groups:
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

    if "hub" in host.groups:
        client1 = inventory.get_host("client1")
        client2 = inventory.get_host("client2")

        wg_conf = files.template(
            name="Write wg0.conf (server)",
            src="templates/wireguard/wg-interface.conf.j2",
            dest="/etc/wireguard/wg0.conf",
            mode="600",
            jinja_env_kwargs=JINJA_ENV_KWARGS,
            local_address=_local_address(host),
            wg_private_key=host.get_fact(WireguardKey).private,
            listen_port=host.data.wg_listen_port,
            wg_dns_server=host.data.wg_ipv4,
            wg_dns_ipv4=host.data.wg_ipv4,
            wg_dns_ipv6=host.data.wg_ipv6,
            peers=[
                {
                    "pubkey": client1.get_fact(WireguardKey).public,
                    "allowed_ips": f"{client1.data.wg_ipv4}/32,{client1.data.wg_ipv6}/128",
                },
                {
                    "pubkey": client2.get_fact(WireguardKey).public,
                    "allowed_ips": f"{client2.data.wg_ipv4}/32,{client2.data.wg_ipv6}/128",
                },
            ],
        )

    elif "spokes" in host.groups:
        server_host = inventory.get_host("server")

        wg_conf = files.template(
            name=f"Write wg0.conf ({host.name})",
            src="templates/wireguard/wg-interface.conf.j2",
            dest="/etc/wireguard/wg0.conf",
            mode="600",
            jinja_env_kwargs=JINJA_ENV_KWARGS,
            local_address=_local_address(host),
            wg_private_key=host.get_fact(WireguardKey).private,
            wg_dns_server=server_host.data.wg_ipv4,
            wg_dns_ipv4=host.data.wg_ipv4,
            wg_dns_ipv6=host.data.wg_ipv6,
            peers=[
                {
                    "pubkey": server_host.get_fact(WireguardKey).public,
                    "endpoint": f"{server_host.data.ssh_hostname}:{server_host.data.wg_listen_port}",
                    # Routes the whole mesh (both families) plus multicast back
                    # through the hub -- the mesh networks come from
                    # host.data.wg_ipv4_network/wg_ipv6_network, same as
                    # local_address above; the multicast ranges are unrelated
                    # to the mesh's own addressing, so stay literal.
                    "allowed_ips": f"{host.data.wg_ipv4_network},224.0.0.0/4,{host.data.wg_ipv6_network},ff02::fb/128",
                    "persistent_keepalive": 25,
                },
            ],
        )

    else:
        wg_conf = None

    # --- Bring the tunnel up ---------------------------------------------

    if "mesh" in host.groups:
        assert wg_conf is not None  # always set: same group check as above

        systemd.service(
            name="Start and enable WireGuard",
            service="wg-quick@wg0",
            running=True,
            enabled=True,
            restarted=wg_conf.will_change,
        )

    if "hub" in host.groups:
        # A drop-in under /etc/sysctl.d/ rather than appending to the
        # monolithic /etc/sysctl.conf -- same reasoning as named's
        # systemd drop-in above: ours lives in its own clearly-owned
        # file instead of interleaved with unrelated system defaults.
        sysctl_persist_file = "/etc/sysctl.d/99-wireguard-mdns.conf"

        # Leftover from before this used a drop-in: both lines used to
        # get appended straight to /etc/sysctl.conf.
        for stale_line in (
            "net.ipv4.ip_forward = 1",
            "net.ipv6.conf.all.forwarding = 1",
        ):
            files.line(
                name=f"Remove '{stale_line}' from /etc/sysctl.conf, if present",
                path="/etc/sysctl.conf",
                line=stale_line,
                present=False,
            )

        server.sysctl(
            name="Allow IPv4 forwarding on the server (routes client1 <-> client2)",
            key="net.ipv4.ip_forward",
            value=1,
            persist=True,
            persist_file=sysctl_persist_file,
        )
        server.sysctl(
            name="Allow IPv6 forwarding on the server (routes client1 <-> client2)",
            key="net.ipv6.conf.all.forwarding",
            value=1,
            persist=True,
            persist_file=sysctl_persist_file,
        )
