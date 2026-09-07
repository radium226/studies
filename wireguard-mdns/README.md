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
domains (WireGuard mesh vs. foreign_lan) stay fully separate: nothing
bridges the two, so nothing on `foreign_lan` is reachable from
`server`/`client2`, and vice versa.

## Topology

```
                wg0 (10.0.0.0/24)
   server  -------------------------  client1 (10.0.0.2/24)
  (10.0.0.1)          |
                       +--------------  client2 (10.0.0.3/24)
```

ONE shared WireGuard interface on the server, with both spokes as peers
-- not one dedicated interface per spoke. See "Why not one WireGuard
interface per spoke?" below for why an earlier version of this study did
exactly that, and why it doesn't scale.

## Why a unicast mDNS repeater?

mDNS is not a routed protocol -- a host only ever hears mDNS traffic from
others on the *same* link. `server`'s two spokes are each on their own
WireGuard tunnel, so without something in the middle, `client1` and
`client2` can never hear each other at all, even though both can reach
`server`.

The obvious fix is a reflector on the hub: hear multicast traffic
arriving on one link, repeat it out the other. That's what avahi's own
`enable-reflector` does, and it's *exactly* why an earlier version of
this study gave `server` two separate WireGuard interfaces (`wg-c1`,
`wg-c2`) instead of one shared `wg0` -- avahi's reflector operates
between distinct *interfaces*, and WireGuard's cryptokey routing can only
ever deliver a given destination (including a multicast address like
`224.0.0.251`) to a single peer per interface, so a shared interface with
multiple peers can't have multicast routed to more than one of them.

That works, but it doesn't scale: N spokes means N dedicated WireGuard
interfaces on the hub, N keypairs, N listen ports, N-entry `avahi
allow-interfaces` config. Fine for a 2-spoke demo, unworkable for
anything real.

**This version replaces that with a small daemon,
`mdns-unicast-repeater`, deployed only on `server`.** It joins the
mDNS multicast group as an ordinary local socket (unrelated to
WireGuard's peer ACLs -- IGMP/MLD group membership is a purely local
kernel/host concern) on the *one shared* `wg0`, and for every packet it
sees, re-sends an identical copy as plain **unicast** UDP to each spoke's
own address. WireGuard has never had any trouble routing unicast to a
specific peer on a shared interface -- that was never the problem -- so
this reaches every spoke without needing multicast to traverse the
tunnel at all, and scales to any number of spokes by just adding an
address to its peer list.

Two details that mattered when building it (see
`ansible/files/mdns-unicast-repeater`):

- The repeater's outbound socket must also bind to port 5353. A socket
  that sends from a random ephemeral port gets silently ignored by
  avahi (and mDNS implementations generally) -- real mDNS traffic
  always comes from port 5353 on both ends, and nothing here enforces
  that except doing it deliberately.
- Sending back to whichever host a packet came from is harmless (avahi
  de-dupes its own traffic) but wasteful, so each host is excluded from
  its own repeat.

Since there's only one interface on the hub now, avahi's own reflector is
disabled everywhere (`enable-reflector: false`) -- there's nothing left
for it to reflect between.

## Why not one WireGuard interface per spoke?

Kept here because the constraint is real and worth understanding, even
though this study no longer works around it that way.

Instead, `server` used to run two dedicated interfaces, `wg-c1` (to
client1) and `wg-c2` (to client2), each a genuine point-to-point link, so
that avahi's reflector could treat them as ordinary distinct interfaces.
Two WireGuard-specific quirks came with it:

- `wg-quick` auto-adds a kernel route for every `AllowedIPs` entry, and
  `224.0.0.0/4` was shared by both of the server's interfaces, which
  collided ("File exists") on whichever interface came up second.
  Routing had to be disabled (`Table = off`) on the server's interfaces,
  with only the one unicast `/32` route each interface actually needed
  added by hand.
- Interestingly, the same collision did **not** happen for IPv6: both
  interfaces joined `ff02::fb` independently with no error, since IPv6
  link-local multicast is scoped by interface index at the socket level
  rather than resolved through the single shared main routing table the
  way IPv4's class-D multicast is. There's no "one owning device"
  constraint to collide on for a link-scope address.

Both problems are moot now that the hub is back to one shared interface
-- there's no second interface to collide with, and multicast doesn't
get routed through WireGuard at all any more, on either address family.

## IPv6

