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
import os
from typing import Optional

log = logging.getLogger("TethrLink")

# The subnet Android hands out for USB tethering. This is the AOSP default
# and correct for stock devices, but a ROM or OEM can hand out something
# else — without an escape hatch, a real tablet on such a device would be
# silently rejected as a non-tether peer with no obvious explanation.
# TETHRLINK_TETHER_SUBNET (below) is that escape hatch.
DEFAULT_TETHER_SUBNET = "192.168.42.0/24"


def resolve_tether_subnet(raw: Optional[str]) -> str:
    """Validate a candidate tethering subnet, falling back to the default
    on anything malformed or implausible.

    Pure function — no environment access — so the override logic is
    testable without touching os.environ or reloading the module. Never
    raises: this feeds a module-level constant computed at import time, and
    a bad TETHRLINK_TETHER_SUBNET value must degrade to "use the default",
    not crash the server on startup. `strict=True` also catches a network
    string with host bits set (e.g. "192.168.42.5/24") as malformed, since
    that isn't a subnet declaration either.

    Beyond syntax, the candidate must plausibly *be* a USB-tethering subnet,
    fail closed rather than open, and be loud either way:

    - IPv4 only. An IPv6 network is never a valid Android USB tethering
      subnet, and accepting one would silently reject every real (IPv4)
      tether peer in is_tether_peer() — a fail-closed but silent breakage.
    - Private and no broader than /16. Every real tethering subnet we know
      of — AOSP's default and every OEM/ROM variant — is a small private
      range: 192.168.x.0/24, 172.x.x.0/24 or 10.x.x.0/24. Requiring
      "private and /16 or narrower" admits all of those, plus generous
      headroom for anything unusual, while rejecting sweeping values like
      0.0.0.0/0, a public range, or a whole RFC1918 block. This override
      exists to widen *which* small subnet is trusted, not to widen *how
      much* is trusted — a value that fails this check would silently
      undo the entire tether filter (see is_tether_peer()) rather than
      just fail to find the tablet, so it must be rejected loudly and
      fall back to the real default, never to something broader.

    Every branch logs: malformed input, an implausible-but-well-formed
    network, and — just as important, since this is a security control —
    a successful override, so an operator can see in the log that the
    default is no longer in effect.
    """
    if raw is None:
        return DEFAULT_TETHER_SUBNET
    candidate = raw.strip()
    if not candidate:
        return DEFAULT_TETHER_SUBNET
    try:
        network = ipaddress.ip_network(candidate, strict=True)
    except ValueError as e:
        log.warning(
            "Ignoring malformed TETHRLINK_TETHER_SUBNET=%r (%s) — falling "
            "back to the default tethering subnet %s",
            raw, e, DEFAULT_TETHER_SUBNET,
        )
        return DEFAULT_TETHER_SUBNET

    if network.version != 4:
        log.warning(
            "Ignoring TETHRLINK_TETHER_SUBNET=%r: IPv6 is never a valid "
            "Android USB tethering subnet, and accepting it would silently "
            "reject every real IPv4 tether peer — falling back to the "
            "default tethering subnet %s",
            raw, DEFAULT_TETHER_SUBNET,
        )
        return DEFAULT_TETHER_SUBNET

    if not network.is_private or network.prefixlen < 16:
        log.warning(
            "Ignoring TETHRLINK_TETHER_SUBNET=%r: not a plausible USB "
            "tethering subnet (must be private and no broader than /16) — "
            "a value this sweeping would silently disable the tether "
            "filter, so falling back to the default tethering subnet %s",
            raw, DEFAULT_TETHER_SUBNET,
        )
        return DEFAULT_TETHER_SUBNET

    log.warning(
        "TETHRLINK_TETHER_SUBNET override in effect: serving peers on %s "
        "instead of the default tethering subnet %s — this is expected "
        "only if this device's tethering implementation doesn't use the "
        "AOSP default",
        network, DEFAULT_TETHER_SUBNET,
    )
    return str(network)


TETHER_SUBNET = resolve_tether_subnet(os.environ.get("TETHRLINK_TETHER_SUBNET"))
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
