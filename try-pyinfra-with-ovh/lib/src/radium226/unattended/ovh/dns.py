from dataclasses import dataclass
from typing import Literal, Self

from loguru import logger
from ovh import Client

from .client import make_client

type DNSRecordType = Literal["A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV", "CAA"]


def normalize_target(type: DNSRecordType, target: str) -> str:
    """
    OVH returns targets in a canonical form that differs from what one usually
    writes: TXT values come back wrapped in double quotes, and hostnames come
    back lowercased with a trailing dot. Compare targets through this.
    """
    target = target.strip()
    if type == "TXT":
        if len(target) >= 2 and target.startswith('"') and target.endswith('"'):
            target = target[1:-1]
        return target
    if type in ("CNAME", "NS", "MX", "SRV"):
        return target.lower().rstrip(".")
    return target


@dataclass(frozen=True)
class DNSRecord:
    id: int
    zone: str
    subdomain: str
    type: DNSRecordType
    target: str
    ttl: int

    @classmethod
    def from_api(cls, zone: str, payload: dict) -> Self:
        return cls(
            id=payload["id"],
            zone=zone,
            subdomain=payload["subDomain"],
            type=payload["fieldType"],
            target=payload["target"],
            ttl=payload["ttl"] or 0,
        )


class DNSZone:
    """
    Thin wrapper around `/domain/zone/{zone}` of the OVH API.

    Changes made to a zone only go live once `refresh()` is called.
    """

    def __init__(self, name: str, client: Client | None = None) -> None:
        self.name = name
        self._client = client or make_client()
        logger.debug("DNS zone {}: OVH client ready", name)

    def records(self, subdomain: str | None = None, type: DNSRecordType | None = None) -> list[DNSRecord]:
        params = {
            key: value
            for key, value in {"subDomain": subdomain, "fieldType": type}.items()
            if value is not None
        }
        logger.debug("GET /domain/zone/{}/record {}", self.name, params)
        ids: list[int] = self._client.get(f"/domain/zone/{self.name}/record", **params)
        records = [self.record(id) for id in sorted(ids)]
        logger.info("Zone {}: found {} record(s) for subdomain={!r} type={}", self.name, len(records), subdomain, type)
        for record in records:
            logger.debug("  {}", record)
        return records

    def record(self, id: int) -> DNSRecord:
        logger.debug("GET /domain/zone/{}/record/{}", self.name, id)
        payload = self._client.get(f"/domain/zone/{self.name}/record/{id}")
        return DNSRecord.from_api(self.name, payload)

    def create(self, subdomain: str, type: DNSRecordType, target: str, ttl: int = 3600) -> DNSRecord:
        logger.info("Zone {}: creating {} record {!r} -> {!r} (ttl={})", self.name, type, subdomain, target, ttl)
        payload = self._client.post(
            f"/domain/zone/{self.name}/record",
            subDomain=subdomain,
            fieldType=type,
            target=target,
            ttl=ttl,
        )
        record = DNSRecord.from_api(self.name, payload)
        logger.info("Zone {}: created record {}", self.name, record.id)
        return record

    def update(self, id: int, target: str | None = None, ttl: int | None = None) -> None:
        params = {key: value for key, value in {"target": target, "ttl": ttl}.items() if value is not None}
        logger.info("Zone {}: updating record {} with {}", self.name, id, params)
        self._client.put(f"/domain/zone/{self.name}/record/{id}", **params)

    def delete(self, id: int) -> None:
        logger.info("Zone {}: deleting record {}", self.name, id)
        self._client.delete(f"/domain/zone/{self.name}/record/{id}")

    def refresh(self) -> None:
        logger.info("Zone {}: refreshing", self.name)
        self._client.post(f"/domain/zone/{self.name}/refresh")
