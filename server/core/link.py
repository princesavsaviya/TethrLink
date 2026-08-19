"""Which peers the server is willing to serve.

TethrLink is a wired second monitor: the only peer that should ever reach it
is the tablet on the other end of the USB cable. The server nevertheless
binds 0.0.0.0 and previously answered anyone on the network — tolerable for a
view-only stream, but not once input can drive the machine.

Filtering happens at accept time rather than by binding to the tethering
address, because that interface has no IPv4 address until tethering is
actually active. The server routinely starts first, so a bind-time approach
would simply fail, and would need re-binding every time the cable moved.

What counts as "the tether", concretely: Android hands the tethered subnet
to whichever *network interface* the USB cable created, and that subnet
varies by device/ROM/OEM — there is no fixed value to hardcode (a previous
version of this file tried "192.168.42.0/24", the AOSP default, and broke
for every device that hands out something else, e.g. 10.x.x.0/24). The
reliable, device-independent signal is which interface is USB-attached: on
Linux, `/sys/class/net/<iface>/device` resolves through a path segment named
`usbN` for any interface enumerated over USB, and does not for interfaces
enumerated over PCI (Wi-Fi, Ethernet) or created by software (docker
bridges, loopback). So: enumerate USB-attached interfaces, read whatever
IPv4 subnet each currently has (if any), and trust exactly that — nothing
hardcoded, nothing cached, because the cable can be plugged, unplugged, and
tethering toggled at any time after the server has already started.

No `gi`/`dbus` import: this is address logic and should be testable anywhere.
"""

import fcntl
import ipaddress
import logging
import os
import re
import socket
import struct
from pathlib import Path
from typing import Callable, List, Optional

log = logging.getLogger("TethrLink")

# Where Linux exposes network interfaces. A parameter (not a bare constant)
# on every function that walks it, so tests can point at a fake directory
# tree instead of this machine's real interfaces.
SYSFS_NET_ROOT = Path("/sys/class/net")

# A USB device's sysfs path always passes through a directory segment named
# exactly "usb" + a bus number (e.g. "usb1"), inserted by the kernel's USB
# subsystem — never a substring of some other component. Matching the whole
# path segment (rather than a bare "usb" substring) avoids false positives
# from vendor/device names that happen to contain "usb".
_USB_PATH_SEGMENT = re.compile(r"^usb\d+$")

# Classic Linux ioctls for an interface's primary IPv4 address/netmask.
# Stdlib-only (fcntl + socket), no third-party dependency, no shelling out.
_SIOCGIFADDR = 0x8915
_SIOCGIFNETMASK = 0x891B


def resolve_tether_subnet(raw: Optional[str]) -> Optional[str]:
    """Validate a candidate TETHRLINK_TETHER_SUBNET override.

    Returns the validated subnet string, or None if `raw` is unset, blank,
    or fails validation — callers must treat None as "no override in
    effect; fall back to USB-interface auto-detection", never as "use some
    default subnet", because there is no longer a single default subnet to
    fall back to (see module docstring).

    Pure function — no environment access — so the override logic is
    testable without touching os.environ. Never raises: a bad
    TETHRLINK_TETHER_SUBNET value must degrade to "no override", not crash
    the server on startup. `strict=True` also catches a network string with
    host bits set (e.g. "192.168.42.5/24") as malformed, since that isn't a
    subnet declaration either.

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
      fall back to auto-detection, never to something broader.

    Every branch logs: malformed input, an implausible-but-well-formed
    network, and — just as important, since this is a security control —
    a successful override, so an operator can see in the log that
    auto-detection is no longer in effect.
    """
    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    try:
        network = ipaddress.ip_network(candidate, strict=True)
    except ValueError as e:
        log.warning(
            "Ignoring malformed TETHRLINK_TETHER_SUBNET=%r (%s) — falling "
            "back to USB-interface auto-detection",
            raw, e,
        )
        return None

    if network.version != 4:
        log.warning(
            "Ignoring TETHRLINK_TETHER_SUBNET=%r: IPv6 is never a valid "
            "Android USB tethering subnet, and accepting it would silently "
            "reject every real IPv4 tether peer — falling back to "
            "USB-interface auto-detection",
            raw,
        )
        return None

    if not network.is_private or network.prefixlen < 16:
        log.warning(
            "Ignoring TETHRLINK_TETHER_SUBNET=%r: not a plausible USB "
            "tethering subnet (must be private and no broader than /16) — "
            "a value this sweeping would silently disable the tether "
            "filter, so falling back to USB-interface auto-detection",
            raw,
        )
        return None

    log.warning(
        "TETHRLINK_TETHER_SUBNET override in effect: serving peers on %s "
        "instead of auto-detecting the USB-tethered interface — this "
        "takes precedence over detection entirely",
        network,
    )
    return str(network)


