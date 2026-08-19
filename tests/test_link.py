import importlib

from server.core import link as link_module
from server.core.link import (
    DEFAULT_TETHER_SUBNET,
    TETHER_SUBNET,
    is_tether_peer,
    resolve_tether_subnet,
    tether_broadcast_address,
)


def test_accepts_addresses_on_the_tethering_subnet():
    assert is_tether_peer("192.168.42.1") is True
    assert is_tether_peer("192.168.42.129") is True
    assert is_tether_peer("192.168.42.254") is True


def test_rejects_a_wifi_lan_address():
    """The case this exists for: a peer on the same Wi-Fi."""
    assert is_tether_peer("10.0.0.193") is False
    assert is_tether_peer("192.168.1.50") is False


def test_rejects_docker_bridge_addresses():
    assert is_tether_peer("172.17.0.1") is False


def test_rejects_a_neighbouring_subnet_that_merely_looks_similar():
    assert is_tether_peer("192.168.43.1") is False
    assert is_tether_peer("192.168.4.2") is False


def test_allows_loopback_so_local_tooling_still_works():
    """Test harnesses and the diagnostic tools connect over loopback."""
    assert is_tether_peer("127.0.0.1") is True


def test_rejects_garbage_rather_than_raising():
    assert is_tether_peer("") is False
    assert is_tether_peer("not-an-address") is False
    assert is_tether_peer(None) is False


def test_broadcast_address_is_scoped_to_the_tether_subnet():
    """Broadcasting to 255.255.255.255 announces the server to the whole LAN."""
    assert tether_broadcast_address() == "192.168.42.255"
    assert tether_broadcast_address() != "255.255.255.255"


def test_subnet_constant_is_the_documented_one():
    assert TETHER_SUBNET.startswith("192.168.42.")


# ── TETHRLINK_TETHER_SUBNET override (pure logic — no env/module reload) ──────

def test_resolve_tether_subnet_accepts_a_valid_override():
    assert resolve_tether_subnet("10.55.0.0/24") == "10.55.0.0/24"


def test_resolve_tether_subnet_falls_back_to_default_on_malformed_value():
    assert resolve_tether_subnet("not-a-subnet") == DEFAULT_TETHER_SUBNET
    assert resolve_tether_subnet("300.1.1.0/24") == DEFAULT_TETHER_SUBNET
    # Host bits set — not a subnet declaration.
    assert resolve_tether_subnet("192.168.42.5/24") == DEFAULT_TETHER_SUBNET


def test_resolve_tether_subnet_default_when_unset():
    assert resolve_tether_subnet(None) == DEFAULT_TETHER_SUBNET
    assert resolve_tether_subnet("") == DEFAULT_TETHER_SUBNET
    assert resolve_tether_subnet("   ") == DEFAULT_TETHER_SUBNET
    # And the module-level constant computed at import time (no env var set
    # in this test run) matches — the default really is still the default.
    assert TETHER_SUBNET == DEFAULT_TETHER_SUBNET


def test_env_override_actually_takes_effect(monkeypatch):
    """The override has to reach the module-level TETHER_SUBNET/_TETHER_NET
    that is_tether_peer() actually uses, computed at import time — not just
    exist as an unused pure function. Reloads the module with the env var
    set, checks behavior changed, then reloads again with it unset so every
    other test in this file keeps seeing the untouched default.
    """
    monkeypatch.setenv("TETHRLINK_TETHER_SUBNET", "10.66.0.0/24")
    try:
        reloaded = importlib.reload(link_module)
        assert reloaded.TETHER_SUBNET == "10.66.0.0/24"
        assert reloaded.is_tether_peer("10.66.0.5") is True
        assert reloaded.is_tether_peer("192.168.42.5") is False
    finally:
        monkeypatch.delenv("TETHRLINK_TETHER_SUBNET", raising=False)
        importlib.reload(link_module)
