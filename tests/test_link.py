"""Tests for server/core/link.py.

The tether link is now detected at runtime (which network interface is
USB-attached, and what IPv4 subnet it currently has) rather than assumed to
be a hardcoded constant. Every test here drives that logic with injected
fake interface data — a fake sysfs tree plus a fake address lookup — so
these tests pass identically regardless of what network hardware the
machine running them happens to have.
"""

import importlib
import ipaddress

import pytest

from server.core import link as link_module
from server.core.link import (
    interface_ipv4_network,
    is_tether_peer,
    resolve_tether_subnet,
    tether_broadcast_addresses,
    usb_interface_names,
    usb_tether_networks,
)


# ── Fake interface data helpers ────────────────────────────────────────────
#
# A real USB-attached interface's `/sys/class/net/<iface>/device` resolves,
# through however many symlinks, to a path with a "usbN" segment in it
# (verified on the actual dev machine: enx7a6c143e84e9's device path is
# .../0000:00:14.0/usb1/1-4/1-4:1.0). A PCI-attached interface's (Wi-Fi,
# wired Ethernet) does not. Interfaces with no "device" link at all (lo,
# docker bridges) are software-only and never USB.

def _make_fake_sysfs(tmp_path, interfaces):
    """Build a fake `/sys/class/net`-shaped tree.

    `interfaces` maps interface name -> "usb" | "pci" | None. "usb" gets a
    device symlink resolving through a usbN path segment; "pci" gets one
    that doesn't; None gets no device link at all (e.g. lo, a docker
    bridge). Returns the fake net root to pass as `sysfs_root`.
    """
    net_root = tmp_path / "net"
    net_root.mkdir()
    devices_root = tmp_path / "devices"
    for name, kind in interfaces.items():
        iface_dir = net_root / name
        iface_dir.mkdir()
        if kind is None:
            continue
        if kind == "usb":
            dev_path = devices_root / "pci0000:00" / "0000:00:14.0" / "usb1" / "1-4" / "1-4:1.0"
        elif kind == "pci":
            dev_path = devices_root / "pci0000:00" / "0000:00:14.3"
        else:
            raise ValueError(f"unknown kind {kind!r}")
        dev_path.mkdir(parents=True, exist_ok=True)
        (iface_dir / "device").symlink_to(dev_path)
    return net_root


def _fake_lookup(mapping):
    """A stand-in for interface_ipv4_network(): iface name -> IPv4Network
    (or None), from a plain dict instead of a real ioctl call.
    """
    def lookup(name):
        return mapping.get(name)
    return lookup


# ── usb_interface_names() / _is_usb_device_path() ──────────────────────────

def test_usb_interface_is_identified_by_its_sysfs_device_path(tmp_path):
    net_root = _make_fake_sysfs(tmp_path, {
        "enx7a6c143e84e9": "usb",
        "wlo1": "pci",
        "lo": None,
    })
    assert usb_interface_names(net_root) == ["enx7a6c143e84e9"]


def test_no_usb_interfaces_gives_an_empty_list(tmp_path):
    net_root = _make_fake_sysfs(tmp_path, {"wlo1": "pci", "docker0": None})
    assert usb_interface_names(net_root) == []


def test_multiple_usb_interfaces_are_all_identified(tmp_path):
    net_root = _make_fake_sysfs(tmp_path, {
        "enx1111": "usb",
        "enx2222": "usb",
        "wlo1": "pci",
    })
    assert set(usb_interface_names(net_root)) == {"enx1111", "enx2222"}


def test_missing_sysfs_root_gives_an_empty_list_rather_than_raising(tmp_path):
    assert usb_interface_names(tmp_path / "does-not-exist") == []


# ── is_tether_peer(): the actual accept/reject decision ────────────────────

