#!/usr/bin/env python3
"""Unicast mDNS repeater for the WireGuard hub.

The hub has ONE shared wg0 interface with every spoke as a peer (not one
interface per spoke) -- which scales to any number of spokes, but means
WireGuard itself cannot reflect multicast between them: cryptokey routing
maps a given destination to exactly one peer per interface, and a
multicast address can't be "owned" by every spoke's peer entry at once.

This sidesteps that instead of working around it: join the mDNS
multicast group locally (a plain socket/IGMP membership, unrelated to
WireGuard's peer ACLs) and, for every packet that arrives, re-send an
identical copy as plain unicast UDP to each spoke's own address. WireGuard
routes unicast to a single peer perfectly fine -- that was never the
problem -- so this reaches every spoke without needing multicast routing
through the tunnel at all.

Configured entirely through environment variables (see
mdns-unicast-repeater.service / the .env file it loads) rather than
templating, so this file is plain, importable Python -- see
tests/test_repeater_unit.py.
"""
import logging
import os
import socket
import struct
import threading

logging.basicConfig(level=logging.INFO, format="%(threadName)s: %(message)s")
log = logging.getLogger(__name__)

MDNS_PORT = 5353
MDNS_GROUP_V4 = "224.0.0.251"
MDNS_GROUP_V6 = "ff02::fb"


def forward_targets(peers: list[str], src_ip: str) -> list[str]:
    """Which peers a packet from src_ip should be repeated to.

    Excludes the sender: avahi already de-dupes its own traffic, so
    sending it back would just be wasted work, not a correctness issue.
    """
    return [peer for peer in peers if peer != src_ip]


def repeat_v4(local_v4: str, peers_v4: list[str]) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", MDNS_PORT))
    mreq = socket.inet_aton(MDNS_GROUP_V4) + socket.inet_aton(local_v4)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    log.info("listening for IPv4 mDNS on %s, repeating to %s", local_v4, peers_v4)

    # Sent from an ephemeral source port, mDNS implementations (avahi
    # included) silently ignore these -- real mDNS traffic always comes
    # from port 5353 too, on both ends. Bind the sending socket there as
    # well (SO_REUSEADDR lets it share the port with the receiving
    # socket above).
    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    send_sock.bind(("0.0.0.0", MDNS_PORT))
    while True:
        data, (src_ip, _src_port) = sock.recvfrom(65535)
        targets = forward_targets(peers_v4, src_ip)
        log.info("got %d bytes from %s, forwarding to %s", len(data), src_ip, targets)
        for peer in targets:
            send_sock.sendto(data, (peer, MDNS_PORT))


def repeat_v6(iface: str, peers_v6: list[str]) -> None:
    ifindex = socket.if_nametoindex(iface)

    sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("::", MDNS_PORT))
    group = socket.inet_pton(socket.AF_INET6, MDNS_GROUP_V6)
    mreq = group + struct.pack("@I", ifindex)
    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, mreq)
    log.info("listening for IPv6 mDNS on %s (ifindex %d), repeating to %s", iface, ifindex, peers_v6)

    send_sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    send_sock.bind(("::", MDNS_PORT))
    while True:
        data, addr = sock.recvfrom(65535)
        src_ip = addr[0].split("%")[0]
        targets = forward_targets(peers_v6, src_ip)
        log.info("got %d bytes from %s, forwarding to %s", len(data), src_ip, targets)
        for peer in targets:
            send_sock.sendto(data, (peer, MDNS_PORT, 0, ifindex))


def run_forever(name, target, *args) -> None:
    while True:
        try:
            target(*args)
        except Exception:
            log.exception("%s crashed, restarting in 2s", name)
            threading.Event().wait(2)


def main() -> None:
    iface = os.environ["MDNS_REPEATER_IFACE"]
    local_v4 = os.environ["MDNS_REPEATER_LOCAL_V4"]
    peers_v4 = os.environ["MDNS_REPEATER_PEERS_V4"].split(",")
    peers_v6 = os.environ["MDNS_REPEATER_PEERS_V6"].split(",")

    threading.Thread(
        target=run_forever, args=("v4", repeat_v4, local_v4, peers_v4), daemon=True, name="v4"
    ).start()
    threading.Thread(
        target=run_forever, args=("v6", repeat_v6, iface, peers_v6), daemon=True, name="v6"
    ).start()
    threading.Event().wait()


if __name__ == "__main__":
    main()