def _override_network(raw: Optional[str] = None) -> Optional[ipaddress.IPv4Network]:
    """The validated override network, or None if no override is in effect.

    Re-reads os.environ (unless `raw` is passed explicitly, for testing)
    and re-validates on every call — no caching — so a value exported after
    the server started, or changed later, takes effect immediately.
    """
    if raw is None:
        raw = os.environ.get("TETHRLINK_TETHER_SUBNET")
    resolved = resolve_tether_subnet(raw)
    if resolved is None:
        return None
    return ipaddress.ip_network(resolved)


def _is_usb_device_path(path: Path) -> bool:
    """True if a resolved `.../device` sysfs path passes through a USB bus
    segment (e.g. ".../usb1/1-4/1-4:1.0"). See module docstring for why this
    is the reliable, device-independent signal for "USB-attached".
    """
    return any(_USB_PATH_SEGMENT.match(part) for part in path.parts)


def usb_interface_names(sysfs_root: Path = SYSFS_NET_ROOT) -> List[str]:
    """Names of network interfaces that are USB-attached, per sysfs.

    Says nothing about whether the interface currently has an IPv4 address
    — a cable can be plugged in with tethering not yet switched on. Callers
    that need "servable right now" should pair this with
    interface_ipv4_network() (see usb_tether_networks()).

    `sysfs_root` defaults to the real /sys/class/net but is a parameter so
    tests can point it at a fake directory tree instead of this machine's
    actual interfaces.
    """
    names: List[str] = []
    try:
        entries = sorted(sysfs_root.iterdir())
    except OSError:
        return names
    for entry in entries:
        device_link = entry / "device"
        try:
            resolved = device_link.resolve(strict=True)
        except OSError:
            continue  # no "device" link (e.g. lo, a docker bridge)
        if _is_usb_device_path(resolved):
            names.append(entry.name)
    return names


