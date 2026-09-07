# The wg0 mesh + mDNS study as one composed deploy: the WireGuard mesh
# itself (wireguard.py), the hub's BIND DNS server with RFC 2136
# self-registration (dns.py), and the unicast mDNS repeater + avahi/nss-mdns
# setup (mdns.py) -- see each module's own header for what it does and why.
# This is deploys' one public entrypoint; deploy.py just calls it.
from pyinfra.api import deploy

from .dns import setup_dns
from .mdns import setup_mdns
from .wireguard import setup_wireguard


@deploy("Setup .internal Network")
def setup_internal_network():
    setup_wireguard()
    setup_dns()
    setup_mdns()
