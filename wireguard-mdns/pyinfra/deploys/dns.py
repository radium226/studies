# Proof-of-concept: real dynamic DNS on the hub. Parallel to the
# avahi/mDNS setup in deploys/mdns.py, not a replacement for it: a BIND
# server on the hub, with peers registering their own name/address via
# genuine RFC 2136 dynamic updates (nsupdate) -- the actual standard
# protocol, not a bespoke one. Open/unauthenticated updates: fine for a
# lab POC, not something to do for real. See files/dns/wg-dns-register
# and the README.
from pyinfra import host
from pyinfra.api import deploy
from pyinfra.facts.files import File
from pyinfra.operations import files, pacman, systemd

from .defaults import WG_DATA_DEFAULTS

# See deploys/wireguard.py's JINJA_ENV_KWARGS for why every
# files.template() call here passes this, even though none of these
# templates currently have conditionals of their own.
JINJA_ENV_KWARGS = {"trim_blocks": True, "lstrip_blocks": True}


# data_defaults: see deploys/wireguard.py's own @deploy for why.
@deploy("Setup DNS", data_defaults=WG_DATA_DEFAULTS)
def setup_dns():
    pacman.packages(
        name="Install bind",
        packages=["bind"],
    )

    if "hub" in host.groups:
        named_conf = files.template(
            name="Deploy named.conf",
            src="templates/dns/named.conf.j2",
            dest="/etc/named.conf",
            user="root",
            group="named",
            mode="640",
            jinja_env_kwargs=JINJA_ENV_KWARGS,
            wg_ipv4=host.data.wg_ipv4,
            wg_ipv6=host.data.wg_ipv6,
            reverse_zone_name=host.data.wg_ipv4_reverse_zone,
            reverse_zone_file=host.data.wg_ipv4_reverse_zone_file,
        )

        # force: false in the old Ansible playbook -- don't clobber a zone
        # file BIND has since rewritten with dynamic records. Whether the
        # file already exists is safe to check as an ordinary fact here:
        # nothing earlier in this deploy creates it, so there's no ordering
        # hazard like the WireGuard keypair fact has.
        if not host.get_fact(File, path="/var/named/wg.zone"):
            files.template(
                name="Deploy the wg zone skeleton (SOA/NS only -- everything else is dynamic)",
                src="templates/dns/wg.zone.j2",
                dest="/var/named/wg.zone",
                user="named",
                group="named",
                mode="644",
                jinja_env_kwargs=JINJA_ENV_KWARGS,
                wg_ipv4=host.data.wg_ipv4,
                wg_ipv6=host.data.wg_ipv6,
            )

        if not host.get_fact(File, path=f"/var/named/{host.data.wg_ipv4_reverse_zone_file}"):
            files.template(
                name="Deploy the reverse zone skeleton",
                src="templates/dns/db.10.0.0.zone.j2",
                dest=f"/var/named/{host.data.wg_ipv4_reverse_zone_file}",
                user="named",
                group="named",
                mode="644",
                jinja_env_kwargs=JINJA_ENV_KWARGS,
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
            src="files/dns/named-wait-for-wg0.conf",
            dest="/etc/systemd/system/named.service.d/override.conf",
            mode="644",
        )

        systemd.service(
            name="Start and enable named",
            service="named",
            running=True,
            enabled=True,
            restarted=named_conf.will_change or named_drop_in.will_change,
            daemon_reload=named_drop_in.will_change,
        )

    # Plain (non-templated) file: HOSTNAME, IPV4, IPV6 and DNS_SERVER are all
    # derived at runtime from wg0 itself (see wg-dns-register's own header),
    # so the same script deploys unchanged to every peer.
    if "mesh" in host.groups:
        files.put(
            name="Deploy the DNS self-registration script",
            src="files/dns/wg-dns-register",
            dest="/usr/local/bin/wg-dns-register",
            mode="755",
        )
