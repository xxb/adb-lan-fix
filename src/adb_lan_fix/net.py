"""Pure helpers: serial parsing, address classification, reachability.

No side effects, no subprocesses. Everything here is unit-testable.
"""

from __future__ import annotations

import socket

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def split_serial(value: str) -> tuple[str, int] | None:
    """Return (host, port) for a network serial like `192.168.1.50:5555`.

    Returns None for USB serials, emulator names, and anything else that is not
    exactly one host:port pair.
    """
    if value.count(":") != 1:
        return None
    host, _, port = value.partition(":")
    if not host or not port.isdigit():
        return None
    port_num = int(port)
    if not 0 < port_num < 65536:
        return None
    return host, port_num


def is_loopback(host: str) -> bool:
    return host in LOOPBACK_HOSTS


def is_private_ipv4(host: str) -> bool:
    """True for RFC1918 and link-local IPv4 ranges.

    Only these are worth probing: public addresses are not subject to the
    Local Network restriction, so rewriting them would be pointless churn.
    """
    parts = host.split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return False
    octets = [int(p) for p in parts]
    if any(o > 255 for o in octets):
        return False
    a, b = octets[0], octets[1]
    return (
        a == 10
        or (a == 172 and 16 <= b <= 31)
        or (a == 192 and b == 168)
        or (a == 169 and b == 254)
    )


def reachable(host: str, port: int, timeout: float = 1.5) -> bool:
    """Can we open a TCP connection right now?"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False
