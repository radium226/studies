# Static addresses on the shared "underlay" LAN all 3 mesh VMs sit on --
# see the Vagrantfile. NOT the WireGuard overlay itself, just how pyinfra
# (and how the VMs reach each other's WireGuard UDP endpoint) gets to
# them, the same way 3 real hosts would reach each other over the
# internet. Each host's SSH key is the one libvirt generates per-VM.
#
# Paths (the SSH key below, and every files.template/files.put `src` in
# deploy.py) are relative to this directory, not the repo root -- always
# invoke pyinfra from inside pyinfra/ (`cd pyinfra && pyinfra inventory.py
# deploy.py`), which is also what makes `import facts` in deploy.py work.
#
# The wg0 mesh's own address space is NOT defined here -- deploys/ is meant
# to eventually become a standalone module, so it must not be imported the
# other way round (inventory.py -> deploys is fine; deploys -> inventory.py
# is not). deploys/defaults.py owns WG_IPV4_NETWORK/WG_IPV6_NETWORK (used
# below to place each host's own address) and every wireguard()/dns() deploy
# reads its mesh-wide config off host.data.wg_* -- see group_data/mesh.py for
# where that data actually comes from.
from deploys import WG_IPV4_NETWORK, WG_IPV6_NETWORK

# ssh_user/ssh_known_hosts_file/ssh_strict_host_key_checking/_sudo: shared by
# every host, so they live in group_data/all.py instead of here.


def _node(name, ip, **data):
    return (
        name,
        {
            "ssh_hostname": ip,
            "ssh_key": f"../.vagrant/machines/{name}/libvirt/private_key",
            **data,
        },
    )


def _wg_host(index):
    """wg_ipv4/wg_ipv6: this host's own address on the wg0 mesh, the index'th
    usable address of WG_IPV4_NETWORK/WG_IPV6_NETWORK above in each family
    (deploys/wireguard.py appends the network's own prefix length to build
    local_address). Index 0 would be the network address itself, so callers
    start at 1, matching the .1/.2/.3 (and ::1/::2/::3) hosts already in use.
    """
    return {
        "wg_ipv4": str(WG_IPV4_NETWORK[index]),
        "wg_ipv6": str(WG_IPV6_NETWORK[index]),
    }


# foreign_lan_ip: only set on client1, which is dual-homed onto the
# foreign_lan network in addition to the mesh -- deploys/mdns.py uses its
# presence to decide whether a host needs a second avahi interface.
#
# avahi_fake_service_src/_dest: deploys/mdns.py advertises one fake (unbacked
# -- nothing is actually listening) service per host, of a different type
# each, so avahi-browse across the mesh shows real variety rather than
# copies of the same thing. _src is the file under files/mdns/ to deploy,
# _dest the service name avahi publishes it under.
hub = [
    _node(
        "server",
        "192.168.56.10",
        **_wg_host(1),
        avahi_fake_service_src="avahi-nas.service",
        avahi_fake_service_dest="nas.service",
    )
]

spokes = [
    _node(
        "client1",
        "192.168.56.11",
        **_wg_host(2),
        foreign_lan_ip="192.168.60.11",
        avahi_fake_service_src="avahi-webapp.service",
        avahi_fake_service_dest="webapp.service",
    ),
    _node(
        "client2",
        "192.168.56.12",
        **_wg_host(3),
        avahi_fake_service_src="avahi-printer.service",
        avahi_fake_service_dest="printer.service",
    ),
]

# Every host actually on the WireGuard mesh -- deploys/*.py checks this
# group (`"mesh" in host.groups`) wherever hub and spokes share the same
# behaviour, instead of repeating `host.name in ("server", "client1",
# "client2")`.
mesh = hub + spokes

# Not part of the WireGuard mesh at all -- represents some other node that
# happens to share client1's non-WireGuard "foreign_lan" network.
foreign_lan = [
    _node(
        "foreign",
        "192.168.60.20",
        avahi_fake_service_src="avahi-foreign-widget.service",
        avahi_fake_service_dest="widget.service",
    )
]
