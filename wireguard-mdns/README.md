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

**Use avahi's own tools (`avahi-resolve`/`avahi-browse`), not `ping` or
`getent`, to judge whether mDNS "works" on a given interface of a
multi-homed host.** `avahi-daemon` binds and resolves per-interface
correctly regardless of the two domains, but plain NSS-based lookups
(`ping foo.local`, `getent hosts foo.local`, via `nss-mdns`) don't --
they send one query that follows the kernel's normal routing decision
for the mDNS multicast address, and `wg-quick` auto-adds a route for
`224.0.0.0/4` toward `wg0`. Since a destination can only have one owning
device in the main routing table, that's the *only* route to
`224.0.0.251` on a multi-homed client, so `ping client2.local` (reachable
via wg0) works, but `ping foreign.local` (only reachable via eth2) gives
"Temporary failure in name resolution" even though it's genuinely
discoverable and resolvable via avahi. This is an inherent limitation of
plain NSS mDNS resolution on any multi-homed multicast host, not
something specific to this setup.

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
