from collections.abc import Generator

from loguru import logger
from pyinfra import host
from pyinfra.api import FunctionCommand, operation

from radium226.unattended.ovh import DNSRecordType, DNSZone, normalize_target

from radium226.unattended.pyinfra.facts.ovh import DnsRecords


@operation()
def dns_record(
    zone: str,
    subdomain: str,
    record_type: DNSRecordType,
    target: str,
    ttl: int = 3600,
    present: bool = True,
) -> Generator[FunctionCommand, None, None]:
    """
    Ensure a DNS record exists (or not) in an OVH DNS zone.

    Records are matched on zone, subdomain, type and target, so a subdomain can
    hold several records of the same type (e.g. multiple TXT). Targets are
    compared in OVH's canonical form (see `normalize_target`). A record that
    matches but has another TTL is updated in place, and identical duplicates
    are deleted. The zone is refreshed once
    after any change, which is what makes it go live at OVH.

    + zone: the DNS zone, e.g. `example.com`
    + subdomain: the record name relative to the zone, `""` for the apex
    + record_type: A, AAAA, CNAME, TXT, ...
    + target: the value of the record
    + ttl: time to live, in seconds
    + present: `False` to delete the matching record(s)
    """

    records = host.get_fact(DnsRecords, zone=zone, subdomain=subdomain, record_type=record_type)
    wanted = normalize_target(record_type, target)
    matching = [record for record in records if normalize_target(record.type, record.target) == wanted]
    dns_zone = DNSZone(zone)
    logger.info(
        "Operation ovh.dns_record: want {} {!r}.{} -> {!r} (ttl={}, present={}); "
        "{} existing record(s), {} matching (normalized target {!r})",
        record_type, subdomain, zone, target, ttl, present, len(records), len(matching), wanted,
    )

    changed = False
    if present:
        if not matching:
            logger.info("Operation ovh.dns_record: no matching record, will create it")
            yield FunctionCommand(dns_zone.create, (subdomain, record_type, target, ttl), {})
            changed = True
        else:
            keeper, *duplicates = matching
            logger.info("Operation ovh.dns_record: keeping record {} (ttl={})", keeper.id, keeper.ttl)
            if keeper.ttl != ttl:
                logger.info("Operation ovh.dns_record: ttl {} != {}, will update", keeper.ttl, ttl)
                yield FunctionCommand(dns_zone.update, (keeper.id,), {"ttl": ttl})
                changed = True
            for duplicate in duplicates:
                logger.info("Operation ovh.dns_record: record {} is a duplicate, will delete it", duplicate.id)
                yield FunctionCommand(dns_zone.delete, (duplicate.id,), {})
                changed = True
    else:
        for record in matching:
            logger.info("Operation ovh.dns_record: record {} must be absent, will delete it", record.id)
            yield FunctionCommand(dns_zone.delete, (record.id,), {})
            changed = True

    if changed:
        logger.info("Operation ovh.dns_record: zone changed, will refresh it")
        yield FunctionCommand(dns_zone.refresh, (), {})
    else:
        logger.info("Operation ovh.dns_record: nothing to do")
        host.noop(f"DNS record {subdomain or '@'}.{zone} {record_type} {target!r} is already in the desired state")
