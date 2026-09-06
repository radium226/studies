# Studies / WireGuard + mDNS

## Goal

3 Arch Linux VMs on a shared "underlay" network, with a WireGuard overlay
on top (server = hub, client1/client2 = spokes), and mDNS (avahi) working
*across* that overlay -- both hostname resolution (`*.local`) and full
service discovery (`avahi-browse`).

A 4th VM, `foreign`, is not part of the WireGuard mesh at all. It sits on
its own separate network, `foreign_lan`, which `client1` also has a
second NIC on -- simulating a real dual-homed host (e.g. a laptop that's
on WireGuard but also plugged into a home/office LAN). This checks that
mDNS keeps working normally on that other network too, and that the two
domains (WireGuard mesh vs. foreign_lan) stay fully separate: avahi's
reflector is never enabled for `foreign_lan`, so nothing on it is
reachable from `server`/`client2`, and vice versa.

## Why a hub interface per spoke?

WireGuard forwards a packet to exactly one peer per interface, chosen by
longest-prefix match on the destination against each peer's `AllowedIPs`
-- including for a multicast destination like mDNS's `224.0.0.251`. Two
peers can't both claim the same destination on one interface, so a single
shared `wg0` on the server could reflect mDNS to at most one spoke.

Instead, `server` runs two dedicated interfaces, `wg-c1` (to client1) and
`wg-c2` (to client2), each a genuine point-to-point link. `avahi-daemon`'s
reflector then repeats mDNS traffic between `wg-c1` and `wg-c2` the same
way it would between any two ordinary NICs.

```
              wg-c1 (10.0.1.0/24)
   server ------------------------ client1 (wg0)
     |
     |    wg-c2 (10.0.2.0/24)
     +--------------------------- client2 (wg0)
```

`AllowedIPs` on every peer includes `224.0.0.0/4` in addition to the
tunnel's unicast /32 or /16, so multicast mDNS packets get routed too.
`net.ipv4.ip_forward=1` on the server also lets unicast traffic route
client1 <-> client2 (e.g. an actual SSH connection to a host discovered
via `avahi-browse`).

Each host's `avahi-daemon.conf` is restricted to its WireGuard
interface(s) only (`allow-interfaces=...`), so mDNS is confined to the
tunnel even though the VMs also share a plain underlay network -- proving
the resolution genuinely happens over WireGuard, not the underlay LAN.

Two other WireGuard quirks needed working around (see
`templates/wg-interface.conf.j2`):

- WireGuard interfaces don't carry the `MULTICAST` link flag by default,
  so avahi silently ignores them -- `PostUp = ip link set dev %i
  multicast on` turns it on.
- `wg-quick` auto-adds a kernel route for every `AllowedIPs` entry, and
  `224.0.0.0/4` is shared by both of the server's interfaces, which
  collides on whichever comes up second. Routing is disabled
  (`Table = off`) on the server's interfaces and only the one unicast
  `/32` route each actually needs is added by hand in `PostUp`.

## IPv6

