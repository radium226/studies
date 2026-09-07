# Data for the "mesh" group (see ../inventory.py: every host actually on the
# wg0 WireGuard mesh). pyinfra auto-loads this file by group name -- see
# https://docs.pyinfra.com/en/3.x/inventory-data.html#project-layout.
#
# This is the site's actual wg0 network plan, kept separate from
# deploys/defaults.py's fallback values: deploys/wireguard.py and
# deploys/dns.py read every key below off host.data.wg_*, falling back to
# defaults.py only if a key were missing here. Re-using WG_DATA_DEFAULTS
# (rather than retyping the same numbers) means this study, which wants
# exactly the defaults, can't drift from them by accident -- point at a
# different ipaddress.ip_network(...)/port to actually change the plan.
from deploys import WG_DATA_DEFAULTS

wg_ipv4_network = WG_DATA_DEFAULTS["wg_ipv4_network"]
wg_ipv6_network = WG_DATA_DEFAULTS["wg_ipv6_network"]
wg_ipv4_prefixlen = WG_DATA_DEFAULTS["wg_ipv4_prefixlen"]
wg_ipv6_prefixlen = WG_DATA_DEFAULTS["wg_ipv6_prefixlen"]
wg_listen_port = WG_DATA_DEFAULTS["wg_listen_port"]
wg_ipv4_reverse_zone = WG_DATA_DEFAULTS["wg_ipv4_reverse_zone"]
wg_ipv4_reverse_zone_file = WG_DATA_DEFAULTS["wg_ipv4_reverse_zone_file"]
