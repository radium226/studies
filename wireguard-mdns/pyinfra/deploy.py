# WireGuard + mDNS study: server (hub) and client1/client2 (spokes) share
# one WireGuard mesh (deploys/wireguard.py); the hub also runs a real
# BIND DNS server with RFC 2136 self-registration (deploys/dns.py) and a
# unicast mDNS repeater + avahi/nss-mdns configuration on every host
# (deploys/mdns.py). `foreign` sits outside the mesh entirely, on its own
# LAN with client1, to prove mDNS still works there independent of
# WireGuard.
#
# Ported from an earlier Ansible playbook. The one structural difference
# worth knowing before editing any deploys/*.py file: pyinfra runs every
# fact during a "prepare" phase, strictly before ANY operation executes
# on ANY host (even operations added earlier in this same deploy, or in
# an earlier deploy this script calls) -- unlike Ansible, which executes
# strictly task-by-task-across-all-hosts, so a `register` in one task is
# always safe to use in the next. A pyinfra fact can only ever see state
# that already existed before this deploy run started. The WireGuard
# keypair exchange in deploys/wireguard.py (server needs both spokes'
# pubkeys; each spoke needs the server's) is the one place that actually
# matters here -- see facts.py's WireguardKey for how it's handled.
#
# "only restart if config changed" -- every systemd.service() below uses
# `restarted=some_op.will_change` (`some_op` being an earlier
# files.template()/files.put() return value). `.will_change` is safe to
# read immediately: pyinfra already diffs every idempotent operation
# against current remote state during the "prepare" phase above (that's
# what powers the "Detected changes" table before anything actually
# runs), and the property just returns that already-computed result.
# Its sibling `.did_change()` looks similar but does the opposite thing
# and is *not* safe here: it reports what actually happened, so it
# raises "Cannot evaluate operation result before execution" unless read
# after that operation has actually run -- which, this early, it hasn't.
# When more than one prior operation should trigger a restart, `or` them
# together (`a.will_change or b.will_change`): both sides are plain
# already-computed bools by this point, not deferred callbacks, so
# there's nothing special about combining them.
from deploys import setup_internal_network

setup_internal_network()