Every WireGuard interface carries a ULA address in `fd00::/64` alongside
its IPv4 one, and every peer's `AllowedIPs` includes `ff02::fb/128`
(mDNS's IPv6 link-local multicast group) alongside `224.0.0.0/4`. avahi
has both `use-ipv4`/`use-ipv6` set to `yes` everywhere, and the repeater
runs an independent IPv6 loop alongside its IPv4 one.

`avahi-resolve -6`/`avahi-browse` work correctly end-to-end over IPv6,
across both the mesh and the `foreign_lan` domain -- see "Why `ping
foo.local` didn't work at first" below for why `ping -6`/`getent
ahostsv6` still don't.

## Usage

```bash
vagrant up
```

This boots `server`, `client1`, `client2`, `foreign` (Arch Linux, libvirt
provider), then runs `ansible/playbook.yml` against all of them at once
(needed since the playbook wires each host's WireGuard public key into
the others' peer config).

If `vagrant`/`virsh` report a permissions error, make sure your user is
in the `libvirt` group and `libvirtd.service` is running, then log out
and back in (or run the command via `sudo -g libvirt -u "$USER" ...`) so
the new group membership takes effect.

## Verification

From `client1`, resolve and reach `client2` purely over the WireGuard
overlay, relayed through the hub's unicast repeater:

```bash
vagrant ssh client1
ping -c2 client2.local              # resolves to 10.0.0.3
avahi-resolve -n client2.local
avahi-resolve -6 -n client2.local   # resolves to fd00::3
avahi-browse -rt _ssh._tcp          # should list server and client2
ssh vagrant@client2.local           # full round trip: discover, resolve, connect
```

Each host also advertises one fake (unbacked -- nothing is actually
listening) service of a different type, to check discovery of more than
just SSH: a fake NAS (`_smb._tcp`) on `server`, a fake web app
(`_http._tcp`) on `client1`, a fake printer (`_ipp._tcp`) on `client2`,
and a fake widget (`_http._tcp`) on `foreign`. `avahi-browse -at` from
any of `server`/`client1`/`client2` should list exactly 6 services (3x
SSH + 3 fakes, all tagged `wg0`) -- never anything from `foreign`.

`sudo journalctl -u mdns-unicast-repeater -f` on `server` shows every
packet it captures and which peers it forwards to, in real time -- handy
for confirming it's actually doing something versus avahi resolving
purely from its own local cache.

`ip addr show wg0` / `wg show` on any node shows the tunnel and handshake
state. On `server`, `wg show` lists both `client1` and `client2` as peers
of the single `wg0`.

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
client2.local` -> `+ 10 0 client2.local 10.0.0.3`), the same way
`avahi-resolve` does. That's also why it correctly resolves
`foreign.local` from the dual-homed `client1` just as well as
`client2.local`.

**IPv6 is a different story, and stays broken**: `ping -6`/`getent
ahostsv6` still don't return the real address. `strace` shows exactly
why -- the `mdns6_minimal` module sends `RESOLVE-HOSTNAME-IPV4` (not
`RESOLVE-HOSTNAME-IPV6`) to avahi's socket regardless of which module
you loaded, so it always gets back the IPv4 answer, which `ping -6`
correctly rejects as the wrong address family (`getent ahostsv6`
"succeeds" only by synthesizing a v4-mapped address, `::ffff:10.0.0.3`,
not the real `fd00::3`). This is a genuine bug in Arch's `nss-mdns
0.15.1-2` package, not a misconfiguration -- there's nothing to fix on
our side for it. `avahi-resolve -6`/`avahi-browse` remain fully correct
and are the reliable way to verify IPv6 mDNS.

## Proof of concept: dynamic DNS (parallel to avahi, not a replacement)

A second, independent name-resolution mechanism alongside everything
above: `server` runs BIND (`named`), and every mesh host (including
`server` itself) registers its own name and address there via a real
**RFC 2136 dynamic DNS update** (`nsupdate`) -- the actual standard
protocol, the same mechanism real "wide-area Bonjour" and dynamic-DNS
setups use, not something bespoke. Two zones: `wg` (forward, `A`/`AAAA`)
and `0.0.10.in-addr.arpa` (reverse, `PTR`, IPv4 only). Both allow
open/unauthenticated updates from anyone -- fine for a lab POC, not
something to do for real.

Registration happens from `wg0`'s `PostUp` (`wg-dns-register`),
so it fires on every tunnel-up, not just once during provisioning.
`resolvectl dns`/`resolvectl domain '~wg'` (also set from `PostUp`) wire
up split-DNS routing, so plain tools resolve `*.wg` names with zero
special tooling, directly comparable to the avahi/mDNS setup:

```bash
vagrant ssh client1
ping client2.wg              # resolves via real unicast DNS, not mDNS
getent hosts client2.wg
getent hosts 10.0.0.3        # reverse lookup -- and it actually works here,
                              # unlike the broken nss-mdns reverse path
