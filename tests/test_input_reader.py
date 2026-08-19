"""Unit tests for Task 5's input reader: message dispatch routing, buffer
accumulation/capping, and the reader loop's socket-facing behaviour.

Two tiers, matching the brief's "test what you can isolate" instruction:

* dispatch_input_message() / accumulate_input_buffer() are pure functions
  exercised directly with a fake RemoteInput and crafted byte strings — no
  socket, no D-Bus.
* run_input_reader() is exercised over a real socket.socketpair(), which is
  a genuine, local, kernel-backed socket (so TCP-style fragmentation and
  socket close/EOF behave exactly as they would on a real connection)
  without touching the network or D-Bus. RemoteInput itself is still a
  fake here — its own D-Bus behaviour is covered by test_remote_input.py
  and by manual verification against a live session, documented in the
  task report, not fabricated here.
"""

import socket
import struct
import threading
import time

import pytest

from server.core import server_core
from server.core.input_protocol import (
    InputMessage,
    MSG_POINTER_AXIS,
    MSG_POINTER_BUTTON,
    MSG_POINTER_MOTION,
)
from server.core.metrics import StreamMetrics
from server.core.remote_input import RemoteInput
from server.core.server_core import (
    INPUT_BUFFER_CAP_BYTES,
    accumulate_input_buffer,
    dispatch_input_message,
    run_input_reader,
)


class _FakeRemoteInput:
    """Records calls in place of a live RemoteInput. No D-Bus involved —
    same spirit as test_remote_input.py's _FakeSession, one level up the
    stack: this stands in for the whole RemoteInput object, not just the
    D-Bus session it wraps.

    button() always "dispatches" (returns True) — this fake does not
    model RemoteInput's own held-button state, so it can't reproduce the
    real no-op-for-an-unheld-release behaviour that RemoteInput.button()
    now implements (see remote_input.py). That behaviour, and the drop
    accounting dispatch_input_message derives from it, is covered against
    the REAL RemoteInput class in test_remote_input.py and in this file's
    "unmatched-release flood" tests below, which build a real RemoteInput
    bound to a fake D-Bus session rather than using this fake."""

    def __init__(self):
        self.moves = []
        self.buttons = []
        self.axes = []
        self.release_all_calls = 0

    def move(self, nx, ny):
        self.moves.append((nx, ny))

    def button(self, code, pressed):
        self.buttons.append((code, pressed))
        return True

    def axis(self, dx, dy):
        self.axes.append((dx, dy))

    def release_all(self):
        self.release_all_calls += 1


def _wire_msg(msg_type: int, payload: bytes) -> bytes:
    return bytes([msg_type, len(payload)]) + payload


def _motion(nx: float, ny: float) -> bytes:
    return _wire_msg(MSG_POINTER_MOTION, struct.pack(">ff", nx, ny))


def _button(wire_code: int, pressed: bool) -> bytes:
    return _wire_msg(MSG_POINTER_BUTTON, bytes([wire_code, 1 if pressed else 0]))


def _axis(dx: float, dy: float) -> bytes:
    return _wire_msg(MSG_POINTER_AXIS, struct.pack(">ff", dx, dy))


# ── dispatch_input_message: routing and drop accounting ─────────────────────

def test_dispatch_routes_motion_to_move():
    fake, metrics = _FakeRemoteInput(), StreamMetrics()
    msg = InputMessage(type=MSG_POINTER_MOTION, payload=struct.pack(">ff", 0.5, 0.75))
    dispatch_input_message(msg, fake, metrics)
    assert fake.moves == [(0.5, 0.75)]
    assert metrics.snapshot()["input_events_dropped"] == 0


def test_dispatch_routes_button_to_button_with_evdev_code():
    fake, metrics = _FakeRemoteInput(), StreamMetrics()
    msg = InputMessage(type=MSG_POINTER_BUTTON, payload=bytes([2, 1]))  # wire 2 = BTN_MIDDLE
    dispatch_input_message(msg, fake, metrics)
    assert fake.buttons == [(0x112, True)]
    assert metrics.snapshot()["input_events_dropped"] == 0