def test_a_usb_interfaces_subnet_is_served(tmp_path):
    """The case this whole rewrite exists for: the tablet's real tether
    link, measured on the dev machine, is a USB interface on 10.125.32.0/24
    — nowhere near the old hardcoded 192.168.42.0/24 assumption.
    """
    net_root = _make_fake_sysfs(tmp_path, {"enx7a6c143e84e9": "usb"})
    lookup = _fake_lookup({
        "enx7a6c143e84e9": ipaddress.ip_network("10.125.32.0/24"),
    })
    assert is_tether_peer(
        "10.125.32.165", sysfs_root=net_root, address_lookup=lookup
    ) is True


def test_a_non_usb_interfaces_subnet_is_refused_even_though_equally_local(tmp_path):
    """Wi-Fi's subnet is just as "private" and "local" as the tether
    subnet — the whole point of USB-attachment detection is that locality
    alone must not be enough.
    """
    net_root = _make_fake_sysfs(tmp_path, {"wlo1": "pci"})
    lookup = _fake_lookup({"wlo1": ipaddress.ip_network("10.0.0.0/24")})
    assert is_tether_peer(
        "10.0.0.193", sysfs_root=net_root, address_lookup=lookup
    ) is False


def test_docker_bridge_is_refused(tmp_path):
    net_root = _make_fake_sysfs(tmp_path, {"docker0": None})
    lookup = _fake_lookup({"docker0": ipaddress.ip_network("172.17.0.0/16")})
    assert is_tether_peer(
        "172.17.0.1", sysfs_root=net_root, address_lookup=lookup
    ) is False


def test_loopback_is_always_served_even_with_no_usb_interfaces(tmp_path):
    net_root = _make_fake_sysfs(tmp_path, {"wlo1": "pci"})
    lookup = _fake_lookup({})
    assert is_tether_peer(
        "127.0.0.1", sysfs_root=net_root, address_lookup=lookup
    ) is True


def test_no_usb_interfaces_at_all_means_only_loopback_is_served(tmp_path):
    """Tethering not switched on (or no USB NIC present) — correct
    behavior is refusing everyone except loopback, not failing open.
    """
    net_root = _make_fake_sysfs(tmp_path, {"wlo1": "pci", "docker0": None})
    lookup = _fake_lookup({"wlo1": ipaddress.ip_network("10.0.0.0/24")})
    assert is_tether_peer(
        "10.0.0.193", sysfs_root=net_root, address_lookup=lookup
    ) is False
    assert is_tether_peer(
        "127.0.0.1", sysfs_root=net_root, address_lookup=lookup
    ) is True


def test_a_usb_interface_with_no_ipv4_yet_serves_nothing_from_it(tmp_path):
    """Cable plugged in, tethering not yet flipped on: the interface is
    USB-attached but has no address, so address_lookup returns None for it
    — it must not be treated as an open subnet.
    """
    net_root = _make_fake_sysfs(tmp_path, {"enx7a6c143e84e9": "usb"})
    lookup = _fake_lookup({"enx7a6c143e84e9": None})
    assert usb_tether_networks(net_root, lookup) == []
    assert is_tether_peer(
        "10.125.32.165", sysfs_root=net_root, address_lookup=lookup
    ) is False


def test_multiple_usb_interfaces_are_all_honoured(tmp_path):
    """Two USB-attached interfaces (e.g. two tethered devices) — a peer on
    either subnet is served.
    """
    net_root = _make_fake_sysfs(tmp_path, {
        "enx1111": "usb",
        "enx2222": "usb",
    })
    lookup = _fake_lookup({
        "enx1111": ipaddress.ip_network("10.125.32.0/24"),
        "enx2222": ipaddress.ip_network("192.168.42.0/24"),
    })
    assert is_tether_peer("10.125.32.5", sysfs_root=net_root, address_lookup=lookup) is True
    assert is_tether_peer("192.168.42.5", sysfs_root=net_root, address_lookup=lookup) is True
    assert is_tether_peer("10.0.0.193", sysfs_root=net_root, address_lookup=lookup) is False