def interface_ipv4_network(ifname: str) -> Optional[ipaddress.IPv4Network]:
    """The IPv4 network currently configured on `ifname`, or None if it has
    no IPv4 address (tethering not yet switched on) or doesn't exist.

    Uses the classic Linux SIOCGIFADDR/SIOCGIFNETMASK ioctls rather than a
    third-party library or shelling out to `ip`. This only ever reports the
    interface's primary address, which is all a USB tethering link has.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            packed_name = struct.pack("256s", ifname.encode("utf-8")[:15])
            addr_bytes = fcntl.ioctl(sock.fileno(), _SIOCGIFADDR, packed_name)
            mask_bytes = fcntl.ioctl(sock.fileno(), _SIOCGIFNETMASK, packed_name)
    except OSError:
        # No IPv4 address assigned (ENODEV/EADDRNOTAVAIL), or interface
        # vanished between enumeration and lookup (cable pulled mid-check).
        return None
    address = socket.inet_ntoa(addr_bytes[20:24])
    netmask = socket.inet_ntoa(mask_bytes[20:24])
    try:
        return ipaddress.ip_network(f"{address}/{netmask}", strict=False)
    except ValueError:
        return None


def usb_tether_networks(
    sysfs_root: Path = SYSFS_NET_ROOT,
    address_lookup: Callable[[str], Optional[ipaddress.IPv4Network]] = interface_ipv4_network,
) -> List[ipaddress.IPv4Network]:
    """IPv4 networks of every USB-attached interface that currently has one.

    Re-walks sysfs and re-reads addresses on every call — deliberately not
    cached — because the cable gets plugged/unplugged and tethering is
    often switched on after the server has already started. An interface
    with no IPv4 address yet (tethering not active) is simply omitted, not
    an error: if none qualify, the caller ends up serving only loopback,
    which is correct.

    `sysfs_root` and `address_lookup` are parameters (with real-system
    defaults) purely so tests can inject fake interface data instead of
    depending on the developer's live network configuration.
    """
    networks: List[ipaddress.IPv4Network] = []
    for name in usb_interface_names(sysfs_root):
        net = address_lookup(name)
        if net is not None:
            networks.append(net)
    return networks


def is_tether_peer(
    addr: Optional[str],
    *,
    sysfs_root: Path = SYSFS_NET_ROOT,
    address_lookup: Callable[[str], Optional[ipaddress.IPv4Network]] = interface_ipv4_network,
) -> bool:
    """True if `addr` is reachable only over the USB cable (or is local).

    Loopback is permitted so local diagnostic tools and test harnesses keep
    working; a loopback peer is already running as the user.

    If TETHRLINK_TETHER_SUBNET is set and valid it takes precedence
    entirely — the operator has told us detection can't handle this
    device, so we trust their answer instead of auto-detecting. Otherwise,
    `addr` is checked against the current IPv4 subnet of every USB-attached
    interface, freshly enumerated on this call (see usb_tether_networks()).
    Wi-Fi, docker bridges and everything else stay refused, because none of
    those interfaces are USB-attached, no matter how "local" their subnet
    looks.
    """
    if not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.is_loopback:
        return True

    override = _override_network()
    if override is not None:
        return ip in override

    for net in usb_tether_networks(sysfs_root, address_lookup):
        if ip in net:
            return True
    return False


def tether_broadcast_addresses(
    sysfs_root: Path = SYSFS_NET_ROOT,
    address_lookup: Callable[[str], Optional[ipaddress.IPv4Network]] = interface_ipv4_network,
) -> List[str]:
    """Where discovery announcements go: the broadcast address of every
    USB-attached interface that currently has an IPv4 address.

    Scoped per-interface rather than to 255.255.255.255, so announcing the
    machine's hostname and port doesn't leak onto Wi-Fi or any other
    network it happens to be attached to. If no USB interface has an IPv4
    address yet (tethering not active), this returns an empty list — there
    is nowhere over the cable to announce to, which is correct.

    If TETHRLINK_TETHER_SUBNET is set and valid, it takes precedence and is
    the only destination — matching is_tether_peer()'s precedence rule.
    """
    override = _override_network()
    if override is not None:
        return [str(override.broadcast_address)]
    return [str(net.broadcast_address) for net in usb_tether_networks(sysfs_root, address_lookup)]


def __getattr__(name: str):
    """Backing for the module-level `TETHER_SUBNET` attribute.

    Kept only for the informational log line in server_core.py's
    accept-path rejection message — it is never used for the actual
    accept/reject decision, which lives entirely in is_tether_peer().
    Implemented as module __getattr__ (PEP 562) rather than a constant
    computed at import time so that even this cosmetic value is
    re-evaluated fresh on every access instead of going stale.
    """
    if name == "TETHER_SUBNET":
        override = _override_network()
        if override is not None:
            return f"{override} (TETHRLINK_TETHER_SUBNET override)"
        names = usb_interface_names()
        if not names:
            return "no USB-tethered interface detected"
        described = []
        for iface in names:
            net = interface_ipv4_network(iface)
            described.append(f"{iface}:{net}" if net is not None else f"{iface}:(no IPv4 yet)")
        return ", ".join(described)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