def test_dispatch_routes_axis_to_axis():
    fake, metrics = _FakeRemoteInput(), StreamMetrics()
    msg = InputMessage(type=MSG_POINTER_AXIS, payload=struct.pack(">ff", -1.0, 2.5))
    dispatch_input_message(msg, fake, metrics)
    assert fake.axes == [(-1.0, 2.5)]
    assert metrics.snapshot()["input_events_dropped"] == 0


def test_dispatch_drops_unknown_message_type():
    fake, metrics = _FakeRemoteInput(), StreamMetrics()
    dispatch_input_message(InputMessage(type=0x7F, payload=b""), fake, metrics)
    assert (fake.moves, fake.buttons, fake.axes) == ([], [], [])
    assert metrics.snapshot()["input_events_dropped"] == 1


@pytest.mark.parametrize("msg_type, payload", [
    (MSG_POINTER_MOTION, b"\x00\x00\x00"),      # wrong length for >ff (needs 8)
    (MSG_POINTER_BUTTON, bytes([99, 1])),        # unknown wire button code
    (MSG_POINTER_AXIS, b"\x00"),                  # wrong length for >ff
])
def test_dispatch_drops_undecodable_payload(msg_type, payload):
    fake, metrics = _FakeRemoteInput(), StreamMetrics()
    dispatch_input_message(InputMessage(type=msg_type, payload=payload), fake, metrics)
    assert (fake.moves, fake.buttons, fake.axes) == ([], [], [])
    assert metrics.snapshot()["input_events_dropped"] == 1


# ── dispatch_input_message: rate limiter gating, and the release exemption ──

@pytest.mark.parametrize("msg_type, payload", [
    (MSG_POINTER_MOTION, struct.pack(">ff", 0.1, 0.1)),
    (MSG_POINTER_AXIS, struct.pack(">ff", 1.0, 1.0)),
    (MSG_POINTER_BUTTON, bytes([0, 1])),  # a press, not a release
])
def test_dispatch_drops_motion_axis_and_button_press_when_limiter_denies(msg_type, payload):
    fake, metrics = _FakeRemoteInput(), StreamMetrics()
    dispatch_input_message(
        InputMessage(type=msg_type, payload=payload), fake, metrics,
        allow_rate_limited=lambda: False,
    )
    assert (fake.moves, fake.buttons, fake.axes) == ([], [], [])
    assert metrics.snapshot()["input_events_dropped"] == 1


def test_dispatch_never_rate_limits_a_button_release():
    """The single worst failure mode this feature has is a button left
    stuck down. A release must reach RemoteInput even when the flood
    limiter says no — dropping it would leave RemoteInput believing a
    button is still held with no future release ever able to clear it
    until the connection ends."""
    fake, metrics = _FakeRemoteInput(), StreamMetrics()
    dispatch_input_message(
        InputMessage(type=MSG_POINTER_BUTTON, payload=bytes([0, 0])),  # release
        fake, metrics, allow_rate_limited=lambda: False,
    )
    assert fake.buttons == [(0x110, False)]
    assert metrics.snapshot()["input_events_dropped"] == 0


def test_dispatch_counts_a_noop_release_as_dropped():
    """RemoteInput.button() reports a no-op release (one for a code it
    does not believe is held) by returning False — see remote_input.py.
    dispatch_input_message must turn that into an input_events_dropped
    count, exactly like every other kind of drop on this path, even
    though the release was never rate-limited and did reach button()."""
    class _NoopButton(_FakeRemoteInput):
        def button(self, code, pressed):
            self.buttons.append((code, pressed))
            return False  # simulates RemoteInput's no-op-release case

    fake, metrics = _NoopButton(), StreamMetrics()
    dispatch_input_message(
        InputMessage(type=MSG_POINTER_BUTTON, payload=bytes([0, 0])),  # release
        fake, metrics,
    )
    assert fake.buttons == [(0x110, False)]  # still reached button()
    assert metrics.snapshot()["input_events_dropped"] == 1


