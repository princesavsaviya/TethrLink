from server.core.link import (
    TETHER_SUBNET,
    is_tether_peer,
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
