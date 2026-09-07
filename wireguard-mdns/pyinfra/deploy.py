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
# packet to multiple peers on one shared interface. Instead of routing
# multicast through WireGuard at all, mdns-unicast-repeater (deployed
# on the server only) joins the multicast group as an ordinary local
# socket and re-sends every packet it sees as plain unicast UDP to each
# spoke's own address -- which WireGuard has never had any trouble with.
#
# Ported from an earlier Ansible playbook. The one structural difference
# worth knowing before editing this file: pyinfra runs every fact during
# a "prepare" phase, strictly before ANY operation executes on ANY host
# (even operations added earlier in this same script) -- unlike Ansible,
# which executes strictly task-by-task-across-all-hosts, so a `register`
# in one task is always safe to use in the next. A pyinfra fact can only
# ever see state that already existed before this deploy run started.
# The WireGuard keypair exchange below (server needs both spokes'
# pubkeys; each spoke needs the server's) is the one place that actually
# matters here -- see facts.py's WireguardKey for how it's handled.
import re

from pyinfra import host, inventory
from pyinfra.facts.files import File
from pyinfra.operations import files, pacman, server, systemd

from facts import InterfaceWithAddress, NsswitchHostsLine, WireguardKey

pacman.packages(
    name="Install WireGuard and mDNS packages",
    packages=["wireguard-tools", "avahi", "nss-mdns", "bind"],
    update=True,
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

WG_DNS_SERVER = "10.0.0.1"

if host.name == "server":
    files.template(
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

    files.template(
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

# --- Proof-of-concept: real dynamic DNS on the hub -------------------
# Parallel to the avahi/mDNS setup, not a replacement for it: a BIND
# server on the hub, with peers registering their own name/address via
# genuine RFC 2136 dynamic updates (nsupdate) -- the actual standard
# protocol, not a bespoke one. Open/unauthenticated updates: fine for a
# lab POC, not something to do for real. See wg-dns-register and the
# README.

if host.name == "server":
    files.template(
        name="Deploy named.conf",
        src="templates/named.conf.j2",
        dest="/etc/named.conf",
        user="root",
        group="named",
        mode="640",
    )

    # force: false in the old Ansible playbook -- don't clobber a zone
    # file BIND has since rewritten with dynamic records. Whether the
    # file already exists is safe to check as an ordinary fact here:
    # nothing earlier in this deploy creates it, so there's no ordering
    # hazard like the WireGuard keypair fact has.
    if not host.get_fact(File, path="/var/named/wg.zone"):
        files.template(
            name="Deploy the wg zone skeleton (SOA/NS only -- everything else is dynamic)",
            src="templates/wg.zone.j2",
            dest="/var/named/wg.zone",
            user="named",
            group="named",
            mode="644",
        )

    if not host.get_fact(File, path="/var/named/db.10.0.0"):
        files.template(
            name="Deploy the reverse zone skeleton",
            src="templates/db.10.0.0.zone.j2",
            dest="/var/named/db.10.0.0",
            user="named",
            group="named",
            mode="644",
        )

    # named needs to bind to wg0's own address (10.0.0.1), so it must
    # not start before that interface exists -- this drop-in makes that
    # true on every boot, not just this one provisioning run.
    files.directory(
        name="Create the systemd drop-in directory",
        path="/etc/systemd/system/named.service.d",
        mode="755",
    )
    files.put(
        name="Deploy the drop-in",
        src="files/named-wait-for-wg0.conf",
        dest="/etc/systemd/system/named.service.d/override.conf",
        mode="644",
    )

    systemd.service(
        name="Start and enable named",
        service="named",
        running=True,
        restarted=True,
        enabled=True,
        daemon_reload=True,
    )

# Plain (non-templated) file: HOSTNAME, IPV4, IPV6 and DNS_SERVER are all
# derived at runtime from wg0 itself (see the file header), so the same
# script deploys unchanged to every peer.
if host.name in ("server", "client1", "client2"):
    files.put(
        name="Deploy the DNS self-registration script",
        src="files/wg-dns-register",
        dest="/usr/local/bin/wg-dns-register",
        mode="755",
    )

# --- Bring tunnels up ------------------------------------------------

if host.name in ("server", "client1", "client2"):
    systemd.service(
        name="Start and enable WireGuard",
        service="wg-quick@wg0",
        running=True,
        restarted=True,
        enabled=True,
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

# --- Unicast mDNS repeater (replaces avahi's reflector) --------------
# See the file header for why: one shared wg0 with multiple peers can't
# have WireGuard forward multicast to more than one of them, so this
# repeats each mDNS packet as unicast instead, which scales to any
# number of spokes without needing one WireGuard interface each.

if host.name == "server":
    # Plain (non-templated) file -- config lives in the environment file
    # below instead, so this script is also plain, importable Python for
    # tests/test_repeater_unit.py to exercise directly, with no Jinja
    # rendering involved.
    files.put(
        name="Deploy the unicast mDNS repeater script",
        src="files/mdns-unicast-repeater",
        dest="/usr/local/bin/mdns-unicast-repeater",
        mode="755",
    )

    files.template(
        name="Deploy the unicast mDNS repeater environment file",
        src="templates/mdns-unicast-repeater.env.j2",
        dest="/etc/mdns-unicast-repeater.env",
        mode="644",
        mdns_repeater_iface="wg0",
        mdns_repeater_local_v4="10.0.0.1",
        mdns_repeater_peers_v4=["10.0.0.2", "10.0.0.3"],
        mdns_repeater_peers_v6=["fd00::2", "fd00::3"],
    )

    files.put(
        name="Deploy the unicast mDNS repeater systemd unit",
        src="files/mdns-unicast-repeater.service",
        dest="/etc/systemd/system/mdns-unicast-repeater.service",
        mode="644",
    )

    systemd.service(
        name="Start and enable the unicast mDNS repeater",
        service="mdns-unicast-repeater",
        running=True,
        restarted=True,
        enabled=True,
        daemon_reload=True,
    )

# --- mDNS (avahi) ----------------------------------------------------
#
# client1 and foreign both also sit on "foreign_lan", a second network
# that has nothing to do with WireGuard (client1 is dual-homed, the way
# a real laptop might be on WireGuard *and* a home/office LAN).
# Vagrant/libvirt don't let us pick the guest's interface name for it in
# advance, so find whichever interface actually has the IP we assigned.
#
# client1's avahi handles two independent mDNS domains at once: wg0 (the
# WireGuard mesh) and foreign_lan (not bridged anywhere). foreign only
# has the latter. No host runs avahi's own reflector any more --
# mdns-unicast-repeater does that job on the server now instead (see the
# file header), so every host here has exactly one relevant mDNS domain
# and nothing to reflect between.
if host.name == "client1":
    foreign_iface = host.get_fact(InterfaceWithAddress, ip="192.168.60.11")
    avahi_allow_interfaces = f"wg0,{foreign_iface}"
elif host.name == "foreign":
    avahi_allow_interfaces = host.get_fact(InterfaceWithAddress, ip="192.168.60.20")
else:
    avahi_allow_interfaces = "wg0"

files.template(
    name="Configure avahi to only publish over the intended interfaces",
    src="templates/avahi-daemon.conf.j2",
    dest="/etc/avahi/avahi-daemon.conf",
    mode="644",
    avahi_allow_interfaces=avahi_allow_interfaces,
    avahi_enable_reflector=False,
)

files.put(
    name="Advertise SSH over mDNS",
    src="files/avahi-ssh.service",
    dest="/etc/avahi/services/ssh.service",
    mode="644",
)

# One fake (unbacked -- nothing is actually listening on these ports)
# service per host, of a different type each, so avahi-browse across the
# mesh shows real variety rather than 3 copies of the same thing.
FAKE_SERVICES = {
    "server": ("avahi-nas.service", "nas.service"),
    "client1": ("avahi-webapp.service", "webapp.service"),
    "client2": ("avahi-printer.service", "printer.service"),
    "foreign": ("avahi-foreign-widget.service", "widget.service"),
}
src_name, dest_name = FAKE_SERVICES[host.name]
files.put(
    name=f"Advertise a fake service over mDNS ({dest_name})",
    src=f"files/{src_name}",
    dest=f"/etc/avahi/services/{dest_name}",
    mode="644",
)

# Two independent fixes to the box's stock hosts: line:
# - The generic "mdns_minimal" module only resolves A (IPv4) records --
#   real dual-stack NSS resolution needs the family-specific
#   mdns4_minimal/mdns6_minimal modules instead (the standard nss-mdns
#   convention).
# - "resolve [!UNAVAIL=return]" stops the whole chain on ANY status
#   other than UNAVAIL -- including a plain NOTFOUND, which is exactly
#   what nss-resolve returns for .local names once resolved's own mDNS
#   is disabled. That silently prevented mdns4_minimal/mdns6_minimal
#   from ever being consulted at all. Dropping the override restores
#   glibc's sane default action set (NOTFOUND/UNAVAIL both continue,
#   only SUCCESS stops), so resolved still short-circuits for names it
#   actually knows, but falls through to nss-mdns for everything else.
current_hosts_line = host.get_fact(NsswitchHostsLine)
if "mdns4_minimal" not in current_hosts_line:
    current_hosts_line = re.sub(r"\bmdns_minimal \[NOTFOUND=return\] ", "", current_hosts_line)
    current_hosts_line = re.sub(
        r"\bdns\b",
        "mdns4_minimal [NOTFOUND=return] mdns6_minimal [NOTFOUND=return] dns",
        current_hosts_line,
    )
new_hosts_line = re.sub(r"resolve \[!UNAVAIL=return\]", "resolve", current_hosts_line)

files.line(
    name="Enable IPv4 + IPv6 mDNS hostname resolution (nss-mdns) in nsswitch.conf",
    path="/etc/nsswitch.conf",
    line="^hosts:",
    replace=new_hosts_line,
)

# systemd-resolved runs its own independent mDNS implementation alongside
# avahi's (that's what avahi's "Detected another IPv4 mDNS stack running
# on this host" startup warning meant). nsswitch's "resolve" entry sits
# before mdns4_minimal/mdns6_minimal and stops the chain on any
# non-UNAVAIL answer -- so whenever resolved's own mDNS answers (even the
# wrong address family, which its legacy gethostbyname2-style API
# doesn't request correctly, unlike its getaddrinfo-style API), our
# nss-mdns modules never get consulted at all. Disabling resolved's own
# mDNS lets "resolve" correctly step aside for .local names and
# avahi/nss-mdns becomes the sole mDNS implementation, as it should be.
files.line(
    name="Disable systemd-resolved's own mDNS (avahi is the sole mDNS implementation)",
    path="/etc/systemd/resolved.conf",
    line="^#?MulticastDNS=",
    replace="MulticastDNS=no",
)

systemd.service(
    name="Restart systemd-resolved",
    service="systemd-resolved",
    running=True,
    restarted=True,
)

systemd.service(
    name="Start and enable avahi-daemon",
    service="avahi-daemon",
    running=True,
    restarted=True,
    enabled=True,
)