Every WireGuard interface also carries a ULA address (`fd00:1::/64` for
the client1 tunnel, `fd00:2::/64` for client2's), and every peer's
`AllowedIPs` includes `ff02::fb/128` (mDNS's IPv6 link-local multicast
group) alongside `224.0.0.0/4`. avahi has both `use-ipv4`/`use-ipv6` set
to `yes` everywhere.

Interestingly, the server's two WireGuard interfaces joining the *same*
`ff02::fb` group does **not** hit the routing collision the "why a hub
interface per spoke" section above describes for IPv4: `ip -6 maddr show`
lists `ff02::fb` as a member on both `wg-c1` and `wg-c2` independently,
with no "File exists" error. IPv6 link-local multicast is scoped by
interface index at the socket level rather than resolved through the
single shared main routing table the way IPv4's class-D multicast is, so
there's no "one owning device" constraint to collide on. The unicast
`/128` route still needs the same manual `PostUp` treatment as IPv4,
since that's an ordinary (non-link-local) route and hits the same
per-interface `Table = off` question.

`avahi-resolve -6`/`avahi-browse` work correctly end-to-end over IPv6,
across both the mesh and the `foreign_lan` domain -- see the "Why `ping
foo.local` didn't work at first" section below for why `ping -6`/`getent
ahostsv6` still don't.

## Usage

```bash
vagrant up
```

This boots `server`, `client1`, `client2` (Arch Linux, libvirt provider),
then runs `ansible/playbook.yml` against all three at once (needed since
the playbook wires each host's WireGuard public key into the others'
peer config).

If `vagrant`/`virsh` report a permissions error, make sure your user is
in the `libvirt` group and `libvirtd.service` is running, then log out
and back in (or run the command via `sudo -g libvirt -u "$USER" ...`) so
the new group membership takes effect.

## Verification

From `client1`, resolve and reach `client2` purely over the WireGuard
overlay:

```bash
vagrant ssh client1
ping -c2 client2.local          # resolves to 10.0.2.2 via mDNS over wg0
avahi-resolve -n client2.local
avahi-resolve -6 -n client2.local   # resolves to fd00:2::2
avahi-browse -rt _ssh._tcp      # should list server and client2
ssh vagrant@client2.local       # full round trip: discover, resolve, connect
```

Each host also advertises one fake (unbacked -- nothing is actually
listening) service of a different type, to check discovery of more than
just SSH: a fake NAS (`_smb._tcp`) on `server`, a fake web app
(`_http._tcp`) on `client1`, a fake printer (`_ipp._tcp`) on `client2`,
and a fake widget (`_http._tcp`) on `foreign`. `avahi-browse -at` from
`server` or `client2` should list exactly 6 services (3x SSH + 3 fakes,
all tagged `wg0`/`wg-c1`/`wg-c2`) -- never anything from `foreign`.

`ip addr show wg0` / `wg show` on any node shows the tunnel and handshake
state. On `server`, `wg show` lists both `wg-c1` and `wg-c2` with a
recent handshake once the spokes are up.

### Checking the foreign_lan / dual-homed side

```bash
vagrant ssh client1
avahi-browse -at              # shows BOTH domains, one row per interface:
                               #   wg0   ... (mesh: server, client1, client2)
                               #   eth2  ... (foreign_lan: client1, foreign)
avahi-resolve -4 -n foreign.local   # resolves to foreign's foreign_lan IP
```

`server`/`client2` (no `foreign_lan` NIC at all) should show nothing from
`foreign` in their own `avahi-browse -at` -- that's the domain separation
working as intended.

### Why `ping foo.local` didn't work at first (and how it's fixed)

Two independent bugs conspired to make plain `ping`/`getent` unreliable
for `.local` names, both fixed in the playbook now:

1. **systemd-resolved runs its own, separate mDNS implementation**,
   alongside avahi's -- that's what avahi's startup warning ("Detected
   another IPv4 mDNS stack running on this host") is about. `resolve` in
   `/etc/nsswitch.conf`'s `hosts:` line is consulted *before*
   `mdns4_minimal`/`mdns6_minimal`, so resolved's own (buggier, as it
   turns out) mDNS answered first and `nss-mdns` was never actually
   reached at all. Fixed by setting `MulticastDNS=no` in
   `/etc/systemd/resolved.conf`.
2. Even with resolved's own mDNS off, its NSS module still answers
   "not found" for `.local` names -- and the box's stock
   `resolve [!UNAVAIL=return]` stops the whole chain on *any* non-UNAVAIL
   status, including a plain NOTFOUND. So `nss-mdns` still never got a
   turn. Fixed by dropping the `[!UNAVAIL=return]` override, restoring
   glibc's sane default (`NOTFOUND`/`UNAVAIL` both continue to the next
   module, only `SUCCESS` stops the chain).

With both fixed, `nss-mdns`'s IPv4 modules genuinely work correctly, and
turned out to have nothing to do with kernel routing at all: `strace`
shows `mdns4_minimal` doesn't send its own multicast packets -- it just
asks the local avahi-daemon over its Unix socket (`RESOLVE-HOSTNAME-IPV4
client2.local` -> `+ 10 0 client2.local 10.0.2.2`), the same way
`avahi-resolve` does. That's also why it now correctly resolves
`foreign.local` from the dual-homed `client1` just as well as
`client2.local` -- there was never a single-route conflict between the
two domains for IPv4 the way there'd been for `wg-quick`'s own routes.

**IPv6 is a different story, and stays broken**: `ping -6`/`getent
ahostsv6` still don't return the real address. `strace` shows exactly
why -- the `mdns6_minimal` module sends `RESOLVE-HOSTNAME-IPV4` (not
`RESOLVE-HOSTNAME-IPV6`) to avahi's socket regardless of which module
you loaded, so it always gets back the IPv4 answer, which `ping -6`
correctly rejects as the wrong address family (`getent ahostsv6`
"succeeds" only by synthesizing a v4-mapped address, `::ffff:10.0.2.2`,
not the real `fd00:2::2`). This is a genuine bug in Arch's `nss-mdns
0.15.1-2` package, not a misconfiguration -- there's nothing to fix on
our side for it. `avahi-resolve -6`/`avahi-browse` remain fully correct
and are the reliable way to verify IPv6 mDNS.

## Notes

- Boxes have no firewall by default, so no explicit forward/accept rules
  were added beyond `net.ipv4.ip_forward`. If you introduce `nftables`
  later, you'll need an explicit forward rule between `wg-c1` and
  `wg-c2`.
- Re-running `vagrant provision` re-applies the whole playbook and always
  restarts WireGuard/avahi -- fine for this kind of throwaway study, not
  written for idempotent no-op re-runs.
- First boot runs a full `pacman -Syu` (the box image is stale enough
  that skipping it hits partial-upgrade file conflicts) and then reboots
  each VM (via the `vagrant-reload` plugin) before starting WireGuard,
  since the upgrade pulls in a new kernel.
- If your host runs Docker, its default `FORWARD` policy is `DROP` and
  will silently block all libvirt VM traffic (SSH still works, but
  pacman/internet access won't). Fix: `iptables -I DOCKER-USER -i virbr+
  -j ACCEPT` and `-o virbr+ -j ACCEPT` (runtime-only, not persisted).