def test_rejects_garbage_rather_than_raising(tmp_path):
    net_root = _make_fake_sysfs(tmp_path, {"enx7a6c143e84e9": "usb"})
    lookup = _fake_lookup({"enx7a6c143e84e9": ipaddress.ip_network("10.125.32.0/24")})
    assert is_tether_peer("", sysfs_root=net_root, address_lookup=lookup) is False
    assert is_tether_peer("not-an-address", sysfs_root=net_root, address_lookup=lookup) is False
    assert is_tether_peer(None, sysfs_root=net_root, address_lookup=lookup) is False


# ── tether_broadcast_addresses() ────────────────────────────────────────────

def test_broadcast_addresses_include_each_usb_interfaces_broadcast(tmp_path):
    net_root = _make_fake_sysfs(tmp_path, {
        "enx1111": "usb",
        "enx2222": "usb",
        "wlo1": "pci",
    })
    lookup = _fake_lookup({
        "enx1111": ipaddress.ip_network("10.125.32.0/24"),
        "enx2222": ipaddress.ip_network("192.168.42.0/24"),
        "wlo1": ipaddress.ip_network("10.0.0.0/24"),
    })
    dests = tether_broadcast_addresses(net_root, lookup)
    assert set(dests) == {"10.125.32.255", "192.168.42.255"}
    assert "10.0.0.255" not in dests  # Wi-Fi never gets an announcement


def test_no_usb_interfaces_means_no_broadcast_destinations(tmp_path):
    net_root = _make_fake_sysfs(tmp_path, {"wlo1": "pci"})
    lookup = _fake_lookup({"wlo1": ipaddress.ip_network("10.0.0.0/24")})
    assert tether_broadcast_addresses(net_root, lookup) == []


# ── TETHRLINK_TETHER_SUBNET override: validation (pure logic) ──────────────

def test_resolve_tether_subnet_accepts_a_valid_override():
    assert resolve_tether_subnet("10.55.0.0/24") == "10.55.0.0/24"


def test_resolve_tether_subnet_falls_back_to_none_on_malformed_value():
    assert resolve_tether_subnet("not-a-subnet") is None
    assert resolve_tether_subnet("300.1.1.0/24") is None
    # Host bits set — not a subnet declaration.
    assert resolve_tether_subnet("192.168.42.5/24") is None


def test_resolve_tether_subnet_none_when_unset():
    assert resolve_tether_subnet(None) is None
    assert resolve_tether_subnet("") is None
    assert resolve_tether_subnet("   ") is None


def test_resolve_tether_subnet_rejects_accept_everything(caplog):
    """Critical: 0.0.0.0/0 is syntactically valid but must not be accepted
    — it would make is_tether_peer() true for every IPv4 address, silently
    undoing the tether filter entirely.
    """
    with caplog.at_level("WARNING"):
        result = resolve_tether_subnet("0.0.0.0/0")
    assert result is None
    assert any("0.0.0.0/0" in r.message for r in caplog.records)


def test_resolve_tether_subnet_rejects_a_whole_private_block_as_too_broad():
    """10.0.0.0/8 is private but covers 16.7M addresses — far broader than
    any real tethering subnet, and just as much a fail-open risk as
    0.0.0.0/0 for anyone on a shared 10.x network.
    """
    assert resolve_tether_subnet("10.0.0.0/8") is None


def test_resolve_tether_subnet_rejects_a_public_range():
    assert resolve_tether_subnet("8.8.8.0/24") is None


def test_resolve_tether_subnet_rejects_ipv6(caplog):
    """An IPv6 network parses fine but is never a real tethering subnet;
    accepting it would silently reject every real IPv4 tether peer since
    an IPv4 address is never "in" an IPv6 network.
    """
    with caplog.at_level("WARNING"):
        result = resolve_tether_subnet("::/0")
    assert result is None
    assert any("::/0" in r.message for r in caplog.records)


def test_resolve_tether_subnet_accepts_a_legitimate_alternative_subnet():
    """Some OEMs hand out 192.168.49.0/24 for USB tethering instead of the
    AOSP default — the override has to actually admit real-world values
    like this one, not just reject everything sweeping.
    """
    assert resolve_tether_subnet("192.168.49.0/24") == "192.168.49.0/24"


