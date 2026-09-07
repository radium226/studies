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
# The same "prepare vs execute" split also rules out the obvious
# `restarted=some_op.did_change()` for "only restart if config changed":
# that expression is a plain Python call evaluated the instant a deploy
# defines the operation, long before some_op has actually run against
# the target -- pyinfra raises "Cannot evaluate operation result before
# execution" rather than silently getting it wrong. The fix used
# throughout deploys/ is `_if=any_changed(some_op, ...)` (or
# `all_changed`) on a *second*, restart-only `systemd.service()` call:
# `_if` takes a callback pyinfra runs later, once execution actually
# reaches it, and skips the whole operation (cleanly reported as no
# change) if it returns False. Keep the "ensure running/enabled" and
# "restart on change" concerns in two separate operations, not one --
# gating a single combined operation behind `_if` would also skip the
# idempotent running/enabled check on a no-change run.
from deploys import dns, mdns, wireguard

wireguard()
dns()
mdns()
