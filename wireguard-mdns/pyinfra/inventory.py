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

COMMON_DATA = {
    "ssh_user": "vagrant",
    "ssh_known_hosts_file": "/dev/null",
    "ssh_strict_host_key_checking": "no",
    "_sudo": True,
}


def _node(name, ip):
    return (
        name,
        {
            **COMMON_DATA,
            "ssh_hostname": ip,
            "ssh_key": f"../.vagrant/machines/{name}/libvirt/private_key",
        },
    )


hub = [_node("server", "192.168.56.10")]

spokes = [
    _node("client1", "192.168.56.11"),
    _node("client2", "192.168.56.12"),
]

# Every host actually on the WireGuard mesh -- deploys/*.py checks this
# group (`"mesh" in host.groups`) wherever hub and spokes share the same
# behaviour, instead of repeating `host.name in ("server", "client1",
# "client2")`.
mesh = hub + spokes

# Not part of the WireGuard mesh at all -- represents some other node that
# happens to share client1's non-WireGuard "foreign_lan" network.
foreign_lan = [_node("foreign", "192.168.60.20")]