def test_dispatch_does_not_count_a_dispatched_button_as_dropped():
    """The mirror of the previous test: when button() returns True (a
    press, or a genuine release), nothing is counted as dropped."""
    fake, metrics = _FakeRemoteInput(), StreamMetrics()
    dispatch_input_message(
        InputMessage(type=MSG_POINTER_BUTTON, payload=bytes([0, 1])),  # press
        fake, metrics,
    )
    assert fake.buttons == [(0x110, True)]
    assert metrics.snapshot()["input_events_dropped"] == 0


# ── accumulate_input_buffer: framing, carry-forward, and the cap ────────────

def test_accumulate_returns_one_complete_message_and_empty_remainder():
    chunk = _axis(1.0, 2.0)
    messages, buffer, overflowed = accumulate_input_buffer(b"", chunk)
    assert len(messages) == 1 and messages[0].type == MSG_POINTER_AXIS
    assert buffer == b""
    assert overflowed is False


def test_accumulate_carries_forward_a_message_split_across_two_reads():
    whole = _motion(0.1, 0.2)
    messages1, buffer1, overflowed1 = accumulate_input_buffer(b"", whole[:4])
    assert messages1 == []
    assert overflowed1 is False
    assert buffer1 == whole[:4]

    messages2, buffer2, overflowed2 = accumulate_input_buffer(buffer1, whole[4:])
    assert len(messages2) == 1 and messages2[0].type == MSG_POINTER_MOTION
    assert buffer2 == b""
    assert overflowed2 is False


def test_accumulate_parses_multiple_messages_delivered_in_one_chunk():
    chunk = _button(0, True) + _button(0, False) + _axis(0.0, -3.0)
    messages, buffer, overflowed = accumulate_input_buffer(b"", chunk)
    assert len(messages) == 3
    assert buffer == b""
    assert overflowed is False


def test_accumulate_tolerates_a_legitimately_incomplete_max_length_frame():
    """A header declaring the wire format's maximum payload (255), with
    most of it not yet arrived, is just an ordinary incomplete message —
    real parse_messages() bounds any remainder at 256 bytes — and must be
    carried forward, not treated as an overflow."""
    header_claiming_max_payload = bytes([MSG_POINTER_MOTION, 255])
    partial_payload = b"y" * 200
    messages, buffer, overflowed = accumulate_input_buffer(
        b"", header_claiming_max_payload + partial_payload
    )
    assert messages == []
    assert overflowed is False
    assert buffer == header_claiming_max_payload + partial_payload


def test_accumulate_discards_and_flags_overflow_when_remainder_exceeds_cap(monkeypatch):
    """The reader's own cap must hold no matter what parse_messages()
    hands back. The real wire format's u8 length field already bounds a
    genuine remainder to at most 256 bytes, so this drives the cap
    directly via a monkeypatched parse_messages() rather than relying on
    a state the real framing can organically produce."""
    oversized = b"x" * (INPUT_BUFFER_CAP_BYTES + 1)
    monkeypatch.setattr(server_core, "parse_messages", lambda buf: ([], oversized))
    messages, buffer, overflowed = accumulate_input_buffer(b"", b"irrelevant")
    assert overflowed is True
    assert buffer == b""
    assert messages == []


# ── run_input_reader: real socket, fake RemoteInput ──────────────────────────

@pytest.fixture
def sock_pair():
    server_sock, client_sock = socket.socketpair()
    yield server_sock, client_sock
    for s in (server_sock, client_sock):
        try:
            s.close()
        except OSError:
            pass


def _run_reader(server_sock, fake, metrics, stop_event):
    t = threading.Thread(
        target=run_input_reader,
        args=(server_sock, fake, metrics, stop_event.is_set),
        daemon=True,
    )
    t.start()
    return t