def test_resolve_tether_subnet_still_rejects_host_bits_set():
    assert resolve_tether_subnet("192.168.42.5/24") is None


# ── TETHRLINK_TETHER_SUBNET override: actually takes precedence ────────────

def test_env_override_takes_precedence_over_usb_detection(tmp_path, monkeypatch):
    """Even with a real, well-formed USB interface detected, the override
    — once set — is the *only* thing consulted.
    """
    net_root = _make_fake_sysfs(tmp_path, {"enx7a6c143e84e9": "usb"})
    lookup = _fake_lookup({
        "enx7a6c143e84e9": ipaddress.ip_network("10.125.32.0/24"),
    })
    monkeypatch.setenv("TETHRLINK_TETHER_SUBNET", "10.66.0.0/24")
    try:
        assert is_tether_peer("10.66.0.5", sysfs_root=net_root, address_lookup=lookup) is True
        # The detected USB subnet is no longer consulted once the override
        # is set — override replaces detection, it doesn't add to it.
        assert is_tether_peer("10.125.32.165", sysfs_root=net_root, address_lookup=lookup) is False
        assert is_tether_peer("127.0.0.1", sysfs_root=net_root, address_lookup=lookup) is True
    finally:
        monkeypatch.delenv("TETHRLINK_TETHER_SUBNET", raising=False)


def test_env_override_also_takes_precedence_for_broadcast(tmp_path, monkeypatch):
    net_root = _make_fake_sysfs(tmp_path, {"enx7a6c143e84e9": "usb"})
    lookup = _fake_lookup({
        "enx7a6c143e84e9": ipaddress.ip_network("10.125.32.0/24"),
    })
    monkeypatch.setenv("TETHRLINK_TETHER_SUBNET", "10.66.0.0/24")
    try:
        assert tether_broadcast_addresses(net_root, lookup) == ["10.66.0.255"]
    finally:
        monkeypatch.delenv("TETHRLINK_TETHER_SUBNET", raising=False)


def test_invalid_override_falls_back_to_real_usb_detection_not_accept_all(tmp_path, monkeypatch):
    """0.0.0.0/0 must not become reachable via the override: an invalid
    override falls back to genuine USB-interface detection, so a Wi-Fi
    peer is still refused and only the real USB subnet is served.
    """
    net_root = _make_fake_sysfs(tmp_path, {
        "enx7a6c143e84e9": "usb",
        "wlo1": "pci",
    })
    lookup = _fake_lookup({
        "enx7a6c143e84e9": ipaddress.ip_network("10.125.32.0/24"),
        "wlo1": ipaddress.ip_network("10.0.0.0/24"),
    })
    monkeypatch.setenv("TETHRLINK_TETHER_SUBNET", "0.0.0.0/0")
    try:
        assert is_tether_peer("10.0.0.193", sysfs_root=net_root, address_lookup=lookup) is False
        assert is_tether_peer("8.8.8.8", sysfs_root=net_root, address_lookup=lookup) is False
        assert is_tether_peer("10.125.32.165", sysfs_root=net_root, address_lookup=lookup) is True
        assert is_tether_peer("127.0.0.1", sysfs_root=net_root, address_lookup=lookup) is True
    finally:
        monkeypatch.delenv("TETHRLINK_TETHER_SUBNET", raising=False)


# ── Backward-compat surface: TETHER_SUBNET (informational only) ────────────

def test_tether_subnet_attribute_is_still_importable_for_logging():
    """server_core.py's accept-path rejection log still does
    `from server.core.link import TETHER_SUBNET, is_tether_peer` — this
    must keep working and never raise, even though there's no single fixed
    subnet to report anymore (it's now a live description, not a
    constant). Not asserting its exact contents: those depend on this
    machine's real, live interfaces.
    """
    importlib.reload(link_module)
    from server.core.link import TETHER_SUBNET  # noqa: F401 (import must not raise)
    assert isinstance(link_module.TETHER_SUBNET, str)
