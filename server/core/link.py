"""Which peers the server is willing to serve.

TethrLink is a wired second monitor: the only peer that should ever reach it
is the tablet on the other end of the USB cable. The server nevertheless
binds 0.0.0.0 and previously answered anyone on the network — tolerable for a
view-only stream, but not once input can drive the machine.

Filtering happens at accept time rather than by binding to the tethering
address, because that interface has no IPv4 address until tethering is
actually active. The server routinely starts first, so a bind-time approach
would simply fail, and would need re-binding every time the cable moved.

No `gi`/`dbus` import: this is address logic and should be testable anywhere.
"""

import ipaddress
import logging
from typing import Optional

log = logging.getLogger("TethrLink")

# The subnet Android hands out for USB tethering.
TETHER_SUBNET = "192.168.42.0/24"
_TETHER_NET = ipaddress.ip_network(TETHER_SUBNET)


def is_tether_peer(addr: Optional[str]) -> bool:
    """True if `addr` is reachable only over the USB cable (or is local).

    Loopback is permitted so local diagnostic tools and test harnesses keep
    working; a loopback peer is already running as the user.
    """
    if not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    return ip in _TETHER_NET


def tether_broadcast_address() -> str:
    """Where discovery announcements go.

    Scoped to the tethering subnet: broadcasting to 255.255.255.255 announces
    the machine's hostname and port to every network it is attached to, which
    is needless exposure even when connections are filtered.
    """
    return str(_TETHER_NET.broadcast_address)
