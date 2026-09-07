# Sensible default mesh-network config for wireguard.py/dns.py -- lets those
# deploys run against any inventory (or none at all) without importing
# anything from inventory.py, since deploys/ is meant to eventually become a
# standalone, reusable module (like pyinfra's own "normally from a
# pyinfra_* package" deploys). @deploy's `data_defaults` makes WG_DATA_DEFAULTS
# the lowest-priority layer of host.data, so an inventory's own group/host
# data (see inventory.py's "mesh" group) always overrides it -- deploys never
# need to know whether that override happened.
import ipaddress

WG_IPV4_NETWORK = ipaddress.ip_network("10.0.0.0/24")
WG_IPV6_NETWORK = ipaddress.ip_network("fd00::/64")
WG_LISTEN_PORT = 51820


def _reverse_zone(network):
    """The classic in-addr.arpa zone name and matching BIND `db.` filename for
    an IPv4 network's own address, e.g. 10.0.0.0/24 -> ("0.0.10.in-addr.arpa",
    "db.10.0.0"). Only meaningful for a prefix length that's a multiple of 8:
    classic BIND reverse zones don't support anything finer without RFC 2317
    delegation, which this study doesn't need.
    """
    assert network.prefixlen % 8 == 0
    host_octets = (network.max_prefixlen - network.prefixlen) // 8
    zone_name = ".".join(network.network_address.reverse_pointer.split(".")[host_octets:])
    kept_octets = str(network.network_address).split(".")[: network.prefixlen // 8]
    return zone_name, "db." + ".".join(kept_octets)


WG_IPV4_REVERSE_ZONE, WG_IPV4_REVERSE_ZONE_FILE = _reverse_zone(WG_IPV4_NETWORK)

# The host.data keys wireguard()/dns() read off every mesh host (via
# host.data.wg_*) -- either from here, or from whatever overrides them.
WG_DATA_DEFAULTS = {
    "wg_ipv4_network": str(WG_IPV4_NETWORK),
    "wg_ipv6_network": str(WG_IPV6_NETWORK),
    "wg_ipv4_prefixlen": WG_IPV4_NETWORK.prefixlen,
    "wg_ipv6_prefixlen": WG_IPV6_NETWORK.prefixlen,
    "wg_listen_port": WG_LISTEN_PORT,
    "wg_ipv4_reverse_zone": WG_IPV4_REVERSE_ZONE,
    "wg_ipv4_reverse_zone_file": WG_IPV4_REVERSE_ZONE_FILE,
}
