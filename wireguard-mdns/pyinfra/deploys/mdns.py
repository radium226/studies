# mDNS discovery across the mesh: mdns-unicast-repeater on the hub
# (server) re-sends multicast mDNS packets as unicast to each spoke,
# since a single shared wg0 with multiple peers can't have WireGuard
# forward multicast to more than one of them (see deploys/wireguard.py's
# file header) -- plus avahi + nss-mdns configuration on every host so
# .local names actually resolve.
import re

from pyinfra import host
from pyinfra.api import deploy
from pyinfra.operations import files, pacman, systemd
from pyinfra.operations.util import any_changed

from facts import InterfaceWithAddress, NsswitchHostsLine

# See deploys/wireguard.py's JINJA_ENV_KWARGS for why every
# files.template() call here passes this, even though none of these
# templates currently have conditionals of their own.
JINJA_ENV_KWARGS = {"trim_blocks": True, "lstrip_blocks": True}


@deploy("mDNS")
def mdns():
    pacman.packages(
        name="Install avahi and nss-mdns",
        packages=["avahi", "nss-mdns", "python"],
    )

    # --- Unicast mDNS repeater (replaces avahi's reflector) --------------
    if host.name == "server":
        # Plain (non-templated) file -- config lives in the environment file
        # below instead, so this script is also plain, importable Python for
        # tests/test_repeater_unit.py to exercise directly, with no Jinja
        # rendering involved.
        repeater_script = files.put(
            name="Deploy the unicast mDNS repeater script",
            src="files/mdns/mdns-unicast-repeater",
            dest="/usr/local/bin/mdns-unicast-repeater",
            mode="755",
        )

        repeater_env = files.template(
            name="Deploy the unicast mDNS repeater environment file",
            src="templates/mdns/mdns-unicast-repeater.env.j2",
            dest="/etc/mdns-unicast-repeater.env",
            mode="644",
            jinja_env_kwargs=JINJA_ENV_KWARGS,
            mdns_repeater_iface="wg0",
            mdns_repeater_local_v4="10.0.0.1",
            mdns_repeater_peers_v4=["10.0.0.2", "10.0.0.3"],
            mdns_repeater_peers_v6=["fd00::2", "fd00::3"],
        )

        repeater_unit = files.put(
            name="Deploy the unicast mDNS repeater systemd unit",
            src="files/mdns/mdns-unicast-repeater.service",
            dest="/etc/systemd/system/mdns-unicast-repeater.service",
            mode="644",
        )

        systemd.service(
            name="Start and enable the unicast mDNS repeater",
            service="mdns-unicast-repeater",
            running=True,
            enabled=True,
        )

        # See deploy.py's file header for why a plain
        # `restarted=repeater_script.did_change() or ...` doesn't work here.
        systemd.service(
            name="Restart the unicast mDNS repeater (script/config changed)",
            service="mdns-unicast-repeater",
            running=True,
            restarted=True,
            daemon_reload=True,
            _if=any_changed(repeater_script, repeater_env, repeater_unit),
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
    # has the latter. No host runs avahi's own reflector any more -- the
    # unicast repeater above does that job on the server now instead, so
    # every host here has exactly one relevant mDNS domain and nothing to
    # reflect between.
    if host.name == "client1":
        foreign_iface = host.get_fact(InterfaceWithAddress, ip="192.168.60.11")
        avahi_allow_interfaces = f"wg0,{foreign_iface}"
    elif host.name == "foreign":
        avahi_allow_interfaces = host.get_fact(InterfaceWithAddress, ip="192.168.60.20")
    else:
        avahi_allow_interfaces = "wg0"

    avahi_conf = files.template(
        name="Configure avahi to only publish over the intended interfaces",
        src="templates/mdns/avahi-daemon.conf.j2",
        dest="/etc/avahi/avahi-daemon.conf",
        mode="644",
        jinja_env_kwargs=JINJA_ENV_KWARGS,
        avahi_allow_interfaces=avahi_allow_interfaces,
        avahi_enable_reflector=False,
    )

    avahi_ssh_service = files.put(
        name="Advertise SSH over mDNS",
        src="files/mdns/avahi-ssh.service",
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
    avahi_fake_service = files.put(
        name=f"Advertise a fake service over mDNS ({dest_name})",
        src=f"files/mdns/{src_name}",
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
    resolved_conf = files.line(
        name="Disable systemd-resolved's own mDNS (avahi is the sole mDNS implementation)",
        path="/etc/systemd/resolved.conf",
        line="^#?MulticastDNS=",
        replace="MulticastDNS=no",
    )

    systemd.service(
        name="Ensure systemd-resolved is running",
        service="systemd-resolved",
        running=True,
    )

    # See deploy.py's file header for why a plain
    # `restarted=resolved_conf.did_change()` doesn't work here.
    systemd.service(
        name="Restart systemd-resolved (resolved.conf changed)",
        service="systemd-resolved",
        running=True,
        restarted=True,
        _if=any_changed(resolved_conf),
    )

    systemd.service(
        name="Start and enable avahi-daemon",
        service="avahi-daemon",
        running=True,
        enabled=True,
    )

    systemd.service(
        name="Restart avahi-daemon (config/services changed)",
        service="avahi-daemon",
        running=True,
        restarted=True,
        _if=any_changed(avahi_conf, avahi_ssh_service, avahi_fake_service),
    )
