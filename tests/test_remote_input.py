"""Unit tests for RemoteInput's own state machines (motion coalescing,
held-button tracking) — the parts of remote_input.py that do not require a
live GNOME/Mutter session to exercise.

No real D-Bus session is ever touched: RemoteInput() is constructed with a
throwaway `bus` object (any truthy value bypasses the real
`dbus.SessionBus()` call in __init__), and a fake session object stands in
for the dbus.Interface a real create() would have produced. The D-Bus calls
themselves (create/start/bind_stream's real behaviour against Mutter) are
intentionally NOT covered here — that requires a live session and is
documented as verified manually in the task report.
"""

import time

import pytest

from server.core.remote_input import RemoteInput


class _FakeBus:
    """Stands in for dbus.SessionBus() purely so RemoteInput.__init__ never
    tries to open a real session bus connection. Never otherwise used."""


class _FakeSession:
    """Records NotifyPointer* calls in place of a live
    org.gnome.Mutter.RemoteDesktop.Session D-Bus interface."""

    def __init__(self):
        self.motions = []  # list of (stream_path, x, y)
        self.buttons = []  # list of (code, pressed)

    def NotifyPointerMotionAbsolute(self, stream_path, x, y):
        self.motions.append((stream_path, x, y))

    def NotifyPointerButton(self, code, pressed):
        self.buttons.append((code, pressed))


class _RaisingButtonSession(_FakeSession):
    """Like _FakeSession, but NotifyPointerButton raises for a chosen set
    of (code, pressed) edges, to exercise exception-safety."""

    def __init__(self, fail_on=()):
        super().__init__()
        self._fail_on = set(fail_on)

    def NotifyPointerButton(self, code, pressed):
        if (code, pressed) in self._fail_on:
            raise RuntimeError("simulated D-Bus failure")
        super().NotifyPointerButton(code, pressed)


def _bound(width=1000, height=500, frame_interval_s=1.0, session=None):
    """A RemoteInput with a fake session already bound directly (bypassing
    create()/bind_stream(), which need a real bus)."""
    ri = RemoteInput(width, height, bus=_FakeBus(), frame_interval_s=frame_interval_s)
    session = session if session is not None else _FakeSession()
    ri._session = session
    ri._stream_path = "/fake/stream"
    return ri, session


def _close_gate(ri):
    """Force move()'s coalescing gate shut, deterministically, regardless
    of how long the test process has been up (time.monotonic()'s epoch is
    unspecified)."""
    ri._last_motion_dispatch = time.monotonic()


def _open_gate(ri):
    """Force move()'s coalescing gate open, deterministically."""
    ri._last_motion_dispatch = time.monotonic() - 10.0


# ── Finding 1: malformed arguments must not escape move() ──────────────────

@pytest.mark.parametrize("nx, ny", [
    (None, 0.5),
    ("nope", 0.5),
    (0.5, (1, 2)),
    (object(), object()),
    (float("nan"), 0.5),
    (0.5, float("inf")),
])
def test_move_does_not_raise_on_malformed_arguments(nx, ny):
    ri, session = _bound()
    _open_gate(ri)
    ri.move(nx, ny)  # must not raise
    assert session.motions == []
    assert ri._pending_position is None


def test_move_dispatches_immediately_when_gate_is_open():
    ri, session = _bound(frame_interval_s=1.0)
    _open_gate(ri)
    ri.move(0.25, 0.5)
    assert session.motions == [("/fake/stream", 250.0, 250.0)]
    assert ri._pending_position is None


# ── Finding 2: coalescing retains and flushes the final position ───────────

def test_gated_move_is_retained_not_dropped():
    ri, session = _bound(frame_interval_s=1000.0)
    _close_gate(ri)
    ri.move(0.5, 0.5)
    assert session.motions == []  # not dispatched while gate is closed
    assert ri._pending_position == (500.0, 250.0)


def test_gated_move_is_flushed_by_a_subsequent_button_event():
    ri, session = _bound(frame_interval_s=1000.0)
    _close_gate(ri)
    ri.move(0.5, 0.5)
    ri.button(0, True)
    assert session.motions == [("/fake/stream", 500.0, 250.0)]
    assert session.buttons == [(0, True)]
    # motion must be flushed BEFORE the button dispatch, per the ordering
    # requirement (a tap must click where the finger actually is)
    assert ri._pending_position is None


def test_most_recent_gated_position_wins():
    ri, session = _bound(frame_interval_s=1000.0)
    _close_gate(ri)
    ri.move(0.1, 0.1)
    ri.move(0.9, 0.9)  # replaces the first pending position, not queued
    ri.button(0, True)
    assert session.motions == [("/fake/stream", 900.0, 450.0)]


def test_stop_flushes_pending_position():
    ri, session = _bound(frame_interval_s=1000.0)
    _close_gate(ri)
    ri.move(0.4, 0.4)
    ri.stop()
    assert session.motions == [("/fake/stream", 400.0, 200.0)]


def test_flush_is_a_noop_when_nothing_is_pending():
    ri, session = _bound(frame_interval_s=1000.0)
    ri.button(0, True)  # no prior move() at all
    assert session.motions == []
    assert session.buttons == [(0, True)]


# ── Finding 3: held-button tracking / release_all ───────────────────────────

def test_release_all_releases_exactly_the_held_buttons():
    ri, session = _bound()
    ri.button(0, True)
    ri.button(1, True)
    ri.button(0, False)  # released normally; should not be released again
    session.buttons.clear()
    ri.release_all()
    assert session.buttons == [(1, False)]
    assert ri._held_buttons == set()


def test_release_all_is_safe_when_nothing_is_held():
    ri, session = _bound()
    ri.release_all()
    assert session.buttons == []
    ri.release_all()  # idempotent
    assert session.buttons == []


def test_release_all_continues_after_one_release_fails():
    session = _RaisingButtonSession(fail_on={(0, False)})
    ri, _ = _bound(session=session)
    ri.button(0, True)
    ri.button(1, True)
    ri.release_all()  # must not raise despite code 0's release failing
    assert (1, False) in session.buttons
    assert ri._held_buttons == set()  # cleared regardless of dispatch outcome


def test_stop_releases_held_buttons():
    ri, session = _bound()
    ri.button(0, True)
    ri.stop()
    assert (0, False) in session.buttons
    assert ri._held_buttons == set()


def test_stop_is_safe_to_call_twice():
    ri, session = _bound()
    ri.button(0, True)
    ri.stop()
    session.buttons.clear()
    ri.stop()  # session is now None; must not raise, must not re-release
    assert session.buttons == []
