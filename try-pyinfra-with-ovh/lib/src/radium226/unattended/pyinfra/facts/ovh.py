"""
pyinfra facts are normally shell commands whose output is parsed by
`process()`. The OVH DNS zone isn't a property of the host, it's behind an HTTP
API, so `command()` only echoes the fact's arguments and `process()` (which
runs on the controller) queries the API with them.
"""

import json

from loguru import logger
from pyinfra.api import FactBase, QuoteString, StringCommand

from radium226.unattended.ovh import DNSRecord, DNSZone


class DnsRecords(FactBase[list[DNSRecord]]):
    @staticmethod
    def default() -> list[DNSRecord]:
        return []

    def command(self, zone: str, subdomain: str, record_type: str) -> StringCommand:
        arguments = {"zone": zone, "subdomain": subdomain, "record_type": record_type}
        return StringCommand("echo", QuoteString(json.dumps(arguments)))

    def process(self, output: list[str]) -> list[DNSRecord]:
        arguments = json.loads("\n".join(output))
        logger.info("Fact ovh.DnsRecords: querying OVH with {}", arguments)
        return DNSZone(arguments["zone"]).records(
            subdomain=arguments["subdomain"],
            type=arguments["record_type"],
        )
