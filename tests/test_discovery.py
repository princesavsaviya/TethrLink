"""Tests for server/core/discovery.py's broadcast mechanics.

The bug this pins: discovery used to send a *directed* subnet broadcast
(e.g. 10.125.32.255) as its actual wire destination. That is scoped
correctly (only reaches the tether interface's subnet) but Android's UDP
sockets receive it unreliably — so the app stopped discovering an
already-running server, and only appeared to work when opened after the
server because it also tries a remembered IP.

The fix: bind the sending socket's *source* address to the USB tether
interface's own IP, then send the *limited* broadcast (255.255.255.255).
The bind — not the destination — is what keeps the packet off Wi-Fi;
Android receives 255.255.255.255 far more reliably than a directed
broadcast.

These tests exercise DiscoveryBroadcaster's two broadcast helpers with a
fake socket (no real network I/O, no dependency on this machine's actual
interfaces) so they run identically on any machine.
"""

from server.core import discovery
from server.core.discovery import DiscoveryBroadcaster


class _FakeSocket:
    """Records what a real socket would have been asked to do, and can be
    told to fail bind() for specific addresses — simulating a tether
    interface address that went stale between resolution and use.
    """

    def __init__(self, bind_should_fail_for):
        self._bind_should_fail_for = bind_should_fail_for
        self.bound = None
        self.sent = []
        self.closed = False
        self.broadcast_opt_set = False

    def setsockopt(self, level, optname, value):
        pass

    def bind(self, addr):
        if addr[0] in self._bind_should_fail_for:
            raise OSError(99, "Cannot assign requested address")
        self.bound = addr

    def sendto(self, data, dest):
        self.sent.append((data, dest))

    def close(self):
        self.closed = True


def _install_fake_socket(monkeypatch, bind_should_fail_for=()):
    """Patch discovery.socket.socket to hand out _FakeSocket instances,
    and return the list they get appended to (one entry per socket()
    call, in order) so a test can inspect exactly what was bound/sent."""
    created = []

    def factory(*args, **kwargs):
        sock = _FakeSocket(set(bind_should_fail_for))
        created.append(sock)
        return sock

    monkeypatch.setattr(discovery.socket, "socket", factory)
    return created


def _broadcaster():
    return DiscoveryBroadcaster(port=51137, width=1920, height=1080)


# ── _send_tether_broadcasts(): the fixed path ───────────────────────────────

def test_tether_broadcast_binds_to_the_resolved_source_address(monkeypatch):
    monkeypatch.setattr(
        discovery, "usb_tether_source_addresses", lambda: ["10.125.32.247"]
    )
    created = _install_fake_socket(monkeypatch)

    _broadcaster()._send_tether_broadcasts(b"PACKET")

    assert len(created) == 1
    assert created[0].bound == ("10.125.32.247", 0)
    assert created[0].closed is True


def test_tether_broadcast_destination_is_the_limited_broadcast_not_directed(monkeypatch):
    """The actual bug: this must be 255.255.255.255, never a directed
    subnet broadcast like 10.125.32.255."""
    monkeypatch.setattr(
        discovery, "usb_tether_source_addresses", lambda: ["10.125.32.247"]
    )
    created = _install_fake_socket(monkeypatch)

    _broadcaster()._send_tether_broadcasts(b"PACKET")

    assert created[0].sent == [
        (b"PACKET", ("255.255.255.255", discovery.BROADCAST_PORT))
    ]
    assert "10.125.32.255" not in [dest[0] for _, dest in created[0].sent]


def test_each_usb_interface_gets_its_own_bound_socket(monkeypatch):
    monkeypatch.setattr(
        discovery, "usb_tether_source_addresses",
        lambda: ["10.125.32.247", "192.168.42.5"],
    )
    created = _install_fake_socket(monkeypatch)

    _broadcaster()._send_tether_broadcasts(b"PACKET")

    assert len(created) == 2
    bound = {sock.bound[0] for sock in created}
    assert bound == {"10.125.32.247", "192.168.42.5"}
    for sock in created:
        assert sock.sent == [
            (b"PACKET", ("255.255.255.255", discovery.BROADCAST_PORT))
        ]
        assert sock.closed is True


def test_no_usb_source_addresses_sends_nothing_and_does_not_raise(monkeypatch):
    monkeypatch.setattr(discovery, "usb_tether_source_addresses", lambda: [])
    created = _install_fake_socket(monkeypatch)

    _broadcaster()._send_tether_broadcasts(b"PACKET")  # must not raise

    assert created == []


# ── Stale/absent source address: the ephemeral-interface case ──────────────

def test_a_stale_source_address_fails_to_bind_without_raising(monkeypatch):
    """The tether interface is ephemeral (a new USB port, a fresh tethering
    session, or Android renegotiating DHCP each hand out a new address).
    Binding a socket to an address that has since vanished raises
    `OSError: [Errno 99] Cannot assign requested address` on Linux — this
    must be swallowed and logged, never allowed to kill the broadcast
    loop.
    """
    monkeypatch.setattr(
        discovery, "usb_tether_source_addresses", lambda: ["10.125.32.165"]
    )
    created = _install_fake_socket(
        monkeypatch, bind_should_fail_for=["10.125.32.165"]
    )

    _broadcaster()._send_tether_broadcasts(b"PACKET")  # must not raise

    assert created[0].bound is None
    assert created[0].sent == []  # never reached sendto after the failed bind
    assert created[0].closed is True  # still cleaned up


def test_a_stale_source_address_does_not_block_a_still_live_one(monkeypatch):
    """One interface going stale mid-cycle must not stop discovery from
    still reaching a second, still-live tether interface."""
    monkeypatch.setattr(
        discovery, "usb_tether_source_addresses",
        lambda: ["10.125.32.165", "192.168.42.5"],
    )
    created = _install_fake_socket(
        monkeypatch, bind_should_fail_for=["10.125.32.165"]
    )

    _broadcaster()._send_tether_broadcasts(b"PACKET")

    stale_sock, live_sock = created
    assert stale_sock.sent == []
    assert live_sock.bound == ("192.168.42.5", 0)
    assert live_sock.sent == [
        (b"PACKET", ("255.255.255.255", discovery.BROADCAST_PORT))
    ]


# ── _send_loopback_broadcast(): unchanged behaviour ─────────────────────────

def test_loopback_broadcast_is_still_sent_and_unbound(monkeypatch):
    created = _install_fake_socket(monkeypatch)

    _broadcaster()._send_loopback_broadcast(b"PACKET")

    assert len(created) == 1
    assert created[0].bound is None  # no source binding for loopback
    assert created[0].sent == [
        (b"PACKET", ("127.255.255.255", discovery.BROADCAST_PORT))
    ]
    assert created[0].closed is True
