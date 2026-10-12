import os

from loguru import logger
from pyinfra import host

from radium226.unattended.pyinfra.facts import freebox as freebox_facts
from radium226.unattended.pyinfra.operations import ovh

logger.enable("radium226.unattended")

zone = os.environ["OVH_DNS_ZONE"]

connection = host.get_fact(freebox_facts.ConnectionStatus)
if connection.state != "up" or connection.ipv4 is None:
    raise RuntimeError(f"The Freebox has no usable IPv4 (state={connection.state}, ipv4={connection.ipv4})")

ovh.dns_record(
    name="Point nantes.outposts to the Freebox public IP",
    zone=zone,
    subdomain="nantes.outposts",
    record_type="A",
    target=connection.ipv4,
    ttl=300,
)