def test_run_input_reader_dispatches_motion_button_axis(sock_pair):
    server_sock, client_sock = sock_pair
    fake, metrics, stop = _FakeRemoteInput(), StreamMetrics(), threading.Event()

    client_sock.sendall(_motion(0.25, 0.5) + _button(0, True) + _axis(1.0, -2.0))
    t = _run_reader(server_sock, fake, metrics, stop)
    time.sleep(0.2)
    client_sock.close()
    t.join(timeout=3.0)

    assert not t.is_alive()
    assert fake.moves == [(0.25, 0.5)]
    assert fake.buttons == [(0x110, True)]  # wire 0 = BTN_LEFT
    assert fake.axes == [(1.0, -2.0)]
    snap = metrics.snapshot()
    assert snap["input_events_received"] == 3
    assert snap["input_events_dropped"] == 0
    assert fake.release_all_calls == 1


def test_run_input_reader_handles_message_split_across_reads(sock_pair):
    server_sock, client_sock = sock_pair
    fake, metrics, stop = _FakeRemoteInput(), StreamMetrics(), threading.Event()
    whole = _axis(3.5, -1.5)

    t = _run_reader(server_sock, fake, metrics, stop)
    client_sock.sendall(whole[:3])
    time.sleep(0.1)
    client_sock.sendall(whole[3:])
    time.sleep(0.2)
    client_sock.close()
    t.join(timeout=3.0)

    assert fake.axes == [(3.5, -1.5)]
    assert metrics.snapshot()["input_events_received"] == 1


def test_run_input_reader_drops_unknown_type_and_discards_truncated_tail_on_close(sock_pair):
    server_sock, client_sock = sock_pair
    fake, metrics, stop = _FakeRemoteInput(), StreamMetrics(), threading.Event()

    unknown = _wire_msg(0x7F, b"\x00\x00")
    truncated = bytes([MSG_POINTER_MOTION, 8]) + b"\x00\x00\x00"  # declares 8, sends 3, never finishes

    t = _run_reader(server_sock, fake, metrics, stop)
    client_sock.sendall(unknown + truncated)
    time.sleep(0.2)
    client_sock.close()
    t.join(timeout=3.0)

    assert not t.is_alive()
    snap = metrics.snapshot()
    assert snap["input_events_received"] == 1  # only the unknown-typed one ever completes
    assert snap["input_events_dropped"] == 1
    assert fake.moves == []
    assert fake.release_all_calls == 1


def test_run_input_reader_rate_limits_a_flood(sock_pair):
    server_sock, client_sock = sock_pair
    fake, metrics, stop = _FakeRemoteInput(), StreamMetrics(), threading.Event()

    n = 200
    blob = b"".join(_button(0, i % 2 == 0) for i in range(n))

    t = _run_reader(server_sock, fake, metrics, stop)
    client_sock.sendall(blob)
    time.sleep(0.3)
    client_sock.close()
    t.join(timeout=3.0)

    snap = metrics.snapshot()
    assert snap["input_events_received"] == n
    assert len(fake.buttons) < n, "a flood must not reach RemoteInput at full volume"
    assert snap["input_events_dropped"] > 0
    assert len(fake.buttons) + snap["input_events_dropped"] == n


def test_run_input_reader_never_rate_limits_a_flood_of_button_releases(sock_pair):
    """Even a large burst of button-release messages — e.g. a buggy or
    malicious client hammering release — must all reach RemoteInput.
    Rate-limiting a release is exactly the failure this design avoids: a
    dropped release could leave a button RemoteInput believes is held
    stuck down for the rest of the connection."""
    server_sock, client_sock = sock_pair
    fake, metrics, stop = _FakeRemoteInput(), StreamMetrics(), threading.Event()

    n = 200
    blob = b"".join(_button(0, False) for _ in range(n))

    t = _run_reader(server_sock, fake, metrics, stop)
    client_sock.sendall(blob)
    time.sleep(0.3)
    client_sock.close()
    t.join(timeout=3.0)

    snap = metrics.snapshot()
    assert snap["input_events_received"] == n
    assert snap["input_events_dropped"] == 0
    assert len(fake.buttons) == n


