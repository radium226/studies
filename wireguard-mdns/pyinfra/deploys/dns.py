# Proof-of-concept: real dynamic DNS on the hub. Parallel to the
# avahi/mDNS setup in deploys/mdns.py, not a replacement for it: a BIND
# server on the hub, with peers registering their own name/address via
# genuine RFC 2136 dynamic updates (nsupdate) -- the actual standard
# protocol, not a bespoke one. Open/unauthenticated updates: fine for a
# lab POC, not something to do for real. See files/wg-dns-register and
# the README.
from pyinfra import host
from pyinfra.api import deploy
from pyinfra.facts.files import File
from pyinfra.operations import files, pacman, systemd
from pyinfra.operations.util import any_changed


@deploy("DNS")
def dns():
    pacman.packages(
        name="Install bind",
        packages=["bind"],
    )

    if host.name == "server":
        named_conf = files.template(
            name="Deploy named.conf",
            src="templates/named.conf.j2",
            dest="/etc/named.conf",
            user="root",
            group="named",
            mode="640",
        )

        # force: false in the old Ansible playbook -- don't clobber a zone
        # file BIND has since rewritten with dynamic records. Whether the
        # file already exists is safe to check as an ordinary fact here:
        # nothing earlier in this deploy creates it, so there's no ordering
        # hazard like the WireGuard keypair fact has.
        if not host.get_fact(File, path="/var/named/wg.zone"):
            files.template(
                name="Deploy the wg zone skeleton (SOA/NS only -- everything else is dynamic)",
                src="templates/wg.zone.j2",
                dest="/var/named/wg.zone",
                user="named",
                group="named",
                mode="644",
            )

        if not host.get_fact(File, path="/var/named/db.10.0.0"):
            files.template(
                name="Deploy the reverse zone skeleton",
                src="templates/db.10.0.0.zone.j2",
                dest="/var/named/db.10.0.0",
                user="named",
                group="named",
                mode="644",
            )

        # named needs to bind to wg0's own address (10.0.0.1), so it must
        # not start before that interface exists -- this drop-in makes that
        # true on every boot, not just this one provisioning run.
        files.directory(
            name="Create the systemd drop-in directory",
            path="/etc/systemd/system/named.service.d",
            mode="755",
        )
        named_drop_in = files.put(
            name="Deploy the drop-in",
            src="files/named-wait-for-wg0.conf",
            dest="/etc/systemd/system/named.service.d/override.conf",
            mode="644",
        )

        systemd.service(
            name="Start and enable named",
            service="named",
            running=True,
            enabled=True,
        )

        # See deploy.py's file header for why a plain
        # `restarted=named_conf.did_change()` doesn't work here.
        systemd.service(
            name="Restart named (config changed)",
            service="named",
            running=True,
            restarted=True,
            daemon_reload=True,
            _if=any_changed(named_conf, named_drop_in),
        )

    # Plain (non-templated) file: HOSTNAME, IPV4, IPV6 and DNS_SERVER are all
    # derived at runtime from wg0 itself (see wg-dns-register's own header),
    # so the same script deploys unchanged to every peer.
    if host.name in ("server", "client1", "client2"):
        files.put(
            name="Deploy the DNS self-registration script",
            src="files/wg-dns-register",
            dest="/usr/local/bin/wg-dns-register",
            mode="755",
        )