```

Two real things surfaced building this:

- **BIND refuses to load a zone whose NS record has no address (glue)
  record**, when the NS target is *inside* that same zone
  ("in-bailiwick"). `wg`'s NS points at `server.wg`, which is inside
  `wg` itself, so the zone failed to load at all (`SERVFAIL` on every
  update) until `server.wg`'s A/AAAA was pre-seeded statically in the
  zone file -- everyone else's records, including a second copy of
  `server.wg` itself, still arrive purely dynamically. (The reverse
  zone's NS target is *outside* it, so it never hit this.)
- Testing this surfaced a **real, pre-existing gap in the mesh itself**:
  `net.ipv6.conf.all.forwarding` was never enabled on the server, only
  the IPv4 equivalent -- so IPv6 unicast between client1 and client2
  (through the hub) was silently broken this whole time. mDNS-only
  testing never exercised a bare cross-spoke IPv6 ping outside of a
  resolved name, so it went unnoticed until this POC's `ping client2.wg`
  needed it. Now fixed alongside `net.ipv4.ip_forward`.

Known limitations, left out deliberately to keep this a small POC: no
IPv6 reverse zone (`ip6.arpa`), and `dig` (unlike `ping`/`getent`) needs
an explicit `@10.0.0.1` -- these boxes' `/etc/resolv.conf` was never
pointed at systemd-resolved's stub listener, so `dig`'s own direct query
path (which bypasses NSS/resolved entirely) has nowhere to send an
unqualified query.

## Tests

```bash
uv sync
uv run pytest              # everything (needs the VMs up for most of it)
uv run pytest -m "not integration"   # unit tests only: fast, no VMs needed
```

Two kinds, in `tests/`:

- **Unit** (`test_repeater_unit.py`): the repeater's pure forwarding
  logic (`forward_targets` -- "which peers should this packet go to"),
  loaded directly from `ansible/files/mdns-unicast-repeater` by path.
  No sockets, no VMs, runs in well under a second.
- **Integration** (everything else, marked `@pytest.mark.integration`):
  SSHes into the live VMs and checks the actual behavior this whole
  study is about -- WireGuard handshakes, mDNS resolution and service
  discovery in both address families, the two-domain isolation, the
  repeater actually forwarding live traffic, and the resolver-config
  fixes. This is the suite that answers "does everything we built
  actually still work" -- run it after any change to the playbook or
  templates. The `ping -6`/`getent ahostsv6` tests are intentionally
  `xfail(strict=True)`: they document the known upstream nss-mdns bug,
  and the suite will tell you loudly (an unexpected XPASS) if a future
  package update ever fixes it.

The integration suite skips itself with a clear message (not a wall of
individual connection-refused failures) if the VMs aren't up.

## Notes

- Boxes have no firewall by default, so no explicit forward/accept rules
  were added beyond `net.ipv4.ip_forward` (needed on `server` so unicast
  traffic between client1 and client2, e.g. an actual SSH connection to a
  discovered host, gets routed through the hub).
- Re-running `vagrant provision` re-applies the whole playbook and always
  restarts WireGuard/avahi/the repeater -- fine for this kind of
  throwaway study, not written for idempotent no-op re-runs.
- First boot runs a full `pacman -Syu` (the box image is stale enough
  that skipping it hits partial-upgrade file conflicts) and then reboots
  each VM (via the `vagrant-reload` plugin) before starting WireGuard,
  since the upgrade pulls in a new kernel.
- If your host runs Docker, its default `FORWARD` policy is `DROP` and
  will silently block all libvirt VM traffic (SSH still works, but
  pacman/internet access won't). Fix: `iptables -I DOCKER-USER -i virbr+
  -j ACCEPT` and `-o virbr+ -j ACCEPT` (runtime-only, not persisted).
- If you're re-running this against VMs built by an earlier version of
  this study, the playbook cleans up the old `wg-c1`/`wg-c2` interfaces
  on the server automatically (both used to listen on the same UDP port
  the new shared `wg0` needs).