class _FakeRDSession:
    """Stands in for a live org.gnome.Mutter.RemoteDesktop.Session, same
    spirit as test_remote_input.py's _FakeSession — records the button
    calls that actually made it all the way to "D-Bus"."""

    def __init__(self):
        self.buttons = []

    def NotifyPointerButton(self, code, pressed):
        self.buttons.append((code, pressed))

    def NotifyPointerMotionAbsolute(self, stream_path, x, y):
        pass


def test_run_input_reader_unmatched_release_flood_never_reaches_dbus(sock_pair):
    """End-to-end version of the fix: this uses a REAL RemoteInput (bound
    directly to a fake D-Bus session, bypassing create()/bind_stream())
    instead of the plumbing-only _FakeRemoteInput used elsewhere in this
    file, specifically to prove the actual held-button state check in
    RemoteInput.button() is what bounds a release flood — not the rate
    limiter, which is explicitly NOT allowed to gate releases (see
    dispatch_input_message's docstring: rate-limiting a release would
    reintroduce the stuck-button risk it was designed to avoid).

    A flood of releases for a button that was never pressed must still
    all reach the reader (received == n) and all reach RemoteInput.button()
    (which is where the no-op decision is made), but none of them may
    reach the underlying D-Bus session, and every one must be counted
    under input_events_dropped.
    """
    server_sock, client_sock = sock_pair
    metrics, stop = StreamMetrics(), threading.Event()

    ri = RemoteInput(100, 100, bus=object())
    session = _FakeRDSession()
    ri._session = session
    ri._stream_path = "/fake/stream"

    n = 2000
    blob = b"".join(_button(0, False) for _ in range(n))

    t = _run_reader(server_sock, ri, metrics, stop)
    client_sock.sendall(blob)
    time.sleep(0.5)
    client_sock.close()
    t.join(timeout=5.0)

    assert not t.is_alive()
    snap = metrics.snapshot()
    assert snap["input_events_received"] == n
    assert snap["input_events_dropped"] == n
    assert session.buttons == []  # none of the 2000 ever reached "D-Bus"


def test_run_input_reader_exits_promptly_when_told_to_stop(sock_pair):
    server_sock, client_sock = sock_pair
    fake, metrics, stop = _FakeRemoteInput(), StreamMetrics(), threading.Event()

    t = _run_reader(server_sock, fake, metrics, stop)
    time.sleep(0.1)
    stop.set()
    t.join(timeout=3.0)

    assert not t.is_alive()
    assert fake.release_all_calls == 1


def test_run_input_reader_releases_all_after_abrupt_disconnect_with_button_held(sock_pair):
    """Never leave a button stuck down: even when the peer disconnects
    immediately after a button-down with no matching button-up, the
    reader's exit path must call RemoteInput.release_all()."""
    server_sock, client_sock = sock_pair
    fake, metrics, stop = _FakeRemoteInput(), StreamMetrics(), threading.Event()

    t = _run_reader(server_sock, fake, metrics, stop)
    client_sock.sendall(_button(1, True))  # wire 1 = BTN_RIGHT, never released
    time.sleep(0.2)
    client_sock.close()
    t.join(timeout=3.0)

    assert fake.buttons == [(0x111, True)]
    assert fake.release_all_calls == 1


def test_run_input_reader_never_raises_out_of_the_thread(sock_pair):
    """A remote_input whose methods raise must not escape this function —
    requirement 8's 'never disturb the send loop' would be meaningless if
    an input-side exception could propagate out of this thread's target."""
    server_sock, client_sock = sock_pair
    metrics, stop = StreamMetrics(), threading.Event()

    class _RaisingRemoteInput(_FakeRemoteInput):
        def move(self, nx, ny):
            raise RuntimeError("simulated failure")

    fake = _RaisingRemoteInput()
    t = _run_reader(server_sock, fake, metrics, stop)
    client_sock.sendall(_motion(0.1, 0.1))
    time.sleep(0.2)
    client_sock.close()
    t.join(timeout=3.0)

    assert not t.is_alive()  # thread ran to completion despite the raise
    assert fake.release_all_calls == 1
